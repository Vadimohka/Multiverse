from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.audit import audit
from app.database import get_db
from app.dependencies import require_roles
from app.models import User, UserRole
from app.schemas import UserCreate
from app.security import hash_password

router = APIRouter(prefix="/users", tags=["Пользователи"])


def serialize(user: User) -> dict:
    return {"id": user.id, "email": user.email, "full_name": user.full_name, "is_active": user.is_active, "roles": [r.role for r in user.roles], "created_at": user.created_at}


@router.get("")
def list_users(db: Session = Depends(get_db), _: User = Depends(require_roles("ADMINISTRATOR"))) -> list[dict]:
    return [serialize(u) for u in db.scalars(select(User).options(selectinload(User.roles)).order_by(User.created_at.desc())).all()]


@router.post("", status_code=201)
def create_user(payload: UserCreate, db: Session = Depends(get_db), actor: User = Depends(require_roles("ADMINISTRATOR"))) -> dict:
    if db.scalar(select(User).where(User.email == payload.email)):
        raise HTTPException(status_code=409, detail="Email уже используется")
    user = User(email=payload.email, full_name=payload.full_name, password_hash=hash_password(payload.password))
    user.roles = [UserRole(role=r) for r in payload.roles]
    db.add(user); db.flush(); audit(db, actor.id, "CREATE", "user", user.id, after={"email": user.email, "roles": payload.roles}); db.commit(); db.refresh(user)
    return serialize(user)
