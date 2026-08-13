from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.database import SessionLocal
from app.models import Run, Schedule, ScheduleOccurrence, Source, Workflow
from app.routers.workflows import active_graph, execute_run
from app.services.run_lifecycle import reconcile_stale_runs
from app.services.run_routing import queue_for_graph
from celery import Celery
from celery.schedules import crontab
from sqlalchemy import select
from workflow_engine import compile_executable_plan

settings = get_settings()
celery_app = Celery("parser_studio", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.task_routes = {
    "parser_studio.execute_run": {"queue": "default"},
    "parser_studio.schedule_tick": {"queue": "maintenance"},
    "parser_studio.reconcile_runs": {"queue": "maintenance"},
}
celery_app.conf.beat_schedule = {
    "parser-studio-schedule-tick": {
        "task": "parser_studio.schedule_tick",
        "schedule": crontab(minute="*"),
    }
    ,
    "parser-studio-reconcile-runs": {
        "task": "parser_studio.reconcile_runs",
        "schedule": crontab(minute="*"),
    },
}
celery_app.conf.timezone = "UTC"


def claim_schedule_occurrence(db, schedule_id: str, planned_at: datetime) -> ScheduleOccurrence | None:
    """Acquire a schedule/minute occurrence without rolling back the tick."""

    occurrence = ScheduleOccurrence(schedule_id=schedule_id, planned_at=planned_at)
    try:
        with db.begin_nested():
            db.add(occurrence)
            db.flush()
    except Exception:
        return None
    return occurrence


@celery_app.task(
    name="parser_studio.execute_run",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def execute_run_task(self, run_id: str) -> None:
    asyncio.run(execute_run(run_id))


@celery_app.task(name="parser_studio.schedule_tick")
def schedule_tick() -> int:
    db = SessionLocal()
    enqueued = 0
    try:
        now_utc = datetime.now(UTC)
        planned_at = now_utc.replace(second=0, microsecond=0)
        schedules = db.scalars(select(Schedule).where(Schedule.enabled.is_(True))).all()
        for schedule in schedules:
            workflow = db.get(Workflow, schedule.workflow_id)
            if not workflow or not workflow.is_active:
                continue
            try:
                local_now = now_utc.astimezone(ZoneInfo(schedule.timezone))
                due = cron_matches(schedule.cron, local_now)
            except Exception:
                continue
            if not due:
                continue
            # A unique occurrence row is the inter-process lock.  ``flush``
            # can race safely with another beat process; the loser does not
            # enqueue any work.
            occurrence = claim_schedule_occurrence(db, schedule.id, planned_at)
            if occurrence is None:
                continue
            workflow_version = workflow.published_version or workflow.version
            settings = workflow.graph_json.get("settings", {})
            if settings.get("run_all_project_sources"):
                targets = list(db.scalars(select(Source).where(Source.project_id == workflow.project_id, Source.enabled.is_(True))).all())
                targets = [source for source in targets if (source.settings or {}).get("access_status", "PUBLIC") == "PUBLIC"]
            else:
                source_id = settings.get("source_id")
                targets = [db.get(Source, source_id)] if source_id else [None]
            for source in targets:
                run = Run(
                    workflow_id=workflow.id,
                    workflow_version=workflow_version,
                    source_id=source.id if source else None,
                    input_json={"schedule_id": schedule.id, "scheduled_at": planned_at.isoformat(), "batch": bool(settings.get("run_all_project_sources"))},
                    deadline_at=now_utc + timedelta(seconds=get_settings().run_default_deadline_seconds),
                    executable_plan_json=compile_executable_plan(
                        active_graph(db, workflow, workflow_version),
                        project_id=workflow.project_id,
                        workflow_id=workflow.id,
                        workflow_version=workflow_version,
                        source_id=source.id if source else None,
                        revision_refs=active_graph(db, workflow, workflow_version).get("settings", {}).get("presetRefs", {}),
                    ).as_dict(),
                )
                db.add(run)
                db.flush()
                queue = queue_for_graph(active_graph(db, workflow, workflow_version), source)
                celery_app.send_task("parser_studio.execute_run", args=[run.id], queue=queue)
                enqueued += 1
            occurrence.run_count = len(targets)
            schedule.last_run_at = planned_at
        db.commit()
        return enqueued
    finally:
        db.close()


@celery_app.task(name="parser_studio.reconcile_runs")
def reconcile_runs() -> int:
    db = SessionLocal()
    try:
        return reconcile_stale_runs(db)
    finally:
        db.close()


def cron_matches(expression: str, moment: datetime) -> bool:
    parts = expression.split()
    if len(parts) != 5:
        raise ValueError("Cron должен содержать 5 полей")
    values = [moment.minute, moment.hour, moment.day, moment.month, (moment.weekday() + 1) % 7]
    limits = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]
    return all(cron_field_matches(field, value, minimum, maximum) for field, value, (minimum, maximum) in zip(parts, values, limits, strict=True))


def cron_field_matches(field: str, value: int, minimum: int, maximum: int) -> bool:
    for token in field.split(","):
        token = token.strip()
        step = 1
        if "/" in token:
            token, step_text = token.split("/", 1)
            step = int(step_text)
        if token == "*":
            start, end = minimum, maximum
        elif "-" in token:
            start, end = (int(part) for part in token.split("-", 1))
        else:
            start = end = int(token)
        if start <= value <= end and (value - start) % step == 0:
            return True
    return False
