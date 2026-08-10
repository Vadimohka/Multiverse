from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.database import SessionLocal
from app.models import Run, Schedule, Source, Workflow
from app.routers.workflows import execute_run
from celery import Celery
from celery.schedules import crontab
from sqlalchemy import select

settings = get_settings()
celery_app = Celery("parser_studio", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.task_routes = {
    "parser_studio.execute_run": {"queue": "default"},
    "parser_studio.schedule_tick": {"queue": "maintenance"},
}
celery_app.conf.beat_schedule = {
    "parser-studio-schedule-tick": {
        "task": "parser_studio.schedule_tick",
        "schedule": crontab(minute="*"),
    }
}
celery_app.conf.timezone = "UTC"


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
            if schedule.last_run_at and schedule.last_run_at.astimezone(UTC).replace(second=0, microsecond=0) >= now_utc.replace(second=0, microsecond=0):
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
                    input_json={"schedule_id": schedule.id, "scheduled_at": now_utc.isoformat(), "batch": bool(settings.get("run_all_project_sources"))},
                )
                db.add(run)
                db.flush()
                queue = queue_for_graph(workflow.graph_json, source)
                celery_app.send_task("parser_studio.execute_run", args=[run.id], queue=queue)
                enqueued += 1
            schedule.last_run_at = now_utc
        db.commit()
        return enqueued
    finally:
        db.close()


def queue_for_graph(graph: dict, source: Source | None = None) -> str:
    types = {node.get("type") or node.get("data", {}).get("type") for node in graph.get("nodes", [])}
    profile = (source.settings or {}).get("profile", {}) if source else {}
    if "browser_open" in types or profile.get("requires_javascript") or (source.fetch_mode or "").upper() == "PLAYWRIGHT" if source else "browser_open" in types:
        return "browser"
    if types & {"parse_document", "download_file"}:
        return "documents"
    if types & {"llm_extract", "llm_classify"}:
        return "llm"
    if types & {"export_file"}:
        return "exports"
    return "default"


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
