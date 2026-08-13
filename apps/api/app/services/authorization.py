"""Project-scoped authorization primitives shared by API routers.

The application historically checked only a user's global role.  That made a
valid developer token sufficient to enumerate or mutate every project.  These
helpers add an object-level boundary without changing the public IDs or the
legacy ``created_by`` ownership model.
"""

from __future__ import annotations

from typing import Any

from app.models import Project, ProjectMember, User
from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

PROJECT_ROLES = {"OWNER", "EDITOR", "VIEWER"}


def is_platform_admin(user: User) -> bool:
    """Platform administrators retain break-glass operational access."""
    return any(role.role == "ADMINISTRATOR" for role in user.roles)


def accessible_project_ids(db: Session, user: User):
    """Return a SQL selectable of projects visible to a non-admin user."""
    return select(Project.id).where(
        or_(
            Project.created_by == user.id,
            Project.id.in_(
                select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)
            ),
        )
    )


def scope_to_projects(stmt: Any, project_column: Any, db: Session, user: User):
    """Restrict a selectable whose model owns a ``project_id`` column."""
    if is_platform_admin(user):
        return stmt
    return stmt.where(project_column.in_(accessible_project_ids(db, user)))


def require_project(db: Session, user: User, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if not project or (
        not is_platform_admin(user)
        and project.created_by != user.id
        and db.scalar(
            select(ProjectMember.id).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == user.id,
            )
        )
        is None
    ):
        # Use 404 to avoid disclosing the existence of a foreign project.
        raise HTTPException(status_code=404, detail="Проект не найден")
    return project


def has_project_access(db: Session, user: User, project_id: str) -> bool:
    """Non-raising predicate for endpoints that must distinguish 403/404."""
    if is_platform_admin(user):
        return db.get(Project, project_id) is not None
    return (
        db.scalar(
            select(Project.id).where(
                Project.id == project_id,
                or_(
                    Project.created_by == user.id,
                    Project.id.in_(
                        select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)
                    ),
                ),
            )
        )
        is not None
    )


def require_project_owner(db: Session, user: User, project_id: str) -> Project:
    project = require_project(db, user, project_id)
    if is_platform_admin(user) or project.created_by == user.id:
        return project
    member_role = db.scalar(
        select(ProjectMember.role).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user.id,
        )
    )
    if member_role != "OWNER":
        raise HTTPException(status_code=403, detail="Требуется роль владельца проекта")
    return project


def require_project_object[T](
    db: Session,
    user: User,
    model: type[T],
    object_id: str,
    *,
    label: str = "Объект",
) -> T:
    """Load an object with ``project_id`` only when it belongs to the caller."""
    item = db.get(model, object_id)
    project_id = getattr(item, "project_id", None) if item is not None else None
    if item is None or project_id is None:
        raise HTTPException(status_code=404, detail=f"{label} не найден")
    require_project(db, user, str(project_id))
    return item


def require_same_project(project_id: str, *items: Any) -> None:
    """Reject references that would bind objects from different projects."""
    if any(item is None or getattr(item, "project_id", None) != project_id for item in items):
        raise HTTPException(status_code=422, detail="Связанные объекты должны принадлежать одному проекту")


def require_project_capability[T](
    db: Session,
    user: User,
    model: type[T],
    capability_id: str,
    project_id: str,
    *,
    label: str = "Capability",
) -> T:
    """Resolve an enabled capability only in the invoking project's scope.

    ``NULL`` is reserved for historical platform records.  It is available
    only to a break-glass administrator and is never inherited by a normal
    project, which prevents an old global secret/profile from crossing tenants.
    """
    require_project(db, user, project_id)
    item = db.get(model, capability_id)
    if item is None or (
        getattr(item, "project_id", None) != project_id
        and not (is_platform_admin(user) and getattr(item, "project_id", None) is None)
    ):
        raise HTTPException(status_code=404, detail=f"{label} не найден")
    if getattr(item, "enabled", True) is False:
        raise HTTPException(status_code=422, detail=f"{label} отключён")
    return item
