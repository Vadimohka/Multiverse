from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_roles
from app.models import DataSchema, User
from app.schemas import DataSchemaCreate, DataSchemaOut

router = APIRouter(prefix="/schemas", tags=["Схемы данных"])


@router.get("", response_model=list[DataSchemaOut])
def list_schemas(project_id: str | None = None, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> list[DataSchema]:
    stmt = select(DataSchema).order_by(DataSchema.updated_at.desc())
    if project_id: stmt = stmt.where(DataSchema.project_id == project_id)
    return list(db.scalars(stmt).all())


@router.post("", response_model=DataSchemaOut, status_code=201)
def create_schema(payload: DataSchemaCreate, db: Session = Depends(get_db), _: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> DataSchema:
    schema = DataSchema(**payload.model_dump()); db.add(schema); db.commit(); db.refresh(schema); return schema


@router.post("/{schema_id}/publish", response_model=DataSchemaOut)
def publish(schema_id: str, db: Session = Depends(get_db), _: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> DataSchema:
    schema = db.get(DataSchema, schema_id)
    if not schema: raise HTTPException(status_code=404, detail="Схема не найдена")
    schema.published = True; db.commit(); db.refresh(schema); return schema
