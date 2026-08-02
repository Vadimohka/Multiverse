from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.security import decode_token

bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется авторизация")
    try:
        payload = decode_token(credentials.credentials)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Недействительный токен") from exc
    user = db.scalar(select(User).where(User.id == payload["sub"], User.is_active.is_(True)))
    if not user:
        raise HTTPException(status_code=401, detail="Пользователь не найден")
    return user


def role_names(user: User) -> set[str]:
    return {r.role for r in user.roles}


def require_roles(*allowed: str) -> Callable[[User], User]:
    def dependency(user: User = Depends(get_current_user)) -> User:
        if not role_names(user).intersection(allowed):
            raise HTTPException(status_code=403, detail="Недостаточно прав")
        return user
    return dependency
