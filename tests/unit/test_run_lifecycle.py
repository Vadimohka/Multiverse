from datetime import UTC, datetime, timedelta

import pytest
from app.database import Base, SessionLocal, engine
from app.models import Run, Schedule
from app.services.run_lifecycle import (
    claim_run,
    finalize_owned_run,
    reconcile_stale_runs,
)
from worker import claim_schedule_occurrence
from workflow_engine import WorkflowEngine
from workflow_engine.types import ExecutionContext, RunCancelledError, RunDeadlineExceededError


@pytest.fixture
def db_session():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def make_run(status: str = "QUEUED") -> Run:
    return Run(workflow_id="workflow", workflow_version=1, status=status)


def test_duplicate_worker_delivery_only_claims_run_once(db_session):
    run = make_run()
    db_session.add(run)
    db_session.commit()

    first = claim_run(db_session, run.id)
    second = claim_run(db_session, run.id)

    assert first
    assert second is None


def test_cancel_request_wins_over_late_worker_success(db_session):
    run = make_run()
    db_session.add(run)
    db_session.commit()
    token = claim_run(db_session, run.id)
    assert token
    run = db_session.get(Run, run.id)
    run.status = "CANCEL_REQUESTED"
    db_session.commit()

    assert not finalize_owned_run(db_session, run.id, token, status="SUCCESS", output_json={"unsafe": True})
    db_session.rollback()
    run = db_session.get(Run, run.id)
    assert run.status == "CANCEL_REQUESTED"
    assert run.output_json == {}


def test_stale_worker_and_deadline_are_reconciled(db_session):
    stale = make_run("RUNNING")
    stale.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    expired = make_run()
    expired.deadline_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.add_all([stale, expired])
    db_session.commit()

    assert reconcile_stale_runs(db_session) == 2
    assert db_session.get(Run, stale.id).status == "FAILED"
    assert db_session.get(Run, expired.id).status == "TIMED_OUT"


def test_duplicate_schedule_tick_has_one_durable_occurrence(db_session):
    schedule = Schedule(workflow_id="workflow", name="daily", cron="* * * * *")
    db_session.add(schedule)
    db_session.commit()
    planned_at = datetime.now(UTC).replace(second=0, microsecond=0)

    assert claim_schedule_occurrence(db_session, schedule.id, planned_at)
    assert claim_schedule_occurrence(db_session, schedule.id, planned_at) is None


def test_engine_cancels_slow_operation_when_runtime_requests_stop():
    class SlowNode:
        type = "slow"

        async def execute(self, context, inputs, config):
            import asyncio

            await asyncio.sleep(60)
            return {"records": []}

    graph = {"nodes": [{"id": "slow", "type": "slow", "config": {}}], "edges": []}
    checks = 0

    async def eventually_cancelled() -> str | None:
        nonlocal checks
        checks += 1
        return "CANCELLED" if checks >= 3 else None

    context = ExecutionContext(
        run_id="run",
        project_id="project",
        workflow_version_id="1",
        stop_check=eventually_cancelled,
        heartbeat_interval_seconds=0.01,
    )

    import asyncio

    try:
        asyncio.run(WorkflowEngine({"slow": SlowNode()}).execute(graph, context))
    except RunCancelledError:
        pass
    else:  # pragma: no cover - assertion form keeps asyncio traceback compact
        raise AssertionError("the slow operation was not cancelled")


def test_engine_rejects_expired_deadline_before_node_execution():
    graph = {"nodes": [{"id": "trigger", "type": "manual_trigger", "config": {}}], "edges": []}
    context = ExecutionContext(
        run_id="run",
        project_id="project",
        workflow_version_id="1",
        deadline_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    import asyncio

    try:
        asyncio.run(WorkflowEngine().execute(graph, context))
    except RunDeadlineExceededError:
        pass
    else:  # pragma: no cover
        raise AssertionError("the expired deadline was ignored")
