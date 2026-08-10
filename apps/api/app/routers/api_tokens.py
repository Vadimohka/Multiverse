import hashlib
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_roles
from app.models import ApiToken, Dataset, User
from app.schemas import ApiTokenCreate, ApiTokenCreated

router = APIRouter(prefix="/api-tokens", tags=["API tokens"])
ALLOWED_SCOPES = {"datasets:read"}


@router.post("", response_model=ApiTokenCreated, status_code=201)
def create_api_token(
    payload: ApiTokenCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER")),
) -> dict:
    scopes = sorted(set(payload.scopes))
    if not scopes or not set(scopes) <= ALLOWED_SCOPES:
        raise HTTPException(status_code=422, detail="Unsupported API token scope")
    dataset_ids = list(dict.fromkeys(payload.dataset_ids))
    existing = set(db.scalars(select(Dataset.id).where(Dataset.id.in_(dataset_ids))).all())
    if existing != set(dataset_ids):
        raise HTTPException(status_code=404, detail="Dataset не найден")
    if payload.expires_at and (payload.expires_at.tzinfo is None or payload.expires_at.utcoffset() is None):
        raise HTTPException(status_code=422, detail="expires_at must include a timezone")
    raw = "mv_" + secrets.token_urlsafe(32)
    token = ApiToken(
        owner_user_id=user.id,
        name=payload.name,
        token_prefix=raw[:12],
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        scopes=scopes,
        dataset_ids=dataset_ids,
        expires_at=payload.expires_at.astimezone(UTC) if payload.expires_at else None,
    )
    db.add(token)
    db.commit()
    db.refresh(token)
    return {
        "id": token.id,
        "name": token.name,
        "token": raw,
        "token_prefix": token.token_prefix,
        "scopes": token.scopes,
        "dataset_ids": token.dataset_ids,
        "expires_at": token.expires_at,
    }


@router.get("")
def list_api_tokens(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER")),
) -> list[dict]:
    tokens = db.scalars(select(ApiToken).where(ApiToken.owner_user_id == user.id).order_by(ApiToken.created_at.desc())).all()
    return [
        {
            "id": token.id,
            "name": token.name,
            "token_prefix": token.token_prefix,
            "scopes": token.scopes,
            "dataset_ids": token.dataset_ids,
            "expires_at": token.expires_at,
            "last_used_at": token.last_used_at,
            "revoked_at": token.revoked_at,
            "created_at": token.created_at,
        }
        for token in tokens
    ]


@router.delete("/{token_id}", status_code=204)
def revoke_api_token(
    token_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER")),
) -> None:
    token = db.scalar(select(ApiToken).where(ApiToken.id == token_id, ApiToken.owner_user_id == user.id))
    if not token:
        raise HTTPException(status_code=404, detail="API token не найден")
    token.revoked_at = datetime.now(UTC)
    db.commit()
