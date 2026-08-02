from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog


def audit(
    db: Session,
    actor_id: str | None,
    action: str,
    entity_type: str,
    entity_id: str | None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    db.add(AuditLog(
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_json=before or {},
        after_json=after or {},
    ))
