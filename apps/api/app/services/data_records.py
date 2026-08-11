from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.models import Record, RecordObservation, RecordVersion
from sqlalchemy import Select, and_, case, func, or_, select
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql.elements import ColumnElement

RecordRow = tuple[Record, RecordVersion | None, RecordObservation | None]
SortDirection = Literal["asc", "desc"]


@dataclass(frozen=True)
class RecordPage:
    rows: list[RecordRow]
    total: int
    has_more: bool


def _apply_time_range(
    statement: Select,
    timestamp: ColumnElement,
    from_: datetime | None,
    to: datetime | None,
) -> Select:
    if from_ is not None:
        statement = statement.where(timestamp.is_not(None), timestamp >= from_)
    if to is not None:
        statement = statement.where(timestamp.is_not(None), timestamp < to)
    return statement


def _apply_cursor(
    statement: Select,
    *,
    null_rank: ColumnElement,
    timestamp: ColumnElement,
    stable_id: ColumnElement,
    cursor_key: tuple[int, datetime, str] | None,
    direction: SortDirection,
) -> Select:
    if cursor_key is None:
        return statement
    cursor_rank, cursor_timestamp, cursor_id = cursor_key
    same_rank = null_rank == cursor_rank
    if cursor_rank == 1:
        same_rank_after = stable_id < cursor_id if direction == "desc" else stable_id > cursor_id
    elif direction == "desc":
        same_rank_after = or_(
            timestamp < cursor_timestamp,
            and_(timestamp == cursor_timestamp, stable_id < cursor_id),
        )
    else:
        same_rank_after = or_(
            timestamp > cursor_timestamp,
            and_(timestamp == cursor_timestamp, stable_id > cursor_id),
        )
    return statement.where(or_(null_rank > cursor_rank, and_(same_rank, same_rank_after)))


def _page(
    db: Session,
    statement: Select,
    *,
    timestamp: ColumnElement,
    stable_id: ColumnElement,
    cursor_key: tuple[int, datetime, str] | None,
    direction: SortDirection,
    limit: int,
    offset: int,
) -> RecordPage:
    null_rank = case((timestamp.is_(None), 1), else_=0)
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    statement = _apply_cursor(
        statement,
        null_rank=null_rank,
        timestamp=timestamp,
        stable_id=stable_id,
        cursor_key=cursor_key,
        direction=direction,
    )
    timestamp_order = timestamp.desc() if direction == "desc" else timestamp.asc()
    id_order = stable_id.desc() if direction == "desc" else stable_id.asc()
    statement = statement.order_by(null_rank.asc(), timestamp_order, id_order)
    if cursor_key is None and offset:
        statement = statement.offset(offset)
    raw_rows = db.execute(statement.limit(limit + 1)).all()
    return RecordPage(
        rows=[(record, version, observation) for record, version, observation in raw_rows[:limit]],
        total=total,
        has_more=len(raw_rows) > limit,
    )


def load_current_page(
    db: Session,
    dataset_id: str,
    *,
    include_pending: bool,
    time_basis: str,
    from_: datetime | None,
    to: datetime | None,
    cursor_key: tuple[int, datetime, str] | None,
    direction: SortDirection,
    limit: int,
    offset: int,
) -> RecordPage:
    """Load one current-record page without materialising the dataset."""
    ranked_observations = select(
        RecordObservation.id.label("observation_id"),
        RecordObservation.record_id.label("record_id"),
        RecordObservation.record_version_id.label("record_version_id"),
        func.row_number()
        .over(
            partition_by=(RecordObservation.record_id, RecordObservation.record_version_id),
            order_by=(RecordObservation.observed_at.desc(), RecordObservation.id.desc()),
        )
        .label("position"),
    ).subquery()
    observation = aliased(RecordObservation)
    statement = (
        select(Record, RecordVersion, observation)
        .join(
            RecordVersion,
            and_(
                RecordVersion.record_id == Record.id,
                RecordVersion.version_number == Record.current_version,
            ),
        )
        .outerjoin(
            ranked_observations,
            and_(
                ranked_observations.c.record_id == Record.id,
                ranked_observations.c.record_version_id == RecordVersion.id,
                ranked_observations.c.position == 1,
            ),
        )
        .outerjoin(observation, observation.id == ranked_observations.c.observation_id)
        .where(Record.dataset_id == dataset_id, Record.status == "ACTIVE")
    )
    if not include_pending:
        statement = statement.where(Record.review_status == "APPROVED")
    observation_timestamp = getattr(observation, time_basis)
    # Only records migrated without an observation fall back to entity creation time.
    timestamp = case(
        (observation.id.is_(None), Record.created_at),
        else_=observation_timestamp,
    )
    statement = _apply_time_range(statement, timestamp, from_, to)
    return _page(
        db,
        statement,
        timestamp=timestamp,
        stable_id=func.coalesce(observation.id, Record.id),
        cursor_key=cursor_key,
        direction=direction,
        limit=limit,
        offset=offset,
    )


def load_observation_page(
    db: Session,
    dataset_id: str,
    *,
    run_id: str | None,
    include_pending: bool,
    time_basis: str,
    from_: datetime | None,
    to: datetime | None,
    cursor_key: tuple[int, datetime, str] | None,
    direction: SortDirection,
    limit: int,
    offset: int,
) -> RecordPage:
    """Load one history/run page with filters and pagination evaluated in SQL."""
    statement = (
        select(Record, RecordVersion, RecordObservation)
        .join(Record, Record.id == RecordObservation.record_id)
        .join(RecordVersion, RecordVersion.id == RecordObservation.record_version_id)
        .where(RecordObservation.dataset_id == dataset_id)
    )
    if run_id:
        statement = statement.where(RecordObservation.run_id == run_id)
    if not include_pending:
        statement = statement.where(RecordVersion.review_status == "APPROVED")
    timestamp = getattr(RecordObservation, time_basis)
    statement = _apply_time_range(statement, timestamp, from_, to)
    return _page(
        db,
        statement,
        timestamp=timestamp,
        stable_id=RecordObservation.id,
        cursor_key=cursor_key,
        direction=direction,
        limit=limit,
        offset=offset,
    )
