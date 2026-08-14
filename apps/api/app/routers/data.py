import base64
import csv
import io
import json
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import audit
from app.database import get_db
from app.dependencies import (
    DataPrincipal,
    authorize_dataset_read,
    get_current_user,
    get_data_principal,
    require_roles,
    role_names,
)
from app.enums import SUCCESSFUL_RUN_STATUSES
from app.models import (
    DataSchema,
    Dataset,
    DatasetRun,
    DatasetSourceMembership,
    Record,
    RecordObservation,
    RecordVersion,
    ReviewTask,
    Run,
    User,
)
from app.schemas import DataRecordsResponse, DatasetCreate, DatasetOut, DatasetUpdate
from app.services.authorization import (
    has_project_access,
    require_project,
    require_project_object,
    require_same_project,
    scope_to_projects,
)
from app.services.data_records import RecordPage, load_current_page, load_observation_page
from app.services.exporter import export_xlsx

router = APIRouter(tags=["Данные и экспорт"])
INTERNAL_RECORD_FIELDS = {"evidence", "raw_artifact", "status_code", "artifacts"}


def exportable_record(data: dict) -> dict:
    """Artifacts stay available in a run; they are not columns in a business export."""
    return {key: value for key, value in data.items() if key not in INTERNAL_RECORD_FIELDS}


def dataset_out(dataset: Dataset) -> dict:
    return {
        "id": dataset.id,
        "project_id": dataset.project_id,
        "schema_id": dataset.schema_id,
        "name": dataset.name,
        "slug": dataset.slug,
        "natural_key_fields": dataset.natural_key_fields or [],
        "review_policy": dataset.review_policy or {},
        "created_at": dataset.created_at,
        "updated_at": dataset.updated_at,
    }


