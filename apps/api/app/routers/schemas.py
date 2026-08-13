from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_roles
from app.models import DataSchema, User
from app.schemas import DataSchemaCreate, DataSchemaOut
from app.services.authorization import require_project, require_project_object, scope_to_projects

router = APIRouter(prefix="/schemas", tags=["Схемы данных"])


@router.get("", response_model=list[DataSchemaOut])
def list_schemas(project_id: str | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[DataSchema]:
    if project_id:
        require_project(db, user, project_id)
    stmt = select(DataSchema).order_by(DataSchema.updated_at.desc())
    if project_id: stmt = stmt.where(DataSchema.project_id == project_id)
    return list(db.scalars(scope_to_projects(stmt, DataSchema.project_id, db, user)).all())


@router.post("", response_model=DataSchemaOut, status_code=201)
def create_schema(payload: DataSchemaCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> DataSchema:
    require_project(db, user, payload.project_id)
    schema = DataSchema(**payload.model_dump()); db.add(schema); db.commit(); db.refresh(schema); return schema


@router.post("/{schema_id}/publish", response_model=DataSchemaOut)
def publish(schema_id: str, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> DataSchema:
    schema = require_project_object(db, user, DataSchema, schema_id, label="Schema")
    if not schema: raise HTTPException(status_code=404, detail="Схема не найдена")
    schema.published = True; db.commit(); db.refresh(schema); return schema
