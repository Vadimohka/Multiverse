from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit
from app.database import get_db
from app.dependencies import get_current_user, require_roles
from app.models import Record, RecordVersion, ReviewTask, User
from app.routers.workflows import stable_record_hash
from app.schemas import ReviewDecision, ReviewOut

router = APIRouter(prefix="/review", tags=["Проверка данных"])


@router.get("", response_model=list[ReviewOut])
def list_review(
    project_id: str | None = None,
    status: str = "PENDING",
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[ReviewTask]:
    stmt = select(ReviewTask).where(ReviewTask.status == status).order_by(ReviewTask.created_at.desc())
    if project_id:
        stmt = stmt.where(ReviewTask.project_id == project_id)
    return list(db.scalars(stmt).all())


def decide(task_id: str, status: str, payload: ReviewDecision, db: Session, user: User) -> ReviewTask:
    task = db.get(ReviewTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Задача проверки не найдена")
    if task.status != "PENDING":
        raise HTTPException(status_code=409, detail="Решение уже принято")
    task.status = status
    task.decision_by = user.id
    task.decision_comment = payload.comment
    # A sample audits an existing visible record, not a proposed replacement.
    # Rejecting it must not make data disappear from an export.
    is_sample = task.reason == "SAMPLED_RECORD"
    if task.record_id:
        record = db.get(Record, task.record_id)
        if record:
            if is_sample and status != "CORRECTED":
                audit(db, user.id, status, "review_task", task.id, after={"comment": payload.comment})
                db.commit()
                db.refresh(task)
                return task
            pending_version = db.scalar(
                select(RecordVersion)
                .where(RecordVersion.record_id == record.id, RecordVersion.run_id == task.run_id)
                .order_by(RecordVersion.version_number.desc())
            )
            if status in {"APPROVED", "CORRECTED"}:
                accepted = payload.corrected_data or task.new_data
                data_hash = stable_record_hash(accepted) if isinstance(accepted, dict) else hashlib.sha256(
                    json.dumps(accepted, ensure_ascii=False, sort_keys=True, default=str).encode()
                ).hexdigest()
                version_number = pending_version.version_number if pending_version and not is_sample else record.current_version + 1
                record.data_json = accepted
                record.data_hash = data_hash
                record.current_version = version_number
                record.review_status = status
                record.confidence = float(accepted.get("confidence", record.confidence) or 0) if isinstance(accepted, dict) else record.confidence
                if pending_version and not is_sample:
                    pending_version.data_json = accepted
                    pending_version.data_hash = data_hash
                    pending_version.review_status = status
                elif status == "CORRECTED":
                    db.add(
                        RecordVersion(
                            record_id=record.id,
                            run_id=task.run_id,
                            version_number=version_number,
                            data_json=accepted,
                            data_hash=data_hash,
                            confidence=record.confidence,
                            review_status=status,
                        )
                    )
            elif status == "REJECTED":
                if pending_version:
                    pending_version.review_status = "REJECTED"
                    # A rejected first observation must never become a visible,
                    # approved record. For a changed record, keep the previously
                    # approved current version untouched.
                    is_initial_pending_version = (
                        pending_version.version_number == 1 and record.review_status == "PENDING"
                    )
                    if is_initial_pending_version:
                        record.status = "REJECTED"
                        record.review_status = "REJECTED"
    audit(db, user.id, status, "review_task", task.id, after={"comment": payload.comment})
    db.commit()
    db.refresh(task)
    return task


@router.post("/{task_id}/approve", response_model=ReviewOut)
def approve(
    task_id: str,
    payload: ReviewDecision,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("ADMINISTRATOR", "OPERATOR")),
) -> ReviewTask:
    return decide(task_id, "APPROVED", payload, db, user)


@router.post("/{task_id}/reject", response_model=ReviewOut)
def reject(
    task_id: str,
    payload: ReviewDecision,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("ADMINISTRATOR", "OPERATOR")),
) -> ReviewTask:
    return decide(task_id, "REJECTED", payload, db, user)


@router.post("/{task_id}/correct", response_model=ReviewOut)
def correct(
    task_id: str,
    payload: ReviewDecision,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("ADMINISTRATOR", "OPERATOR")),
) -> ReviewTask:
    if payload.corrected_data is None:
        raise HTTPException(status_code=422, detail="corrected_data обязателен")
    return decide(task_id, "CORRECTED", payload, db, user)
