import csv
import io
import json

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_roles
from app.models import DataSchema, Dataset, Record, RecordVersion, User
from app.schemas import DatasetCreate, DatasetOut, DatasetUpdate
from app.services.exporter import export_xlsx

router = APIRouter(tags=["Данные и экспорт"])
INTERNAL_RECORD_FIELDS = {"evidence", "raw_artifact", "status_code", "artifacts"}


def exportable_record(data: dict) -> dict:
    """Artifacts stay available in a run; they are not columns in a business export."""
    return {key: value for key, value in data.items() if key not in INTERNAL_RECORD_FIELDS}


def dataset_out(dataset: Dataset) -> dict:
    return {"id": dataset.id, "project_id": dataset.project_id, "schema_id": dataset.schema_id, "name": dataset.name, "slug": dataset.slug,
            "natural_key_fields": dataset.natural_key_fields or [], "review_policy": dataset.review_policy or {}, "created_at": dataset.created_at, "updated_at": dataset.updated_at}


@router.get("/datasets", response_model=list[DatasetOut])
def list_datasets(project_id: str | None = None, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> list[dict]:
    stmt = select(Dataset).order_by(Dataset.updated_at.desc())
    if project_id: stmt = stmt.where(Dataset.project_id == project_id)
    datasets = db.scalars(stmt).all()
    return [dataset_out(d) for d in datasets]


@router.post("/datasets", response_model=DatasetOut, status_code=201)
def create_dataset(payload: DatasetCreate, db: Session = Depends(get_db), _: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> Dataset:
    if db.scalar(select(Dataset).where(Dataset.slug == payload.slug)):
        raise HTTPException(status_code=409, detail="Slug dataset уже используется")
    if payload.schema_id and not db.get(DataSchema, payload.schema_id):
        raise HTTPException(status_code=404, detail="Схема не найдена")
    dataset = Dataset(**payload.model_dump())
    db.add(dataset); db.commit(); db.refresh(dataset)
    return dataset


@router.patch("/datasets/{dataset_id}", response_model=DatasetOut)
def update_dataset(dataset_id: str, payload: DatasetUpdate, db: Session = Depends(get_db), _: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> Dataset:
    dataset = db.get(Dataset, dataset_id)
    if not dataset: raise HTTPException(status_code=404, detail="Dataset не найден")
    changes = payload.model_dump(exclude_unset=True)
    if "slug" in changes and changes["slug"] != dataset.slug and db.scalar(select(Dataset).where(Dataset.slug == changes["slug"])):
        raise HTTPException(status_code=409, detail="Slug dataset уже используется")
    if changes.get("schema_id") and not db.get(DataSchema, changes["schema_id"]):
        raise HTTPException(status_code=404, detail="Схема не найдена")
    for key, value in changes.items(): setattr(dataset, key, value)
    db.commit(); db.refresh(dataset)
    return dataset


@router.get("/datasets/{dataset_id}/records")
def list_records(dataset_id: str, limit: int = 100, offset: int = 0, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict:
    if not db.get(Dataset, dataset_id): raise HTTPException(status_code=404, detail="Dataset не найден")
    # The data catalogue is the confirmed-data view. Pending/rejected versions
    # are available to operators through the dedicated review queue instead.
    filters = (Record.dataset_id == dataset_id, Record.status == "ACTIVE", Record.review_status == "APPROVED")
    total = db.scalar(select(func.count()).select_from(Record).where(*filters)) or 0
    records = db.scalars(
        select(Record).where(*filters)
        .order_by(Record.updated_at.desc())
        .offset(offset)
        .limit(min(limit, 1000))
    ).all()
    return {"items": [{"id": r.id, "natural_key": r.natural_key, "status": r.status, "data": r.data_json, "confidence": r.confidence, "review_status": r.review_status, "updated_at": r.updated_at} for r in records], "limit": limit, "offset": offset, "total": total}


@router.get("/records/{record_id}/history")
def record_history(record_id: str, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> list[dict]:
    if not db.get(Record, record_id): raise HTTPException(status_code=404, detail="Запись не найдена")
    versions = db.scalars(select(RecordVersion).where(RecordVersion.record_id == record_id).order_by(RecordVersion.version_number.desc())).all()
    return [{"id": item.id, "run_id": item.run_id, "version": item.version_number, "data": item.data_json, "hash": item.data_hash, "review_status": item.review_status, "confidence": item.confidence, "observed_at": item.observed_at} for item in versions]


@router.post("/exports")
def export_dataset(dataset_id: str, format: str = "xlsx", db: Session = Depends(get_db), _: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER", "OPERATOR", "VIEWER"))) -> Response:
    dataset = db.get(Dataset, dataset_id)
    if not dataset: raise HTTPException(status_code=404, detail="Dataset не найден")
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
    if format == "json": return Response(json.dumps(rows, ensure_ascii=False, default=str), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="{dataset.slug}.json"'})
    if format == "csv":
        buffer = io.StringIO(); columns = sorted({k for row in rows for k in row}); writer = csv.DictWriter(buffer, fieldnames=columns); writer.writeheader(); writer.writerows(rows)
        return Response(buffer.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{dataset.slug}.csv"'})
    content = export_xlsx(rows, {"dataset": dataset.name, "records": len(rows)})
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="{dataset.slug}.xlsx"'})
