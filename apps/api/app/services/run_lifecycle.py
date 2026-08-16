"""Atomic ownership and terminal-state transitions for workflow runs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import get_settings
from app.models import Run
from sqlalchemy import or_, update
from sqlalchemy.orm import Session


def now_utc() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """SQLite returns naive timestamps even for timezone-aware columns."""

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def lease_duration() -> timedelta:
    return timedelta(seconds=max(5, get_settings().run_lease_seconds))


def claim_run(db: Session, run_id: str, *, now: datetime | None = None) -> str | None:
    """Claim a queued run exactly once.

    A crashed worker is reconciled to ``FAILED`` rather than silently being
    taken over by an arbitrary duplicate delivery.  This keeps attempt lineage
    truthful and lets an operator choose an explicit retry.
    """

    now = now or now_utc()
    token = uuid.uuid4().hex
    claimed = db.execute(
        update(Run)
        .where(Run.id == run_id)
        .where(Run.status == "QUEUED")
        .values(
            status="RUNNING",
            lease_token=token,
            lease_expires_at=now + lease_duration(),
            heartbeat_at=now,
            started_at=now,
        )
    )
    if claimed.rowcount != 1:
        db.rollback()
        return None
    db.commit()
    return token


def heartbeat_run(db: Session, run_id: str, lease_token: str, *, now: datetime | None = None) -> bool:
    """Renew only the lease currently held by this worker."""

    now = now or now_utc()
    result = db.execute(
        update(Run)
        .where(Run.id == run_id, Run.status == "RUNNING", Run.lease_token == lease_token)
        .values(heartbeat_at=now, lease_expires_at=now + lease_duration())
    )
    return result.rowcount == 1


def should_stop_run(db: Session, run_id: str, lease_token: str, *, now: datetime | None = None) -> str | None:
    """Return an operator-visible stop reason while also renewing the lease."""

    now = now or now_utc()
    run = db.get(Run, run_id, populate_existing=True)
    if run is None or run.lease_token != lease_token:
        return "LEASE_LOST"
    if run.status in {"CANCEL_REQUESTED", "CANCELLED"}:
        return "CANCELLED"
    if run.status != "RUNNING":
        return "LEASE_LOST"
    if run.deadline_at and as_utc(run.deadline_at) <= now:
        return "DEADLINE_EXCEEDED"
    if not heartbeat_run(db, run_id, lease_token, now=now):
        return "LEASE_LOST"
    db.commit()
    return None


def finalize_owned_run(
    db: Session,
    run_id: str,
    lease_token: str,
    *,
    status: str,
    output_json: dict[str, Any] | None = None,
    error_json: dict[str, Any] | None = None,
    record_counts: dict[str, int] | None = None,
    now: datetime | None = None,
) -> bool:
    """Write a terminal status only if cancellation has not won the race."""

    now = now or now_utc()
    values: dict[str, Any] = {
        "status": status,
        "finished_at": now,
        "lease_token": None,
        "lease_expires_at": None,
        "heartbeat_at": now,
    }
    if output_json is not None:
        values["output_json"] = output_json
    if error_json is not None:
        values["error_json"] = error_json
    if record_counts:
        # Denormalised counters keep the Runs list response light without a
        # per-row ``output_json`` parse on every poll.
        values["records_created"] = int(record_counts.get("created") or 0)
        values["records_updated"] = int(record_counts.get("updated") or 0)
        values["records_unchanged"] = int(record_counts.get("unchanged") or 0)
    result = db.execute(
        update(Run)
        .where(Run.id == run_id, Run.status == "RUNNING", Run.lease_token == lease_token)
        .values(**values)
    )
    return result.rowcount == 1


def mark_cancelled_if_owned(db: Session, run_id: str, lease_token: str, *, now: datetime | None = None) -> bool:
    now = now or now_utc()
    result = db.execute(
        update(Run)
        .where(Run.id == run_id, Run.lease_token == lease_token, Run.status == "CANCEL_REQUESTED")
        .values(
            status="CANCELLED",
            finished_at=now,
            lease_token=None,
            lease_expires_at=None,
            heartbeat_at=now,
        )
    )
    return result.rowcount == 1


def reconcile_stale_runs(db: Session, *, now: datetime | None = None) -> int:
    """Turn abandoned leases and exhausted deadlines into explicit terminal runs.

    A run is only declared abandoned when both the lease expired and the
    heartbeat went quiet for another full lease window.  Under many parallel
    runs a live worker's renewal can be delayed past the lease boundary; the
    double margin keeps ``RUN_LEASE_EXPIRED`` from firing while the worker is
    still breathing.
    """

    now = now or now_utc()
    stale_error = {
        "code": "RUN_LEASE_EXPIRED",
        "message": "Worker heartbeat expired before the run completed",
    }
    heartbeat_grace = lease_duration()
    stale = db.execute(
        update(Run)
        .where(
            Run.status == "RUNNING",
            Run.lease_expires_at.is_not(None),
            Run.lease_expires_at < now,
            or_(
                Run.heartbeat_at.is_(None),
                Run.heartbeat_at < now - heartbeat_grace,
            ),
        )
        .values(
            status="FAILED",
            error_json=stale_error,
            finished_at=now,
            lease_token=None,
            lease_expires_at=None,
        )
    ).rowcount
    timed_out = db.execute(
        update(Run)
        .where(
            Run.status.in_(("QUEUED", "RUNNING")),
            Run.deadline_at.is_not(None),
            Run.deadline_at <= now,
        )
        .values(
            status="TIMED_OUT",
            error_json={"code": "RUN_DEADLINE_EXCEEDED", "message": "Run deadline was exceeded"},
            finished_at=now,
            lease_token=None,
            lease_expires_at=None,
            heartbeat_at=now,
        )
    ).rowcount
    cancelled = db.execute(
        update(Run)
        .where(
            Run.status == "CANCEL_REQUESTED",
            or_(Run.lease_expires_at.is_(None), Run.lease_expires_at < now),
        )
        .values(status="CANCELLED", finished_at=now, lease_token=None, lease_expires_at=None)
    ).rowcount
    db.commit()
    return stale + timed_out + cancelled
