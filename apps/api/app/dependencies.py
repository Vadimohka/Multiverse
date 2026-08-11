import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ApiToken, User
from app.security import decode_token
from app.services.rate_limit import enforce_api_token_rate_limit

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class DataPrincipal:
    user: User
    api_token: ApiToken | None = None


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется авторизация"
        )
    try:
        payload = decode_token(credentials.credentials)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Недействительный токен") from exc
    user = db.scalar(select(User).where(User.id == payload["sub"], User.is_active.is_(True)))
    if not user:
        raise HTTPException(status_code=401, detail="Пользователь не найден")
    return user


def get_data_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> DataPrincipal:
    if not credentials:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    raw = credentials.credentials
    if not raw.startswith("mv_"):
        return DataPrincipal(user=get_current_user(credentials, db))
    digest = hashlib.sha256(raw.encode()).hexdigest()
    token = db.scalar(
        select(ApiToken).where(ApiToken.token_hash == digest, ApiToken.revoked_at.is_(None))
    )
    now = datetime.now(UTC)
    expires_at = token.expires_at if token else None
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if not token or (expires_at and expires_at <= now):
        raise HTTPException(status_code=401, detail="Недействительный API token")
    user = db.scalar(select(User).where(User.id == token.owner_user_id, User.is_active.is_(True)))
    if not user:
        raise HTTPException(status_code=401, detail="Владелец API token отключён")
    enforce_api_token_rate_limit(db, token, now)
    token.last_used_at = now
    db.commit()
    return DataPrincipal(user=user, api_token=token)


def authorize_dataset_read(principal: DataPrincipal, dataset_id: str) -> None:
    token = principal.api_token
    if token and (
        "datasets:read" not in (token.scopes or []) or dataset_id not in (token.dataset_ids or [])
    ):
        raise HTTPException(status_code=403, detail="API token не имеет доступа к dataset")


def role_names(user: User) -> set[str]:
    return {r.role for r in user.roles}


def require_roles(*allowed: str) -> Callable[[User], User]:
    def dependency(user: User = Depends(get_current_user)) -> User:
        if not role_names(user).intersection(allowed):
            raise HTTPException(status_code=403, detail="Недостаточно прав")
        return user

    return dependency
