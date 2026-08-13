from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_roles
from app.models import AIProviderConfig, Prompt, User
from app.schemas import PromptCreate, PromptOut
from app.security import decrypt_secret
from app.services.authorization import require_project, require_project_object, scope_to_projects
from app.services.llm import MockProvider, OpenAICompatibleProvider, get_provider

router = APIRouter(prefix="/prompts", tags=["Промпты"])


@router.get("", response_model=list[PromptOut])
def list_prompts(
    project_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[Prompt]:
    if project_id:
        require_project(db, user, project_id)
    stmt = select(Prompt).order_by(Prompt.updated_at.desc())
    if project_id:
        stmt = stmt.where(Prompt.project_id == project_id)
    return list(db.scalars(scope_to_projects(stmt, Prompt.project_id, db, user)).all())


@router.post("", response_model=PromptOut, status_code=201)
def create_prompt(
    payload: PromptCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER")),
) -> Prompt:
    require_project(db, user, payload.project_id)
    if payload.provider != "mock":
        provider = db.scalar(select(AIProviderConfig).where(AIProviderConfig.project_id == payload.project_id, AIProviderConfig.provider_name == payload.provider, AIProviderConfig.enabled.is_(True)))
        if not provider:
            raise HTTPException(status_code=422, detail="Выберите включённого AI provider")
        if payload.model not in (set(provider.available_models or []) | {provider.default_model}):
            raise HTTPException(status_code=422, detail="Модель не входит в список, разрешённый администратором")
    prompt = Prompt(**payload.model_dump())
    db.add(prompt)
    db.commit()
    db.refresh(prompt)
    return prompt


class PromptTestRequest(BaseModel):
    content: Any
    variables: dict[str, Any] = {}


@router.post("/{prompt_id}/test")
async def test_prompt(
    prompt_id: str,
    payload: PromptTestRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER")),
) -> dict[str, Any]:
    prompt = require_project_object(db, user, Prompt, prompt_id, label="Prompt")
    if not prompt:
        raise HTTPException(status_code=404, detail="Промпт не найден")
    content = payload.content if isinstance(payload.content, str) else __import__("json").dumps(payload.content, ensure_ascii=False)
    user_prompt = prompt.user_prompt.replace("{{content}}", content)
    user_prompt = user_prompt.replace("{{schema}}", __import__("json").dumps(prompt.response_schema, ensure_ascii=False))
    for key, value in payload.variables.items():
        user_prompt = user_prompt.replace("{{" + key + "}}", str(value))
    provider_config = db.scalar(select(AIProviderConfig).where(AIProviderConfig.project_id == prompt.project_id, AIProviderConfig.provider_name == prompt.provider, AIProviderConfig.enabled.is_(True)))
    if prompt.provider == "mock":
        provider = MockProvider()
    elif provider_config:
        provider = OpenAICompatibleProvider(provider_config.base_url, decrypt_secret(provider_config.encrypted_api_key) if provider_config.encrypted_api_key else "")
    else:
        provider = get_provider(prompt.provider)
    return await provider.complete(
        prompt.model,
        [{"role": "system", "content": prompt.system_prompt}, {"role": "user", "content": user_prompt}],
        prompt.settings,
    )
