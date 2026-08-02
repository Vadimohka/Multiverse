from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit
from app.database import get_db
from app.dependencies import get_current_user, require_roles
from app.models import Source, SourceProfile, User, Workflow
from app.schemas import EndpointUseRequest, ProfileRequest, SourceCreate, SourceOut
from app.services.selector_picker import build_selector_snapshot
from app.services.source_profiler import profile_url

router = APIRouter(prefix="/sources", tags=["Источники"])


@router.get("", response_model=list[SourceOut])
def list_sources(project_id: str | None = None, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> list[Source]:
    stmt = select(Source).order_by(Source.created_at.desc())
    if project_id: stmt = stmt.where(Source.project_id == project_id)
    return list(db.scalars(stmt).all())


@router.post("", response_model=SourceOut, status_code=201)
def create_source(payload: SourceCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> Source:
    source = Source(**payload.model_dump()); db.add(source); db.flush(); audit(db, user.id, "CREATE", "source", source.id, after=payload.model_dump()); db.commit(); db.refresh(source); return source


@router.post("/endpoint/use")
def use_endpoint(payload: EndpointUseRequest, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> dict:
    candidate = payload.candidate or {}
    url = str(candidate.get("url") or "")
    if not url: raise HTTPException(status_code=422, detail="Endpoint URL не задан")
    method = str(candidate.get("method") or "GET").upper()
    headers = candidate.get("headers") if isinstance(candidate.get("headers"), dict) else {}
    query_params = candidate.get("query_params") if isinstance(candidate.get("query_params"), dict) else {}
    request_body = candidate.get("request_body") or ""
    try:
        json_body = __import__("json").loads(request_body) if request_body else {}
    except Exception:
        json_body = {}
    http_config = {"url": url, "method": method, "headers": headers, "query_params": query_params, "json_body": json_body, "timeout": 30}
    if payload.mode == "source":
        if not payload.project_id: raise HTTPException(status_code=422, detail="Для источника выберите проект")
        source = Source(project_id=payload.project_id, name=payload.name or urlsplit(url).netloc or "JSON endpoint", source_type="JSON_API", entry_url=url, base_url=f"{urlsplit(url).scheme}://{urlsplit(url).netloc}", fetch_mode="XHR_JSON", settings={"http_request": http_config})
        db.add(source); db.commit(); db.refresh(source)
        return {"mode": "source", "source": SourceOut.model_validate(source).model_dump()}
    if payload.mode == "workflow_node":
        if not payload.workflow_id: raise HTTPException(status_code=422, detail="Выберите workflow")
        workflow = db.get(Workflow, payload.workflow_id)
        if not workflow: raise HTTPException(status_code=404, detail="Workflow не найден")
        return {"mode": "workflow_node", "node": {"type": "http_request", "config": http_config}}
    raise HTTPException(status_code=422, detail="Неизвестный способ использования endpoint")


@router.post("/profile")
async def profile(payload: ProfileRequest, db: Session = Depends(get_db), _: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> dict:
    try: result = await profile_url(payload.url, payload.timeout)
    except Exception as exc: raise HTTPException(status_code=422, detail=f"Не удалось профилировать URL: {exc}") from exc
    db.add(SourceProfile(source_id=payload.source_id, url=payload.url, result_json=result)); db.commit(); return result


@router.post("/{source_id}/test")
async def test_source(source_id: str, db: Session = Depends(get_db), _: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER", "OPERATOR"))) -> dict:
    source = db.get(Source, source_id)
    if not source: raise HTTPException(status_code=404, detail="Источник не найден")
    return await profile_url(source.entry_url)


class SelectorSnapshotRequest(BaseModel):
    url: str
    timeout: float = 30


@router.post("/selector-snapshot")
async def selector_snapshot(payload: SelectorSnapshotRequest, _: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> dict:
    try: return await build_selector_snapshot(payload.url, payload.timeout)
    except Exception as exc: raise HTTPException(status_code=422, detail=f"Не удалось создать selector snapshot: {exc}") from exc
