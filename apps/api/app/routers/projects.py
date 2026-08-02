from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit
from app.database import get_db
from app.dependencies import get_current_user, require_roles
from app.models import DataSchema, Dataset, Project, Source, User, Workflow
from app.schemas import ProjectCreate, ProjectOut, ProjectUpdate
from app.seed_templates import BANK_DEPOSIT_SCHEMA, BCSE_NEWS_SCHEMA, bcse_news_graph

router = APIRouter(prefix="/projects", tags=["Проекты"])


@router.get("", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> list[Project]:
    return list(db.scalars(select(Project).order_by(Project.created_at.desc())).all())


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> Project:
    if db.scalar(select(Project).where(Project.slug == payload.slug)):
        raise HTTPException(status_code=409, detail="Slug уже используется")
    project = Project(**payload.model_dump(exclude={"template"}), created_by=user.id)
    db.add(project); db.flush()
    if payload.template == "bank_deposits":
        schema = DataSchema(project_id=project.id, name="BankDepositOffer", description="Встроенная схема депозитов", schema_json=BANK_DEPOSIT_SCHEMA, published=True)
        db.add(schema); db.flush(); db.add(Dataset(project_id=project.id, schema_id=schema.id, name="Банковские депозиты", slug=f"{payload.slug}-bank-deposits"))
    if payload.template == "bcse_news":
        schema = DataSchema(project_id=project.id, name="BCSE News", description="Русскоязычные новости Белорусской валютно-фондовой биржи", schema_json=BCSE_NEWS_SCHEMA, published=True)
        db.add(schema); db.flush()
        dataset = Dataset(project_id=project.id, schema_id=schema.id, name="BCSE News", slug=f"{payload.slug}-news")
        db.add(dataset); db.flush()
        source = Source(
            project_id=project.id, name="БВФБ — Новости (рус.)", source_type="WEB_PAGE", entry_url="https://www.bcse.by/press-center/news",
            base_url="https://www.bcse.by", fetch_mode="PLAYWRIGHT", tags=["bcse", "news", "ru"],
            description="Официальный русскоязычный раздел новостей БВФБ; архив берётся через календарный JSON endpoint.",
            settings={"timeout": 60, "headers": {"Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5"}},
        )
        db.add(source); db.flush()
        db.add(Workflow(project_id=project.id, name="BCSE News — Historical Backfill", description="Полная историческая загрузка русскоязычных новостей БВФБ", graph_json=bcse_news_graph(source.id, dataset.id)))
        db.add(Workflow(project_id=project.id, name="BCSE News — Incremental", description="Новые и недавно изменённые новости БВФБ за последние 45 дней", graph_json=bcse_news_graph(source.id, dataset.id, incremental=True)))
    audit(db, user.id, "CREATE", "project", project.id, after=payload.model_dump()); db.commit(); db.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: str, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> Project:
    project = db.get(Project, project_id)
    if not project: raise HTTPException(status_code=404, detail="Проект не найден")
    return project


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(project_id: str, payload: ProjectUpdate, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> Project:
    project = db.get(Project, project_id)
    if not project: raise HTTPException(status_code=404, detail="Проект не найден")
    before = {"name": project.name, "description": project.description, "status": project.status}
    for key, value in payload.model_dump(exclude_none=True).items(): setattr(project, key, value)
    audit(db, user.id, "UPDATE", "project", project.id, before=before, after=payload.model_dump(exclude_none=True)); db.commit(); db.refresh(project)
    return project
