from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.dependencies import get_current_user, require_roles
from app.enums import TERMINAL_RUN_STATUSES
from app.models import NodeRun, RawDocument, Run, User
from app.routers.workflows import execute_run
from app.schemas import RunOut
from app.services.artifact_storage import ArtifactStorage

router = APIRouter(prefix="/runs", tags=["Запуски"])


@router.get("", response_model=list[RunOut])
def list_runs(
    workflow_id: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[Run]:
    stmt = select(Run).order_by(Run.created_at.desc()).limit(200)
    if workflow_id:
        stmt = stmt.where(Run.workflow_id == workflow_id)
    if status:
        stmt = stmt.where(Run.status == status)
    return list(db.scalars(stmt).all())


@router.get("/artifacts/{artifact_id}/download")
async def download_artifact(
    artifact_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Response:
    artifact = db.get(RawDocument, artifact_id)
    if not artifact:
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
) -> StreamingResponse:
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
    _: User = Depends(get_current_user),
) -> dict:
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Запуск не найден")
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
    _: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER", "OPERATOR")),
) -> dict:
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Запуск не найден")
    if run.status in TERMINAL_RUN_STATUSES:
        raise HTTPException(status_code=409, detail="Запуск уже завершён")
    run.status = "CANCELLED"
    db.commit()
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
