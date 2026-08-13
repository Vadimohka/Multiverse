from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit
from app.database import get_db
from app.dependencies import get_current_user, require_roles
from app.models import DataSchema, Dataset, Project, ProjectMember, User
from app.schemas import (
    ProjectCreate,
    ProjectMemberCreate,
    ProjectMemberOut,
    ProjectOut,
    ProjectUpdate,
)
from app.seed_templates import BANK_DEPOSIT_SCHEMA
from app.services.authorization import (
    PROJECT_ROLES,
    require_project,
    require_project_owner,
    scope_to_projects,
)

router = APIRouter(prefix="/projects", tags=["Проекты"])


@router.get("", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[Project]:
    return list(db.scalars(scope_to_projects(select(Project).order_by(Project.created_at.desc()), Project.id, db, user)).all())


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> Project:
    if db.scalar(select(Project).where(Project.slug == payload.slug)):
        raise HTTPException(status_code=409, detail="Slug уже используется")
    project = Project(**payload.model_dump(exclude={"template"}), created_by=user.id)
    db.add(project); db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role="OWNER"))
    if payload.template == "bank_deposits":
        schema = DataSchema(project_id=project.id, name="BankDepositOffer", description="Встроенная схема депозитов", schema_json=BANK_DEPOSIT_SCHEMA, published=True)
        db.add(schema); db.flush(); db.add(Dataset(project_id=project.id, schema_id=schema.id, name="Банковские депозиты", slug=f"{payload.slug}-bank-deposits"))
    audit(db, user.id, "CREATE", "project", project.id, after=payload.model_dump()); db.commit(); db.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Project:
    return require_project(db, user, project_id)


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(project_id: str, payload: ProjectUpdate, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> Project:
    project = require_project_owner(db, user, project_id)
    before = {"name": project.name, "description": project.description, "status": project.status}
    for key, value in payload.model_dump(exclude_none=True).items(): setattr(project, key, value)
    audit(db, user.id, "UPDATE", "project", project.id, before=before, after=payload.model_dump(exclude_none=True)); db.commit(); db.refresh(project)
    return project


@router.get("/{project_id}/members", response_model=list[ProjectMemberOut])
def list_project_members(project_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[ProjectMember]:
    require_project(db, user, project_id)
    return list(db.scalars(select(ProjectMember).where(ProjectMember.project_id == project_id).order_by(ProjectMember.created_at)).all())


@router.post("/{project_id}/members", response_model=ProjectMemberOut, status_code=201)
def add_project_member(
    project_id: str,
    payload: ProjectMemberCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER")),
) -> ProjectMember:
    require_project_owner(db, user, project_id)
    if payload.role not in PROJECT_ROLES:
        raise HTTPException(status_code=422, detail="Недопустимая роль проекта")
    if not db.get(User, payload.user_id):
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    member = db.scalar(select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == payload.user_id))
    if member:
        member.role = payload.role
    else:
        member = ProjectMember(project_id=project_id, user_id=payload.user_id, role=payload.role)
        db.add(member)
    audit(db, user.id, "GRANT", "project_member", member.id, after={"project_id": project_id, "user_id": payload.user_id, "role": payload.role})
    db.commit(); db.refresh(member)
    return member