@router.get("/datasets", response_model=list[DatasetOut])
def list_datasets(
    project_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    if project_id:
        require_project(db, user, project_id)
    stmt = select(Dataset).order_by(Dataset.updated_at.desc())
    if project_id:
        stmt = stmt.where(Dataset.project_id == project_id)
    datasets = db.scalars(scope_to_projects(stmt, Dataset.project_id, db, user)).all()
    return [dataset_out(d) for d in datasets]


@router.post("/datasets", response_model=DatasetOut, status_code=201)
def create_dataset(
    payload: DatasetCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER")),
) -> Dataset:
    require_project(db, user, payload.project_id)
    if db.scalar(select(Dataset).where(Dataset.slug == payload.slug)):
        raise HTTPException(status_code=409, detail="Slug dataset уже используется")
    if payload.schema_id:
        schema = require_project_object(db, user, DataSchema, payload.schema_id, label="Schema")
        require_same_project(payload.project_id, schema)
    if payload.schema_id and not db.get(DataSchema, payload.schema_id):
        raise HTTPException(status_code=404, detail="Схема не найдена")
    dataset = Dataset(**payload.model_dump())
    db.add(dataset)
    db.commit()
    db.refresh(dataset)
    return dataset


@router.patch("/datasets/{dataset_id}", response_model=DatasetOut)
def update_dataset(
    dataset_id: str,
    payload: DatasetUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER")),
) -> Dataset:
    dataset = require_project_object(db, user, Dataset, dataset_id, label="Dataset")
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset не найден")
    changes = payload.model_dump(exclude_unset=True)
    if (
        "slug" in changes
        and changes["slug"] != dataset.slug
        and db.scalar(select(Dataset).where(Dataset.slug == changes["slug"]))
    ):
        raise HTTPException(status_code=409, detail="Slug dataset уже используется")
    if changes.get("schema_id"):
        schema = require_project_object(db, user, DataSchema, changes["schema_id"], label="Schema")
        require_same_project(dataset.project_id, schema)
    if changes.get("schema_id") and not db.get(DataSchema, changes["schema_id"]):
        raise HTTPException(status_code=404, detail="Схема не найдена")
    for key, value in changes.items():
        setattr(dataset, key, value)
    db.commit()
    db.refresh(dataset)
    return dataset


def clear_dataset_records(db: Session, dataset_id: str) -> int:
    """Remove only data owned by a dataset, including its review tasks."""
    records = list(db.scalars(select(Record).where(Record.dataset_id == dataset_id)).all())
    record_ids = [record.id for record in records]
    if record_ids:
        for task in db.scalars(
            select(ReviewTask).where(ReviewTask.record_id.in_(record_ids))
        ).all():
            db.delete(task)
    for record in records:
        db.delete(record)
    return len(records)


@router.delete("/datasets/{dataset_id}/records")
def clear_dataset(
    dataset_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER")),
) -> dict:
    require_project_object(db, user, Dataset, dataset_id, label="Dataset")
    if not db.get(Dataset, dataset_id):
        raise HTTPException(status_code=404, detail="Dataset не найден")
    removed = clear_dataset_records(db, dataset_id)
    audit(db, user.id, "CLEAR", "dataset", dataset_id, after={"removed_records": removed})
    db.commit()
    return {"removed_records": removed}


@router.delete("/datasets/{dataset_id}", status_code=204)
def delete_dataset(
    dataset_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER")),
) -> None:
    dataset = require_project_object(db, user, Dataset, dataset_id, label="Dataset")
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset не найден")
    removed = clear_dataset_records(db, dataset_id)
    audit(
        db,
        user.id,
        "DELETE",
        "dataset",
        dataset_id,
        before={"name": dataset.name, "removed_records": removed},
    )
    db.delete(dataset)
    db.commit()


@router.get("/datasets/{dataset_id}/summary")
def dataset_summary(
    dataset_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    require_project_object(db, user, Dataset, dataset_id, label="Dataset")
    if not db.get(Dataset, dataset_id):
        raise HTTPException(status_code=404, detail="Dataset не найден")
    base = (Record.dataset_id == dataset_id, Record.status == "ACTIVE")
    return {
        "approved": db.scalar(
            select(func.count())
            .select_from(Record)
            .where(*base, Record.review_status == "APPROVED")
        )
        or 0,
        "pending": db.scalar(
            select(func.count()).select_from(Record).where(*base, Record.review_status == "PENDING")
        )
        or 0,
        "rejected": db.scalar(
            select(func.count())
            .select_from(Record)
            .where(Record.dataset_id == dataset_id, Record.status == "REJECTED")
        )
        or 0,
        "pending_initial": db.scalar(
            select(func.count())
            .select_from(Record)
            .where(*base, Record.review_status == "PENDING", Record.current_version == 1)
        )
        or 0,
    }


@router.get("/datasets/{dataset_id}/records", response_model=DataRecordsResponse)
def list_records(
    dataset_id: str,
    view: Literal["current", "latest_run", "run", "history"] = "current",
    run_id: str | None = None,
    time_basis: Literal[
        "source_published_at", "source_modified_at", "fetched_at", "observed_at"
    ] = "observed_at",
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    at: datetime | None = None,
    cursor: str | None = None,
    sort: Literal["asc", "desc"] = "desc",
    limit: int = 100,
    offset: int = 0,
    include_pending: bool = False,
    include: str | None = None,
    db: Session = Depends(get_db),
    principal: DataPrincipal = Depends(get_data_principal),
) -> dict:
    dataset = resolve_dataset(db, dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset не найден")
    authorize_dataset_read(principal, dataset.id)
    if not has_project_access(db, principal.user, dataset.project_id):
        if include_pending and not principal.api_token:
            raise HTTPException(status_code=403, detail="Pending records require a project grant")
        raise HTTPException(status_code=404, detail="Dataset не найден")
    if include_pending and (
        principal.api_token
        or not role_names(principal.user).intersection({"ADMINISTRATOR", "OPERATOR"})
    ):
        raise HTTPException(status_code=403, detail="Pending records require a review role")
    if view not in {"current", "latest_run", "run", "history"}:
        raise HTTPException(
            status_code=422, detail="view must be current, latest_run, run or history"
        )
    if time_basis not in {"source_published_at", "source_modified_at", "fetched_at", "observed_at"}:
        raise HTTPException(status_code=422, detail="Unsupported time_basis")
    requested_at = at
    from_, to = validate_time_range(from_, to, at)
    if sort not in {"asc", "desc"}:
        raise HTTPException(status_code=422, detail="sort must be asc or desc")
    if cursor and offset:
        raise HTTPException(status_code=422, detail="cursor cannot be combined with offset")
    if view == "run" and not run_id:
        raise HTTPException(status_code=422, detail="run_id is required for view=run")
    if view != "run" and run_id:
        raise HTTPException(status_code=422, detail="run_id is only valid for view=run")

    selected_run_id = run_id
    if view != "current":
        if view == "latest_run":
            selected_run_id = db.scalar(
                select(DatasetRun.run_id)
                .join(Run, Run.id == DatasetRun.run_id)
                .where(
                    DatasetRun.dataset_id == dataset.id,
                    Run.status.in_(SUCCESSFUL_RUN_STATUSES),
                )
                .order_by(Run.finished_at.desc(), Run.created_at.desc(), Run.id.desc())
                .limit(1)
            )
        elif view == "run":
            dataset_run = db.scalar(
                select(DatasetRun).where(
                    DatasetRun.dataset_id == dataset.id, DatasetRun.run_id == selected_run_id
                )
            )
            if not db.get(Run, selected_run_id) or not dataset_run:
                raise HTTPException(status_code=404, detail="Run не найден для dataset")
    cursor_context = {
        "dataset_id": dataset.id,
        "view": view,
        "run_id": selected_run_id,
        "time_basis": time_basis,
        "from": iso_utc(from_),
        "to": iso_utc(to),
        "sort": sort,
        "include_pending": include_pending,
    }
    cursor_key = None
    if cursor:
        cursor_payload = decode_cursor(cursor)
        if cursor_payload.get("context") != cursor_context:
            raise HTTPException(status_code=400, detail="Cursor does not match request filters")
        cursor_key = (
            cursor_payload["null_rank"],
            cursor_payload["parsed_timestamp"],
            cursor_payload["id"],
        )
    page_limit = min(max(limit, 1), 1000)
    start = max(offset, 0) if not cursor else 0
    if view == "current":
        record_page = load_current_page(
            db,
            dataset.id,
            include_pending=include_pending,
            time_basis=time_basis,
            from_=from_,
            to=to,
            cursor_key=cursor_key,
            direction=sort,
            limit=page_limit,
            offset=start,
        )
    elif view == "latest_run" and not selected_run_id:
        record_page = RecordPage(rows=[], total=0, has_more=False)
    else:
        record_page = load_observation_page(
            db,
            dataset.id,
            run_id=selected_run_id,
            include_pending=include_pending,
            time_basis=time_basis,
            from_=from_,
            to=to,
            cursor_key=cursor_key,
            direction=sort,
            limit=page_limit,
            offset=start,
        )
    next_cursor = (
        encode_cursor(record_page.rows[-1], time_basis, cursor_context)
        if record_page.rows and record_page.has_more
        else None
    )
    include_evidence = "evidence" in {value.strip() for value in (include or "").split(",")}
    items = [record_api_item(*row, include_evidence=include_evidence) for row in record_page.rows]
    return {
        "items": items,
        "pagination": {"limit": page_limit, "next_cursor": next_cursor},
        "meta": {
            "dataset_id": dataset.id,
            "dataset_slug": dataset.slug,
            "view": view,
            "run_id": selected_run_id,
            "time_basis": time_basis,
            "from": iso_utc(from_),
            "to": iso_utc(to),
            "at": iso_utc(requested_at),
        },
        # Compatibility fields for the existing frontend and API consumers.
        "limit": page_limit,
        "offset": max(offset, 0),
        "total": record_page.total,
    }


def resolve_dataset(db: Session, dataset_ref: str) -> Dataset | None:
    return db.get(Dataset, dataset_ref) or db.scalar(
        select(Dataset).where(Dataset.slug == dataset_ref)
    )


def validate_time_range(
    from_: datetime | None,
    to: datetime | None,
    at: datetime | None,
) -> tuple[datetime | None, datetime | None]:
    values = [value for value in (from_, to, at) if value is not None]
    if any(value.tzinfo is None or value.utcoffset() is None for value in values):
        raise HTTPException(status_code=422, detail="Timestamps must include a timezone")
    if at and (from_ or to):
        raise HTTPException(status_code=422, detail="at cannot be combined with from/to")
    if at:
        start = at.astimezone(UTC).replace(microsecond=0)
        return start, start + timedelta(seconds=1)
    start = from_.astimezone(UTC) if from_ else None
    end = to.astimezone(UTC) if to else None
    if start and end and end <= start:
        raise HTTPException(status_code=422, detail="to must be later than from")
    return start, end


def observation_matches_time(
    observation: RecordObservation | None,
    time_basis: str,
    from_: datetime | None,
    to: datetime | None,
) -> bool:
    if from_ is None and to is None:
        return True
    if observation is None:
        return False
    value = getattr(observation, time_basis)
    if value is None:
        return False
    value = ensure_utc(value)
    return not ((from_ and value < from_) or (to and value >= to))


def observation_sort_key(
    observation: RecordObservation | None,
    time_basis: str,
    record: Record,
) -> tuple[int, datetime, str]:
    if observation is None:
        return 0, ensure_utc(record.created_at), record.id
    value = getattr(observation, time_basis)
    if value is None:
        return 1, datetime.min.replace(tzinfo=UTC), observation.id
    return 0, ensure_utc(value), observation.id


def sort_observation_rows(
    rows: list[tuple[Record, RecordVersion | None, RecordObservation | None]],
    time_basis: str,
    direction: str,
) -> None:
    # The stable second sort keeps NULL timestamps last for both directions.
    rows.sort(
        key=lambda row: observation_sort_key(row[2], time_basis, row[0])[1:],
        reverse=direction == "desc",
    )
    rows.sort(key=lambda row: observation_sort_key(row[2], time_basis, row[0])[0])


def observation_key_is_after(
    key: tuple[int, datetime, str],
    cursor_key: tuple[int, datetime, str],
    direction: str,
) -> bool:
    if key[0] != cursor_key[0]:
        return key[0] > cursor_key[0]
    return key[1:] < cursor_key[1:] if direction == "desc" else key[1:] > cursor_key[1:]


def record_api_item(
    record: Record,
    version: RecordVersion | None,
    observation: RecordObservation | None,
    *,
    include_evidence: bool = False,
) -> dict:
    data = version.data_json if version else record.data_json
    item = {
        "id": record.id,
        "record_id": record.id,
        "record_version_id": version.id if version else None,
        "natural_key": record.natural_key,
        "status": record.status,
        "data": data,
        "timestamps": {
            "source_published_at": iso_utc(
                observation.source_published_at if observation else None
            ),
            "source_modified_at": iso_utc(observation.source_modified_at if observation else None),
            "fetched_at": iso_utc(observation.fetched_at if observation else None),
            "observed_at": iso_utc(observation.observed_at if observation else None),
        },
        "provenance": {
            "run_id": observation.run_id if observation else (version.run_id if version else None),
            "source_id": observation.source_id if observation else None,
            "raw_document_id": observation.raw_document_id if observation else None,
        },
        "confidence": version.confidence if version else record.confidence,
        "review_status": version.review_status if version else record.review_status,
        "updated_at": iso_utc(record.updated_at),
    }
    if include_evidence:
        item["evidence"] = observation.evidence if observation else {}
    return item


@router.get("/datasets/{dataset_id}/coverage")
def dataset_coverage(
    dataset_id: str,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    db: Session = Depends(get_db),
    principal: DataPrincipal = Depends(get_data_principal),
) -> dict:
    """Return source-level coverage without hardcoding a market/source list."""

    dataset = resolve_dataset(db, dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset не найден")
    authorize_dataset_read(principal, dataset.id)
    if not has_project_access(db, principal.user, dataset.project_id):
        raise HTTPException(status_code=404, detail="Dataset не найден")
    from_, to = validate_time_range(from_, to, None)
    memberships = list(db.scalars(
        select(DatasetSourceMembership)
        .where(DatasetSourceMembership.dataset_id == dataset.id)
        .order_by(DatasetSourceMembership.source_key)
    ).all())
    sources: list[dict] = []
    for membership in memberships:
        stmt = (
            select(Run, DatasetRun)
            .join(DatasetRun, DatasetRun.run_id == Run.id)
            .where(DatasetRun.dataset_id == dataset.id)
        )
        if membership.workflow_id:
            stmt = stmt.where(Run.workflow_id == membership.workflow_id)
        elif membership.source_id:
            stmt = stmt.where(Run.source_id == membership.source_id)
        if from_:
            stmt = stmt.where(Run.finished_at >= from_)
        if to:
            stmt = stmt.where(Run.finished_at < to)
        latest = db.execute(stmt.order_by(Run.finished_at.desc(), Run.created_at.desc()).limit(1)).first()
        run, dataset_run = latest if latest else (None, None)
        raw_assessment = (run.output_json.get("result", {}) if run and isinstance(run.output_json, dict) else {})
        assessment = str(raw_assessment.get("assessment_status") or "")
        if run is None:
            status, codes = "MISSING", ["SOURCE_NOT_CHECKED"]
        elif run.status in {"SUCCESS", "WAITING_FOR_REVIEW", "SUCCESS_EMPTY_ALLOWED"} and assessment != "PARTIAL":
            status, codes = "PASS", list(raw_assessment.get("assessment_codes") or [])
        elif run.status in SUCCESSFUL_RUN_STATUSES:
            status, codes = "PARTIAL", list(raw_assessment.get("assessment_codes") or ["PARTIAL_RUN"])
        else:
            status, codes = "FAIL", [str(run.status)]
        sources.append({
            "source_id": membership.source_key,
            "workflow_id": membership.workflow_id,
            "run_id": run.id if run else None,
            "source_preset_id": membership.source_preset_revision_id,
            "status": status,
            "record_count": dataset_run.observed_count if dataset_run else 0,
            "empty_reason": "EMPTY_VALID_WINDOW" if "EMPTY_VALID_WINDOW" in codes else None,
            "assessment_codes": codes,
            "finished_at": iso_utc(run.finished_at) if run else None,
            "observed_at": iso_utc(dataset_run.created_at) if dataset_run else None,
            "required": membership.required,
        })
    required = [item for item in sources if item["required"]]
    status_values = {item["status"] for item in required}
    status = "PASS" if not required or status_values == {"PASS"} else "PARTIAL" if any(item["status"] == "PASS" for item in required) else "FAIL"
    return {
        "dataset_id": dataset.id,
        "dataset_slug": dataset.slug,
        "status": status,
        "expected_sources": len(required),
        "checked_sources": sum(item["status"] != "MISSING" for item in required),
        "successful_sources": sum(item["status"] == "PASS" for item in required),
        "partial_sources": sum(item["status"] == "PARTIAL" for item in required),
        "failed_sources": sum(item["status"] in {"FAIL", "MISSING"} for item in required),
        "window": {"from": iso_utc(from_), "to": iso_utc(to)},
        "sources": sources,
    }


def ensure_utc(value: datetime) -> datetime:
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value).astimezone(UTC)


def iso_utc(value: datetime | None) -> str | None:
    return ensure_utc(value).isoformat().replace("+00:00", "Z") if value else None


def encode_cursor(
    row: tuple[Record, RecordVersion | None, RecordObservation | None],
    time_basis: str,
    context: dict,
) -> str:
    null_rank, timestamp, item_id = observation_sort_key(row[2], time_basis, row[0])
    raw = json.dumps(
        {
            "null_rank": null_rank,
            "timestamp": None if null_rank else iso_utc(timestamp),
            "id": item_id,
            "context": context,
        },
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> dict:
    try:
        padding = "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(cursor + padding))
        if (
            not isinstance(value, dict)
            or not {"null_rank", "timestamp", "id", "context"} <= value.keys()
        ):
            raise ValueError
        if value["null_rank"] not in {0, 1} or not isinstance(value["id"], str) or not value["id"]:
            raise ValueError
        if not isinstance(value["context"], dict):
            raise ValueError
        if value["null_rank"] == 1:
            if value["timestamp"] is not None:
                raise ValueError
            parsed = datetime.min.replace(tzinfo=UTC)
        else:
            if not isinstance(value["timestamp"], str):
                raise ValueError
            parsed = datetime.fromisoformat(value["timestamp"].replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError
            parsed = parsed.astimezone(UTC)
        value["parsed_timestamp"] = parsed
        return value
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor") from exc


@router.post("/datasets/{dataset_id}/accept-baseline")
def accept_baseline(
    dataset_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("ADMINISTRATOR", "OPERATOR")),
) -> dict:
    """Publish only first-observation records after an operator reviewed a sample.

    Later changed versions keep their own Review Queue tasks and are never
    silently accepted by this convenience action.
    """
    require_project_object(db, user, Dataset, dataset_id, label="Dataset")
    if not db.get(Dataset, dataset_id):
        raise HTTPException(status_code=404, detail="Dataset не найден")
    records = list(
        db.scalars(
            select(Record).where(
                Record.dataset_id == dataset_id,
                Record.status == "ACTIVE",
                Record.review_status == "PENDING",
                Record.current_version == 1,
            )
        ).all()
    )
    record_ids = [record.id for record in records]
    for record in records:
        record.review_status = "APPROVED"
    if record_ids:
        versions = db.scalars(
            select(RecordVersion).where(
                RecordVersion.record_id.in_(record_ids), RecordVersion.version_number == 1
            )
        ).all()
        for version in versions:
            version.review_status = "APPROVED"
        tasks = db.scalars(
            select(ReviewTask).where(
                ReviewTask.record_id.in_(record_ids),
                ReviewTask.reason == "NEW_RECORD",
                ReviewTask.status == "PENDING",
            )
        ).all()
        for task in tasks:
            task.status = "APPROVED"
            task.decision_by = user.id
            task.decision_comment = "Базовый срез принят оператором после выборочной проверки"
    audit(
        db,
        user.id,
        "ACCEPT_BASELINE",
        "dataset",
        dataset_id,
        after={"approved_records": len(records)},
    )
    db.commit()
    return {"approved_records": len(records)}


@router.get("/records/{record_id}/history")
def record_history(
    record_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[dict]:
    record = db.get(Record, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    require_project_object(db, user, Dataset, record.dataset_id, label="Dataset")
    versions = db.scalars(
        select(RecordVersion)
        .where(RecordVersion.record_id == record_id)
        .order_by(RecordVersion.version_number.desc())
    ).all()
    return [
        {
            "id": item.id,
            "run_id": item.run_id,
            "version": item.version_number,
            "data": item.data_json,
            "hash": item.data_hash,
            "review_status": item.review_status,
            "confidence": item.confidence,
            "observed_at": item.observed_at,
        }
        for item in versions
    ]


@router.post("/exports")
def export_dataset(
    dataset_id: str,
    format: str = "xlsx",
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER", "OPERATOR", "VIEWER")),
) -> Response:
    dataset = require_project_object(db, user, Dataset, dataset_id, label="Dataset")
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset не найден")
    rows = [
        exportable_record(r.data_json)
        for r in db.scalars(
            select(Record).where(
                Record.dataset_id == dataset_id,
                Record.status == "ACTIVE",
                Record.review_status == "APPROVED",
            )
        ).all()
    ]
    if format == "json":
        return Response(
            json.dumps(rows, ensure_ascii=False, default=str),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{dataset.slug}.json"'},
        )
    if format == "csv":
        buffer = io.StringIO()
        columns = sorted({k for row in rows for k in row})
        writer = csv.DictWriter(buffer, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
        return Response(
            buffer.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{dataset.slug}.csv"'},
        )
    content = export_xlsx(rows, {"dataset": dataset.name, "records": len(rows)})
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{dataset.slug}.xlsx"'},
    )
