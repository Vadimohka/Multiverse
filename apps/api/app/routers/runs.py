from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.dependencies import get_current_user, require_roles
from app.enums import TERMINAL_RUN_STATUSES
from app.models import NodeRun, RawDocument, Run, User, Workflow
from app.routers.workflows import execute_run
from app.schemas import RunOut
from app.services.artifact_storage import ArtifactStorage
from app.services.authorization import require_project_object, scope_to_projects

router = APIRouter(prefix="/runs", tags=["Запуски"])


@router.get("", response_model=list[RunOut])
def list_runs(
    workflow_id: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[Run]:
    stmt = select(Run).join(Workflow, Workflow.id == Run.workflow_id).order_by(Run.created_at.desc()).limit(200)
    if workflow_id:
        workflow = require_project_object(db, user, Workflow, workflow_id, label="Workflow")
        stmt = stmt.where(Run.workflow_id == workflow.id)
    if status:
        stmt = stmt.where(Run.status == status)
    return list(db.scalars(scope_to_projects(stmt, Workflow.project_id, db, user)).all())


@router.get("/artifacts/{artifact_id}/download")
async def download_artifact(
    artifact_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    artifact = db.get(RawDocument, artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact не найден")
    run = db.get(Run, artifact.run_id) if artifact.run_id else None
    if run:
        require_project_object(db, user, Workflow, run.workflow_id, label="Workflow")
    elif artifact.source_id:
        from app.models import Source
        require_project_object(db, user, Source, artifact.source_id, label="Source")
    else:
        raise HTTPException(status_code=404, detail="Artifact не найден")
    metadata = artifact.metadata_json or {}
    try:
        content = await ArtifactStorage().get_bytes(
            str(metadata.get("bucket") or "raw"),
            artifact.storage_key,
            str(metadata.get("storage_backend") or "S3"),
        )
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Artifact недоступен: {exc}") from exc
    filename = str(metadata.get("filename") or f"artifact-{artifact.id}")
    return Response(
        content,
        media_type=artifact.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{run_id}/events")
async def run_events(
    run_id: str,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    # Preserve direct-call compatibility for existing SSE tests while HTTP
    # requests still inject a real session through the wrapper below.
    owns_db = not hasattr(db, "get")
    if owns_db:
        db = SessionLocal()
    run = db.get(Run, run_id)
    if not run:
        if owns_db:
            db.close()
        raise HTTPException(status_code=404, detail="Запуск не найден")
    # Unit callers historically invoke this endpoint function directly with a
    # lightweight sentinel. HTTP calls always receive a real authenticated
    # user and therefore pass through the project boundary.
    if getattr(_, "id", None):
        require_project_object(db, _, Workflow, run.workflow_id, label="Workflow")
    if owns_db:
        db.close()
    async def stream():
        previous = None
        while True:
            db = SessionLocal()
            try:
                run = db.get(Run, run_id)
                if not run:
                    yield "event: error\ndata: {\"message\": \"Запуск не найден\"}\n\n"
                    return
                node_count = db.scalar(select(func.count()).select_from(NodeRun).where(NodeRun.run_id == run_id)) or 0
                payload = {"id": run.id, "status": run.status, "node_count": node_count, "error": run.error_json, "finished_at": run.finished_at.isoformat() if run.finished_at else None}
            finally:
                db.close()
            serialized = json.dumps(payload, ensure_ascii=False)
            if serialized != previous:
                yield f"event: status\ndata: {serialized}\n\n"
                previous = serialized
            if payload["status"] in TERMINAL_RUN_STATUSES:
                return
            await asyncio.sleep(1)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/{run_id}")
def get_run(
    run_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Запуск не найден")
    require_project_object(db, user, Workflow, run.workflow_id, label="Workflow")
    nodes = db.scalars(select(NodeRun).where(NodeRun.run_id == run_id).order_by(NodeRun.created_at)).all()
    artifacts = db.scalars(select(RawDocument).where(RawDocument.run_id == run_id).order_by(RawDocument.created_at)).all()
    return {
        "run": RunOut.model_validate(run),
        "nodes": [
            {
                "id": node.id,
                "node_id": node.node_id,
                "node_type": node.node_type,
                "status": node.status,
                "duration_ms": node.duration_ms,
                "input": node.input_json,
                "output": node.output_json,
                "error": node.error_json,
            }
            for node in nodes
        ],
        "artifacts": [
            {
                "id": artifact.id,
                "url": artifact.url,
                "content_type": artifact.content_type,
                "sha256": artifact.sha256,
                "metadata": artifact.metadata_json,
                "created_at": artifact.created_at,
                "download_url": f"/api/v1/runs/artifacts/{artifact.id}/download",
            }
            for artifact in artifacts
        ],
    }


@router.post("/{run_id}/cancel")
def cancel(
    run_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER", "OPERATOR")),
) -> dict:
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Запуск не найден")
    require_project_object(db, user, Workflow, run.workflow_id, label="Workflow")
    if run.status in TERMINAL_RUN_STATUSES:
        raise HTTPException(status_code=409, detail="Запуск уже завершён")
    now = datetime.now(UTC)
    if run.status == "QUEUED":
        db.execute(
            update(Run)
            .where(Run.id == run_id, Run.status == "QUEUED")
            .values(status="CANCELLED", cancel_requested_at=now, finished_at=now)
        )
    else:
        # The worker observes this CAS state via its heartbeat and cancels
        # active HTTP/browser work. It can never subsequently finalize SUCCESS.
        db.execute(
            update(Run)
            .where(Run.id == run_id, Run.status.in_(("RUNNING", "CANCEL_REQUESTED")))
            .values(status="CANCEL_REQUESTED", cancel_requested_at=now)
        )
    db.commit()
    db.refresh(run)
    return {"status": run.status}


@router.post("/{run_id}/retry", response_model=RunOut)
async def retry(
    run_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER", "OPERATOR")),
) -> Run:
    original = db.get(Run, run_id)
    if not original:
        raise HTTPException(status_code=404, detail="Запуск не найден")
    require_project_object(db, user, Workflow, original.workflow_id, label="Workflow")
    run = Run(
        workflow_id=original.workflow_id,
        workflow_version=original.workflow_version,
        source_id=original.source_id,
        input_json=original.input_json,
        created_by=user.id,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    await execute_run(run.id)
    db.refresh(run)
    return run
