from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from workflow_engine.redaction import redact_text

from app.audit import audit
from app.database import get_db
from app.dependencies import get_current_user, require_roles
from app.models import (
    AIProviderConfig,
    BrowserProfile,
    DatabaseConnection,
    Schedule,
    Secret,
    User,
    Workflow,
)
from app.schemas import (
    AIProviderCreate,
    BrowserProfileCreate,
    ConnectionCreate,
    ScheduleCreate,
    SecretCreate,
)
from app.security import encrypt_secret, mask_secret
from app.services.authorization import (
    require_project,
    require_project_capability,
    require_project_object,
    scope_to_projects,
)

router = APIRouter(tags=["Подключения и настройки"])


@router.get("/schedules")
def list_schedules(
    project_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    stmt = select(Schedule).join(Workflow, Workflow.id == Schedule.workflow_id).order_by(Schedule.created_at.desc())
    if project_id:
        require_project(db, user, project_id)
        stmt = stmt.where(Workflow.project_id == project_id)
    stmt = scope_to_projects(stmt, Workflow.project_id, db, user)
    return [{"id": x.id, "workflow_id": x.workflow_id, "name": x.name, "cron": x.cron, "timezone": x.timezone, "enabled": x.enabled, "created_at": x.created_at, "last_run_at": x.last_run_at} for x in db.scalars(stmt).all()]


@router.post("/schedules", status_code=201)
def create_schedule(payload: ScheduleCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> dict[str, Any]:
    require_project_object(db, user, Workflow, payload.workflow_id, label="Workflow")
    item = Schedule(**payload.model_dump()); db.add(item); db.flush(); audit(db, user.id, "CREATE", "schedule", item.id, after=payload.model_dump()); db.commit(); db.refresh(item)
    return {"id": item.id, **payload.model_dump()}


@router.delete("/schedules/{schedule_id}")
def delete_schedule(schedule_id: str, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> dict[str, bool]:
    item = db.get(Schedule, schedule_id)
    if not item: raise HTTPException(status_code=404, detail="Расписание не найдено")
    require_project_object(db, user, Workflow, item.workflow_id, label="Workflow")
    db.delete(item); audit(db, user.id, "DELETE", "schedule", schedule_id); db.commit(); return {"deleted": True}


@router.get("/connections")
def list_connections(project_id: str, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> list[dict[str, Any]]:
    require_project(db, user, project_id)
    items = db.scalars(scope_to_projects(select(DatabaseConnection).where(DatabaseConnection.project_id == project_id).order_by(DatabaseConnection.created_at.desc()), DatabaseConnection.project_id, db, user)).all()
    return [{"id": x.id, "project_id": x.project_id, "name": x.name, "engine": x.engine, "host": x.host, "port": x.port, "database": x.database, "username": x.username, "password": "••••••••", "ssl_mode": x.ssl_mode, "schema": x.schema_name, "enabled": x.enabled, "last_tested_at": x.last_tested_at, "last_test_result": x.last_test_result} for x in items]


@router.post("/connections", status_code=201)
def create_connection(payload: ConnectionCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMINISTRATOR"))) -> dict[str, Any]:
    require_project(db, user, payload.project_id)
    item = DatabaseConnection(**payload.model_dump(exclude={"password"}), encrypted_password=encrypt_secret(payload.password) if payload.password else "")
    db.add(item); db.flush(); audit(db, user.id, "CREATE", "database_connection", item.id, after={**payload.model_dump(exclude={"password"}), "password": mask_secret(payload.password)}); db.commit(); db.refresh(item)
    return {"id": item.id, "name": item.name, "engine": item.engine, "enabled": item.enabled}


def connection_url(item: DatabaseConnection) -> str:
    # Password is intentionally not exposed here. Connection testing supports URLs supplied in connection_options for admin-only use.
    if "url" in item.connection_options:
        return str(item.connection_options["url"])
    driver = {"postgresql": "postgresql+psycopg", "mysql": "mysql+pymysql", "mariadb": "mysql+pymysql", "mssql": "mssql+pyodbc", "sqlite": "sqlite"}.get(item.engine, item.engine)
    if driver == "sqlite": return f"sqlite:///{item.database}"
    from app.security import decrypt_secret
    password = decrypt_secret(item.encrypted_password) if item.encrypted_password else ""
    from urllib.parse import quote_plus
    return f"{driver}://{quote_plus(item.username)}:{quote_plus(password)}@{item.host}:{item.port}/{item.database}"


@router.post("/connections/{connection_id}/test")
def test_connection(connection_id: str, project_id: str, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMINISTRATOR"))) -> dict[str, Any]:
    item = require_project_capability(db, user, DatabaseConnection, connection_id, project_id, label="Подключение")
    try:
        engine = create_engine(connection_url(item), pool_pre_ping=True)
        with engine.connect() as conn: conn.execute(text("SELECT 1"))
        result = {"success": True, "message": "Подключение успешно"}
    except Exception as exc:
        from app.security import decrypt_secret

        password = decrypt_secret(item.encrypted_password) if item.encrypted_password else ""
        result = {"success": False, "message": redact_text(str(exc), [password])[:500]}
    item.last_tested_at = datetime.now(UTC); item.last_test_result = result; db.commit(); return result


@router.get("/secrets")
def list_secrets(project_id: str, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMINISTRATOR"))) -> list[dict[str, Any]]:
    require_project(db, user, project_id)
    items = db.scalars(scope_to_projects(select(Secret).where(Secret.project_id == project_id).order_by(Secret.name), Secret.project_id, db, user)).all()
    return [{"id": x.id, "project_id": x.project_id, "name": x.name, "value": x.masked_value, "updated_at": x.updated_at} for x in items]


@router.post("/secrets", status_code=201)
def create_secret(payload: SecretCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMINISTRATOR"))) -> dict[str, Any]:
    require_project(db, user, payload.project_id)
    item = db.scalar(select(Secret).where(Secret.project_id == payload.project_id, Secret.name == payload.name))
    action = "UPDATE" if item else "CREATE"
    if not item: item = Secret(project_id=payload.project_id, name=payload.name, encrypted_value="", masked_value=""); db.add(item)
    item.encrypted_value = encrypt_secret(payload.value); item.masked_value = mask_secret(payload.value); db.flush(); audit(db, user.id, action, "secret", item.id, after={"project_id": payload.project_id, "name": item.name, "value": item.masked_value}); db.commit(); return {"id": item.id, "project_id": item.project_id, "name": item.name, "value": item.masked_value}


@router.get("/browser-profiles")
def list_browser_profiles(project_id: str, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> list[dict[str, Any]]:
    require_project(db, user, project_id)
    items = db.scalars(scope_to_projects(select(BrowserProfile).where(BrowserProfile.project_id == project_id).order_by(BrowserProfile.name), BrowserProfile.project_id, db, user)).all()
    return [{"id": x.id, "project_id": x.project_id, "name": x.name, "browser": x.browser, "viewport": x.viewport, "locale": x.locale, "timezone": x.timezone, "enabled": x.enabled, "last_verified_at": x.last_verified_at} for x in items]


@router.post("/browser-profiles", status_code=201)
def create_browser_profile(payload: BrowserProfileCreate, db: Session = Depends(get_db), _: User = Depends(require_roles("ADMINISTRATOR"))) -> dict[str, Any]:
    require_project(db, _, payload.project_id)
    values = payload.model_dump(exclude={"storage_state"}); values["encrypted_storage_state"] = encrypt_secret(payload.storage_state) if payload.storage_state else ""
    item = BrowserProfile(**values); db.add(item); db.commit(); db.refresh(item); return {"id": item.id, "project_id": item.project_id, "name": item.name, "enabled": item.enabled}


@router.get("/ai-providers")
def list_ai_providers(project_id: str, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> list[dict[str, Any]]:
    require_project(db, user, project_id)
    items = db.scalars(scope_to_projects(select(AIProviderConfig).where(AIProviderConfig.project_id == project_id).order_by(AIProviderConfig.provider_name), AIProviderConfig.project_id, db, user)).all()
    return [{"id": x.id, "project_id": x.project_id, "provider_name": x.provider_name, "provider_type": x.provider_type, "base_url": x.base_url, "api_key": "••••••••", "default_model": x.default_model, "available_models": x.available_models or [x.default_model], "timeout": x.timeout, "max_retries": x.max_retries, "enabled": x.enabled} for x in items]


@router.get("/ai-models")
def list_ai_models(project_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    require_project(db, user, project_id)
    items = db.scalars(scope_to_projects(select(AIProviderConfig).where(AIProviderConfig.project_id == project_id, AIProviderConfig.enabled.is_(True)), AIProviderConfig.project_id, db, user)).all()
    return [{"provider": item.provider_name, "models": list(dict.fromkeys([*(item.available_models or []), item.default_model]))} for item in items]


@router.post("/ai-providers", status_code=201)
def create_ai_provider(payload: AIProviderCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMINISTRATOR"))) -> dict[str, Any]:
    require_project(db, user, payload.project_id)
    values = payload.model_dump(exclude={"api_key"}); values["available_models"] = list(dict.fromkeys([*values["available_models"], values["default_model"]]))
    item = AIProviderConfig(**values, encrypted_api_key=encrypt_secret(payload.api_key) if payload.api_key else "")
    db.add(item); db.flush(); audit(db, user.id, "CREATE", "ai_provider", item.id, after={**payload.model_dump(exclude={"api_key"}), "api_key": mask_secret(payload.api_key)}); db.commit(); return {"id": item.id, "project_id": item.project_id, "provider_name": item.provider_name}
