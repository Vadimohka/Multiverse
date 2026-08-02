from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import User
from app.schemas import LoginRequest, TokenPair
from app.security import create_access_token, create_refresh_token, decode_token, verify_password

router = APIRouter(prefix="/auth", tags=["Авторизация"])


@router.post("/login", response_model=TokenPair)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenPair:
    user = db.scalar(select(User).where(User.email == payload.email))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Неверный email или пароль")
    roles = [r.role for r in user.roles]
    return TokenPair(access_token=create_access_token(user.id, roles), refresh_token=create_refresh_token(user.id, roles))


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/refresh", response_model=TokenPair)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> TokenPair:
    try:
        decoded = decode_token(payload.refresh_token, "refresh")
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Недействительный refresh token") from exc
    # Refresh tokens are deliberately not self-sufficient: a user may have been
    # disabled or had roles changed after the token was issued.
    user = db.scalar(
        select(User)
        .options(selectinload(User.roles))
        .where(User.id == decoded.get("sub"), User.is_active.is_(True))
    )
    if not user:
        raise HTTPException(status_code=401, detail="Пользователь не найден или отключён")
    roles = [role.role for role in user.roles]
    return TokenPair(
        access_token=create_access_token(user.id, roles),
        refresh_token=create_refresh_token(user.id, roles),
    )
