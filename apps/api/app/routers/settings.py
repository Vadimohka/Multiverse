from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.audit import audit
from app.database import get_db
from app.dependencies import get_current_user, require_roles
from app.models import AIProviderConfig, BrowserProfile, DatabaseConnection, Schedule, Secret, User
from app.schemas import (
    AIProviderCreate,
    BrowserProfileCreate,
    ConnectionCreate,
    ScheduleCreate,
    SecretCreate,
)
from app.security import encrypt_secret, mask_secret

router = APIRouter(tags=["Подключения и настройки"])


@router.get("/schedules")
def list_schedules(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    return [{"id": x.id, "workflow_id": x.workflow_id, "name": x.name, "cron": x.cron, "timezone": x.timezone, "enabled": x.enabled, "created_at": x.created_at, "last_run_at": x.last_run_at} for x in db.scalars(select(Schedule).order_by(Schedule.created_at.desc())).all()]


@router.post("/schedules", status_code=201)
def create_schedule(payload: ScheduleCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> dict[str, Any]:
    item = Schedule(**payload.model_dump()); db.add(item); db.flush(); audit(db, user.id, "CREATE", "schedule", item.id, after=payload.model_dump()); db.commit(); db.refresh(item)
    return {"id": item.id, **payload.model_dump()}


@router.delete("/schedules/{schedule_id}")
def delete_schedule(schedule_id: str, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> dict[str, bool]:
    item = db.get(Schedule, schedule_id)
    if not item: raise HTTPException(status_code=404, detail="Расписание не найдено")
    db.delete(item); audit(db, user.id, "DELETE", "schedule", schedule_id); db.commit(); return {"deleted": True}


@router.get("/connections")
def list_connections(db: Session = Depends(get_db), _: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> list[dict[str, Any]]:
    return [{"id": x.id, "name": x.name, "engine": x.engine, "host": x.host, "port": x.port, "database": x.database, "username": x.username, "password": "••••••••", "ssl_mode": x.ssl_mode, "schema": x.schema_name, "enabled": x.enabled, "last_tested_at": x.last_tested_at, "last_test_result": x.last_test_result} for x in db.scalars(select(DatabaseConnection).order_by(DatabaseConnection.created_at.desc())).all()]


@router.post("/connections", status_code=201)
def create_connection(payload: ConnectionCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMINISTRATOR"))) -> dict[str, Any]:
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
def test_connection(connection_id: str, db: Session = Depends(get_db), _: User = Depends(require_roles("ADMINISTRATOR"))) -> dict[str, Any]:
    item = db.get(DatabaseConnection, connection_id)
    if not item: raise HTTPException(status_code=404, detail="Подключение не найдено")
    try:
        engine = create_engine(connection_url(item), pool_pre_ping=True)
        with engine.connect() as conn: conn.execute(text("SELECT 1"))
        result = {"success": True, "message": "Подключение успешно"}
    except Exception as exc:
        result = {"success": False, "message": str(exc)[:500]}
    item.last_tested_at = datetime.now(UTC); item.last_test_result = result; db.commit(); return result


@router.get("/secrets")
def list_secrets(db: Session = Depends(get_db), _: User = Depends(require_roles("ADMINISTRATOR"))) -> list[dict[str, Any]]:
    return [{"id": x.id, "name": x.name, "value": x.masked_value, "updated_at": x.updated_at} for x in db.scalars(select(Secret).order_by(Secret.name)).all()]


@router.post("/secrets", status_code=201)
def create_secret(payload: SecretCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMINISTRATOR"))) -> dict[str, Any]:
    item = db.scalar(select(Secret).where(Secret.name == payload.name))
    action = "UPDATE" if item else "CREATE"
    if not item: item = Secret(name=payload.name, encrypted_value="", masked_value=""); db.add(item)
    item.encrypted_value = encrypt_secret(payload.value); item.masked_value = mask_secret(payload.value); db.flush(); audit(db, user.id, action, "secret", item.id, after={"name": item.name, "value": item.masked_value}); db.commit(); return {"id": item.id, "name": item.name, "value": item.masked_value}


@router.get("/browser-profiles")
def list_browser_profiles(db: Session = Depends(get_db), _: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> list[dict[str, Any]]:
    return [{"id": x.id, "name": x.name, "browser": x.browser, "viewport": x.viewport, "locale": x.locale, "timezone": x.timezone, "enabled": x.enabled, "last_verified_at": x.last_verified_at} for x in db.scalars(select(BrowserProfile).order_by(BrowserProfile.name)).all()]


@router.post("/browser-profiles", status_code=201)
def create_browser_profile(payload: BrowserProfileCreate, db: Session = Depends(get_db), _: User = Depends(require_roles("ADMINISTRATOR"))) -> dict[str, Any]:
    values = payload.model_dump(exclude={"storage_state"}); values["encrypted_storage_state"] = encrypt_secret(payload.storage_state) if payload.storage_state else ""
    item = BrowserProfile(**values); db.add(item); db.commit(); db.refresh(item); return {"id": item.id, "name": item.name, "enabled": item.enabled}


@router.get("/ai-providers")
def list_ai_providers(db: Session = Depends(get_db), _: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> list[dict[str, Any]]:
    return [{"id": x.id, "provider_name": x.provider_name, "provider_type": x.provider_type, "base_url": x.base_url, "api_key": "••••••••", "default_model": x.default_model, "available_models": x.available_models or [x.default_model], "timeout": x.timeout, "max_retries": x.max_retries, "enabled": x.enabled} for x in db.scalars(select(AIProviderConfig).order_by(AIProviderConfig.provider_name)).all()]


@router.get("/ai-models")
def list_ai_models(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    return [{"provider": item.provider_name, "models": list(dict.fromkeys([*(item.available_models or []), item.default_model]))} for item in db.scalars(select(AIProviderConfig).where(AIProviderConfig.enabled.is_(True))).all()]


@router.post("/ai-providers", status_code=201)
def create_ai_provider(payload: AIProviderCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMINISTRATOR"))) -> dict[str, Any]:
    values = payload.model_dump(exclude={"api_key"}); values["available_models"] = list(dict.fromkeys([*values["available_models"], values["default_model"]]))
    item = AIProviderConfig(**values, encrypted_api_key=encrypt_secret(payload.api_key) if payload.api_key else "")
    db.add(item); db.flush(); audit(db, user.id, "CREATE", "ai_provider", item.id, after={**payload.model_dump(exclude={"api_key"}), "api_key": mask_secret(payload.api_key)}); db.commit(); return {"id": item.id, "provider_name": item.provider_name}
