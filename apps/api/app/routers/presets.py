"""Immutable universal workflow blueprints and source preset revisions."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from workflow_engine import compile_executable_plan, standard_v2_graph

from app.audit import audit
from app.database import get_db
from app.dependencies import get_current_user, require_roles
from app.models import (
    Dataset,
    Source,
    SourcePresetRevision,
    User,
    Workflow,
    WorkflowBlueprintRevision,
)
from app.schemas import (
    SourcePresetInstantiateRequest,
    SourcePresetRevisionCreate,
    SourcePresetRevisionOut,
    WorkflowBlueprintRevisionCreate,
    WorkflowBlueprintRevisionOut,
    WorkflowOut,
)
from app.services.authorization import (
    require_project,
    require_project_object,
    require_same_project,
    scope_to_projects,
)
from app.services.preset_compiler import (
    PresetCompilationError,
    compile_preset,
    legacy_conversion_report,
    validate_blueprint_graph,
    validate_status,
)

router = APIRouter(prefix="/presets", tags=["Universal presets"])


@router.get("/blueprints", response_model=list[WorkflowBlueprintRevisionOut])
def list_blueprints(
    project_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[WorkflowBlueprintRevision]:
    if project_id:
        require_project(db, user, project_id)
    stmt = select(WorkflowBlueprintRevision).order_by(
        WorkflowBlueprintRevision.slug, WorkflowBlueprintRevision.revision.desc()
    )
    if project_id:
        stmt = stmt.where(WorkflowBlueprintRevision.project_id == project_id)
    return list(db.scalars(scope_to_projects(stmt, WorkflowBlueprintRevision.project_id, db, user)).all())


@router.post("/blueprints", response_model=WorkflowBlueprintRevisionOut, status_code=201)
def create_blueprint(
    payload: WorkflowBlueprintRevisionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER")),
) -> WorkflowBlueprintRevision:
    require_project(db, user, payload.project_id)
    try:
        graph = validate_blueprint_graph(payload.graph_json or standard_v2_graph())
        status = validate_status(payload.status)
    except PresetCompilationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    revision = _next_revision(db, WorkflowBlueprintRevision, payload.project_id, payload.slug)
    item = WorkflowBlueprintRevision(
        project_id=payload.project_id,
        slug=payload.slug,
        name=payload.name,
        description=payload.description,
        revision=revision,
        status=status,
        graph_json=graph,
        parameter_schema_json=payload.parameter_schema_json,
        conversion_report_json={"targetContractVersion": 2, "unresolved": [], "warnings": []},
        created_by=user.id,
    )
    db.add(item)
    db.flush()
    audit(db, user.id, "CREATE", "workflow_blueprint_revision", item.id, after={"slug": item.slug, "revision": item.revision, "status": item.status})
    db.commit()
    db.refresh(item)
    return item


@router.get("/blueprints/{blueprint_id}", response_model=WorkflowBlueprintRevisionOut)
def get_blueprint(
    blueprint_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> WorkflowBlueprintRevision:
    return require_project_object(db, user, WorkflowBlueprintRevision, blueprint_id, label="Blueprint")


@router.get("/source", response_model=list[SourcePresetRevisionOut])
def list_source_presets(
    project_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[SourcePresetRevision]:
    if project_id:
        require_project(db, user, project_id)
    stmt = select(SourcePresetRevision).order_by(
        SourcePresetRevision.slug, SourcePresetRevision.revision.desc()
    )
    if project_id:
        stmt = stmt.where(SourcePresetRevision.project_id == project_id)
    return list(db.scalars(scope_to_projects(stmt, SourcePresetRevision.project_id, db, user)).all())


@router.post("/source", response_model=SourcePresetRevisionOut, status_code=201)
def create_source_preset(
    payload: SourcePresetRevisionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER")),
) -> SourcePresetRevision:
    require_project(db, user, payload.project_id)
    blueprint = require_project_object(db, user, WorkflowBlueprintRevision, payload.blueprint_revision_id, label="Blueprint")
    require_same_project(payload.project_id, blueprint)
    try:
        compile_preset(
            blueprint.graph_json,
            {
                "config_json": payload.config_json,
                "source_policy_ref": payload.source_policy_ref,
                "dataset_schema_ref": payload.dataset_schema_ref,
            },
        )
        status = validate_status(payload.status)
        if status == "VERIFIED" and not payload.fixture_refs:
            raise PresetCompilationError("A VERIFIED preset requires at least one fixture reference")
    except PresetCompilationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    revision = _next_revision(db, SourcePresetRevision, payload.project_id, payload.slug)
    item = SourcePresetRevision(
        project_id=payload.project_id,
        blueprint_revision_id=blueprint.id,
        slug=payload.slug,
        name=payload.name,
        revision=revision,
        status=status,
        config_json=payload.config_json,
        source_policy_ref=payload.source_policy_ref,
        dataset_schema_ref=payload.dataset_schema_ref,
        fixture_refs=payload.fixture_refs,
        last_verified_at=datetime.now(UTC) if status == "VERIFIED" else None,
        created_by=user.id,
    )
    db.add(item)
    db.flush()
    audit(db, user.id, "CREATE", "source_preset_revision", item.id, after={"slug": item.slug, "revision": item.revision, "status": item.status})
    db.commit()
    db.refresh(item)
    return item


@router.get("/source/{preset_id}", response_model=SourcePresetRevisionOut)
def get_source_preset(
    preset_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SourcePresetRevision:
    return require_project_object(db, user, SourcePresetRevision, preset_id, label="Source preset")


@router.get("/source/{preset_id}/compile")
def preview_source_preset(
    preset_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    preset = require_project_object(db, user, SourcePresetRevision, preset_id, label="Source preset")
    blueprint = require_project_object(db, user, WorkflowBlueprintRevision, preset.blueprint_revision_id, label="Blueprint")
    try:
        result = compile_preset(blueprint.graph_json, preset.__dict__)
    except PresetCompilationError as exc:  # immutable historical rows might predate validation
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"graph": result.graph, "report": result.report, "preset": {"id": preset.id, "revision": preset.revision, "status": preset.status}}


@router.post("/source/{preset_id}/instantiate", response_model=WorkflowOut, status_code=201)
def instantiate_source_preset(
    preset_id: str,
    payload: SourcePresetInstantiateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER")),
) -> Workflow:
    preset = require_project_object(db, user, SourcePresetRevision, preset_id, label="Source preset")
    if preset.status == "DEPRECATED":
        raise HTTPException(status_code=422, detail="A deprecated source preset cannot be instantiated")
    blueprint = require_project_object(db, user, WorkflowBlueprintRevision, preset.blueprint_revision_id, label="Blueprint")
    try:
        result = compile_preset(blueprint.graph_json, preset.__dict__)
    except PresetCompilationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if payload.source_id:
        source = require_project_object(db, user, Source, payload.source_id, label="Source")
        require_same_project(preset.project_id, source)
        result.graph["settings"]["source_id"] = source.id
    if payload.dataset_id:
        dataset = require_project_object(db, user, Dataset, payload.dataset_id, label="Dataset")
        require_same_project(preset.project_id, dataset)
        result.graph["settings"]["dataset_id"] = dataset.id
    workflow = Workflow(
        project_id=preset.project_id,
        name=payload.name or preset.name,
        description=f"Compiled from source preset {preset.slug}@{preset.revision}",
        graph_json=result.graph,
    )
    db.add(workflow)
    db.flush()
    plan = compile_executable_plan(
        result.graph,
        project_id=workflow.project_id,
        workflow_id=workflow.id,
        workflow_version=workflow.version,
        source_id=payload.source_id,
        revision_refs={"blueprintRevisionId": blueprint.id, "sourcePresetRevisionId": preset.id},
    )
    workflow.graph_json["settings"]["compiledPlanDigest"] = plan.digest
    # JSON columns do not reliably observe nested in-place mutation across
    # every SQLAlchemy dialect.  Reassign the graph snapshot so the immutable
    # revision refs are persisted with this compiled workflow.
    graph_snapshot = deepcopy(workflow.graph_json)
    graph_snapshot["settings"]["presetRefs"] = {
        **graph_snapshot["settings"].get("presetRefs", {}),
        "blueprintRevisionId": blueprint.id,
        "sourcePresetRevisionId": preset.id,
    }
    workflow.graph_json = graph_snapshot
    audit(db, user.id, "INSTANTIATE", "source_preset_revision", preset.id, after={"workflow_id": workflow.id, "plan_digest": plan.digest})
    db.commit()
    db.refresh(workflow)
    return workflow


@router.post("/legacy-conversion-report")
def conversion_report(graph_json: dict, _: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> dict:
    return legacy_conversion_report(deepcopy(graph_json))


def _next_revision(db: Session, model, project_id: str, slug: str) -> int:
    return int(
        db.scalar(select(func.max(model.revision)).where(model.project_id == project_id, model.slug == slug))
        or 0
    ) + 1
