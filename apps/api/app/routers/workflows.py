from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from workflow_engine import NODE_CATALOG, WorkflowEngine, validate_dag
from workflow_engine.nodes import validate_json_schema
from workflow_engine.types import ExecutionContext

from app.audit import audit
from app.config import get_settings
from app.database import SessionLocal, get_db
from app.dependencies import get_current_user, require_roles
from app.models import (
    AIProviderConfig,
    BrowserProfile,
    DatabaseConnection,
    DataSchema,
    Dataset,
    DatasetRun,
    LLMCall,
    NodeRun,
    RawDocument,
    Record,
    RecordObservation,
    RecordVersion,
    ReviewTask,
    Run,
    Secret,
    Source,
    SourceProfile,
    User,
    Workflow,
    WorkflowVersion,
    utcnow,
)
from app.schemas import (
    NodeTestRequest,
    RunOut,
    RunRequest,
    WorkflowCreate,
    WorkflowImportRequest,
    WorkflowOut,
    WorkflowTemplateRequest,
    WorkflowUpdate,
)
from app.security import decrypt_secret
from app.services.artifact_storage import ArtifactStorage
from app.services.run_routing import queue_for_graph

router = APIRouter(prefix="/workflows", tags=["Workflows"])
settings = get_settings()


@router.get("/catalog")
def node_catalog(_: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    return NODE_CATALOG


@router.get("", response_model=list[WorkflowOut])
def list_workflows(
    project_id: str | None = None,
    include_legacy: bool = False,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[Workflow]:
    stmt = select(Workflow).order_by(Workflow.updated_at.desc())
    if project_id:
        stmt = stmt.where(Workflow.project_id == project_id)
    workflows = list(db.scalars(stmt).all())
    return workflows


@router.post("", response_model=WorkflowOut, status_code=201)
def create_workflow(
    payload: WorkflowCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER")),
) -> Workflow:
    errors = validate_dag(payload.graph_json)
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    workflow = Workflow(**payload.model_dump())
    db.add(workflow)
    db.flush()
    audit(db, user.id, "CREATE", "workflow", workflow.id, after=payload.model_dump())
    db.commit()
    db.refresh(workflow)
    return workflow


@router.post("/import", response_model=WorkflowOut, status_code=201)
def import_workflow(payload: WorkflowImportRequest, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> Workflow:
    graph = deepcopy(payload.graph_json)
    graph.setdefault("settings", {}).pop("source_id", None)
    errors = validate_dag(graph)
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    workflow = Workflow(project_id=payload.project_id, name=payload.name, description=payload.description, graph_json=graph)
    db.add(workflow); db.flush()
    audit(db, user.id, "IMPORT", "workflow", workflow.id, after={"name": workflow.name})
    db.commit(); db.refresh(workflow)
    return workflow


@router.get("/{workflow_id}/export")
def export_workflow(workflow_id: str, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> Response:
    workflow = db.get(Workflow, workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    graph = deepcopy(workflow.graph_json)
    graph.setdefault("settings", {}).pop("source_id", None)
    payload = {"format": "parser-studio-workflow/v1", "name": workflow.name, "description": workflow.description, "graph_json": graph}
    filename = "workflow-" + "".join(char if char.isalnum() else "-" for char in workflow.name.lower()).strip("-") + ".json"
    return Response(content=json.dumps(payload, ensure_ascii=False, indent=2), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/from-source", response_model=WorkflowOut, status_code=201)
def create_from_source(
    payload: WorkflowTemplateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER")),
) -> Workflow:
    source = db.get(Source, payload.source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Источник не найден")
    # Older clients/profile API calls may store the profiler result separately.
    # Hydrate source settings before generating the graph so that endpoint is
    # never silently replaced by a demo template.
    if not (isinstance(source.settings, dict) and source.settings.get("profile")):
        latest_profile = db.scalar(select(SourceProfile).where(SourceProfile.source_id == source.id).order_by(SourceProfile.created_at.desc()))
        if latest_profile:
            source.settings = {**(source.settings or {}), "profile": latest_profile.result_json}
    graph = build_source_template(source, payload.template)
    workflow = Workflow(
        project_id=source.project_id,
        name=payload.name or f"Парсер: {source.name}",
        description=f"Workflow для источника {source.name}",
        graph_json=graph,
    )
    db.add(workflow)
    db.flush()
    audit(db, user.id, "CREATE", "workflow", workflow.id, after={"source_id": source.id, "template": payload.template})
    db.commit()
    db.refresh(workflow)
    return workflow


@router.get("/{workflow_id}", response_model=WorkflowOut)
def get_workflow(
    workflow_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Workflow:
    workflow = db.get(Workflow, workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow не найден")
    return workflow


@router.patch("/{workflow_id}", response_model=WorkflowOut)
def update_workflow(
    workflow_id: str,
    payload: WorkflowUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER")),
) -> Workflow:
    workflow = db.get(Workflow, workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow не найден")
    if payload.graph_json is not None:
        errors = validate_dag(payload.graph_json)
        if errors:
            raise HTTPException(status_code=422, detail=errors)
    changes = {
        key: value
        for key, value in payload.model_dump(exclude_none=True).items()
        if getattr(workflow, key) != value
    }
    if not changes:
        return workflow
    before = {"name": workflow.name, "version": workflow.version, "graph_json": workflow.graph_json}
    for key, value in changes.items():
        setattr(workflow, key, value)
    workflow.version += 1
    audit(db, user.id, "UPDATE", "workflow", workflow.id, before=before, after={"version": workflow.version})
    db.commit()
    db.refresh(workflow)
    return workflow


@router.post("/{workflow_id}/validate")
def validate(
    workflow_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER")),
) -> dict[str, Any]:
    workflow = db.get(Workflow, workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow не найден")
    errors = validate_dag(workflow.graph_json)
    known = {item["type"] for item in NODE_CATALOG}
    for node in workflow.graph_json.get("nodes", []):
        node_type = node.get("type") or node.get("data", {}).get("type")
        if node_type not in known:
            errors.append(f"Неизвестный тип узла: {node_type}")
    return {"valid": not errors, "errors": errors}


@router.post("/{workflow_id}/publish", response_model=WorkflowOut)
def publish(
    workflow_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER")),
) -> Workflow:
    workflow = db.get(Workflow, workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow не найден")
    errors = validate_dag(workflow.graph_json)
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    # WorkflowVersion is the immutable draft revision created by PATCH.  A
    # publication must point at that same revision, not at a separate counter:
    # otherwise the counters eventually collide and a newly published graph can
    # silently keep running an older snapshot.
    version = workflow.version
    published = db.scalar(
        select(WorkflowVersion).where(
            WorkflowVersion.workflow_id == workflow.id,
            WorkflowVersion.version == version,
        )
    )
    if published is None:
        db.add(
            WorkflowVersion(
                workflow_id=workflow.id,
                version=version,
                graph_json=deepcopy(workflow.graph_json),
                created_by=user.id,
            )
        )
    workflow.published_version = version
    audit(db, user.id, "PUBLISH", "workflow", workflow.id, after={"published_version": version})
    db.commit()
    db.refresh(workflow)
    return workflow


@router.post("/node-test")
async def test_node(
    payload: NodeTestRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER", "OPERATOR")),
) -> dict[str, Any]:
    graph = node_test_graph(payload)
    source = db.get(Source, payload.source_id) if payload.source_id else None
    variables, secrets = build_execution_variables(db, source)
    context = ExecutionContext(
        run_id="node-test",
        project_id=source.project_id if source else "test",
        workflow_version_id="test",
        user_id=user.id,
        variables={**variables, **payload.inputs},
        secrets=secrets,
        artifact_storage=ArtifactStorage(),
    )
    return await WorkflowEngine().execute(graph, context, payload.inputs)


def node_test_graph(payload: NodeTestRequest) -> dict[str, Any]:
    """Return the selected node together with all of its upstream dependencies.

    A node such as JSONPath is not useful in isolation: its input is the
    response produced by HTTP Request.  The editor therefore submits its
    current graph and target node; this helper keeps the test bounded to the
    minimal dependency subgraph while preserving the old one-node API.
    """
    if not payload.graph or not payload.target_node_id:
        return {
            "version": 1,
            "settings": {},
            "nodes": [{"id": "test", "type": payload.node_type, "config": payload.config}],
            "edges": [],
        }
    errors = validate_dag(payload.graph)
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    node_id = str(payload.target_node_id)
    nodes = {str(node.get("id")): node for node in payload.graph.get("nodes", [])}
    if node_id not in nodes:
        raise HTTPException(status_code=422, detail="Выбранный узел отсутствует в workflow")
    incoming: dict[str, list[str]] = {}
    for edge in payload.graph.get("edges", []):
        source, target = str(edge.get("source")), str(edge.get("target"))
        incoming.setdefault(target, []).append(source)
    included: set[str] = set()
    stack = [node_id]
    while stack:
        current = stack.pop()
        if current in included:
            continue
        included.add(current)
        stack.extend(incoming.get(current, []))
    return {
        "version": payload.graph.get("version", 1),
        "settings": payload.graph.get("settings", {}),
        "nodes": [node for node in payload.graph.get("nodes", []) if str(node.get("id")) in included],
        "edges": [
            edge for edge in payload.graph.get("edges", [])
            if str(edge.get("source")) in included and str(edge.get("target")) in included
        ],
    }


def _source_extractor_config(source: Source) -> dict[str, Any]:
    """Return the profiler suggestion plus explicit source overrides.

    Source settings are intentionally JSON: users can edit them in the
    workflow editor and a new site never requires a bank-specific code change.
    """
    settings = source.settings if isinstance(source.settings, dict) else {}
    profile = settings.get("profile") if isinstance(settings.get("profile"), dict) else {}
    configured = settings.get("extractor") if isinstance(settings.get("extractor"), dict) else {}
    profile_extractor = profile.get("extractor") if isinstance(profile.get("extractor"), dict) else {}
    candidates = profile.get("repeating_candidates") if isinstance(profile.get("repeating_candidates"), list) else []
    candidate = configured.get("candidate") if isinstance(configured.get("candidate"), dict) else None
    if not candidate:
        usable = [item for item in candidates if isinstance(item, dict) and item.get("selector")]
        candidate = max(usable, key=lambda item: (len(item.get("fields") or []), int(item.get("count") or 0)), default={})
    container_selector = str(configured.get("container_selector") or profile_extractor.get("container_selector") or candidate.get("selector") or "")
    fields = configured.get("fields") if isinstance(configured.get("fields"), list) else profile_extractor.get("fields") if isinstance(profile_extractor.get("fields"), list) else candidate.get("fields")
    fields = [dict(item) for item in (fields or []) if isinstance(item, dict) and item.get("name") and item.get("selector")]
    if not fields and container_selector:
        fields = [{"name": "url", "selector": "a[href]", "attribute": "href"}]
    link_field = next((item for item in fields if item.get("name") in {"url", "link", "href"} and item.get("attribute") == "href"), None)
    if link_field and link_field.get("name") != "url":
        link_field = {**link_field, "name": "url"}
        fields = [link_field if item.get("name") in {"link", "href"} else item for item in fields]
    detail = configured.get("detail") if isinstance(configured.get("detail"), dict) else settings.get("detail")
    if not isinstance(detail, dict):
        detail = {}
    return {
        "container_selector": container_selector,
        "fields": fields,
        "follow_links": bool(configured.get("follow_links", settings.get("follow_links", profile_extractor.get("follow_links", bool(link_field))))),
        "detail": detail,
    }


def build_source_template(source: Source, template: str) -> dict[str, Any]:
    fetch_type = "browser_open" if source.fetch_mode == "PLAYWRIGHT" else "download_file" if source.fetch_mode == "DOCUMENT" else "http_request"
    fetch_config: dict[str, Any] = {"url": "{{source.url}}", "timeout": source.settings.get("timeout", 45)}
    if source.fetch_mode == "XHR_JSON":
        fetch_config.update(source.settings.get("http_request") or {})
        nodes = [
            {"id": "trigger", "type": "manual_trigger", "position": {"x": 20, "y": 160}, "config": {}},
            {"id": "fetch", "type": "http_request", "position": {"x": 280, "y": 160}, "config": fetch_config},
            {"id": "json", "type": "json_path", "position": {"x": 540, "y": 160}, "config": {"input_path": "body", "path": "$"}},
            {"id": "mapping", "type": "mapping", "position": {"x": 690, "y": 160}, "config": {"input_path": "records", "fields": []}},
            {"id": "output", "type": "output", "position": {"x": 900, "y": 160}, "config": {"input_path": "records"}},
        ]
        edges = [
            {"id": "e-trigger-fetch", "source": "trigger", "target": "fetch"},
            {"id": "e-fetch-json", "source": "fetch", "target": "json"},
            {"id": "e-json-mapping", "source": "json", "target": "mapping"}, {"id": "e-mapping-output", "source": "mapping", "target": "output"},
        ]
        return {"version": 1, "settings": {"source_id": source.id, "review_policy": {"new": True, "changed": True, "confidence_below": 0.8}}, "nodes": nodes, "edges": edges}
    extractor = _source_extractor_config(source)
    nodes: list[dict[str, Any]] = [
        {"id": "trigger", "type": "manual_trigger", "position": {"x": 20, "y": 160}, "config": {}},
        {"id": "fetch", "type": fetch_type, "position": {"x": 240, "y": 160}, "config": fetch_config},
    ]
    edges: list[dict[str, Any]] = [{"id": "e-trigger-fetch", "source": "trigger", "target": "fetch"}]
    if fetch_type == "download_file":
        nodes.extend([
            {"id": "parse", "type": "parse_document", "position": {"x": 480, "y": 160}, "config": {"input_path": "content_base64", "filename_path": "filename"}},
            {"id": "mapping", "type": "mapping", "position": {"x": 620, "y": 160}, "config": {"input_path": "records", "fields": []}},
            {"id": "output", "type": "output", "position": {"x": 820, "y": 160}, "config": {"input_path": "records"}},
        ])
    else:
        # Prefer profiler-generated selectors.  With no repeating candidate we
        # still create a useful, editable link collector instead of demo CSS.
        if extractor["container_selector"]:
            parse_node = {"id": "parse", "type": "parse_html", "position": {"x": 480, "y": 160}, "config": {"input_path": "body"}}
            extract_node = {"id": "extract", "type": "extract_repeating_list", "position": {"x": 700, "y": 160}, "config": {"input_path": "html", "container_selector": extractor["container_selector"], "fields": extractor["fields"]}}
            nodes.extend([parse_node, extract_node])
            edges.extend([{ "id": "e-fetch-parse", "source": "fetch", "target": "parse" }, {"id": "e-parse-extract", "source": "parse", "target": "extract"}])
            previous = "extract"
        else:
            select_node = {"id": "select", "type": "select_elements", "position": {"x": 540, "y": 160}, "config": {"input_path": "body", "selector": str(extractor.get("selector") or "a[href]"), "attribute": str(extractor.get("attribute") or "href")}}
            nodes.append(select_node)
            edges.append({"id": "e-fetch-select", "source": "fetch", "target": "select"})
            previous = "select"

        if extractor["follow_links"]:
            detail = extractor["detail"]
            follow_config = {"input_collection": "records", "url_field": "url", "merge_mode": "MERGE_PARENT_CHILD", "max_pages": int(detail.get("max_pages", 50)), "detail_fields": detail.get("fields", [])}
            if detail.get("table"):
                follow_config["detail_table"] = detail["table"]
            nodes.append({"id": "follow", "type": "follow_links", "position": {"x": 900, "y": 160}, "config": follow_config})
            edges.append({"id": "e-extract-follow", "source": previous, "target": "follow"})
            previous = "follow"

        names = [str(item.get("name")) for item in extractor["fields"] if item.get("name")]
        for name in extractor["detail"].get("field_names", []):
            if name not in names:
                names.append(str(name))
        operations = []
        if "rate" in names:
            operations.append({"type": "rate", "field": "rate"})
        if "term" in names:
            operations.append({"type": "term", "field": "term"})
        if "currency" in names:
            operations.append({"type": "currency", "field": "currency"})
        nodes.append({"id": "transform", "type": "transform", "position": {"x": 1080, "y": 160}, "config": {"input_path": "records", "operations": operations}})
        edges.append({"id": "e-previous-transform", "source": previous, "target": "transform"})
        mapping_fields = [{"target": name, "source_path": name} for name in names]
        if "rate" in names:
            mapping_fields.extend([{"target": "rate_value", "source_path": "rate_value"}])
        if "term" in names:
            mapping_fields.extend([
                {"target": "term_min_days", "source_path": "term_min_days"},
                {"target": "term_max_days", "source_path": "term_max_days"},
            ])
        nodes.append({"id": "mapping", "type": "mapping", "position": {"x": 1260, "y": 160}, "config": {"input_path": "records", "fields": mapping_fields}})
        edges.append({"id": "e-transform-mapping", "source": "transform", "target": "mapping"})
        key_fields = ["url"] if "url" in names else ([names[0]] if names else ["value"])
        nodes.append({"id": "output", "type": "output", "position": {"x": 1440, "y": 160}, "config": {"input_path": "records", "natural_key_fields": key_fields, "on_empty": "warning"}})
        edges.append({"id": "e-mapping-output", "source": "mapping", "target": "output"})
        return {"version": 1, "settings": {"source_id": source.id, "review_policy": {"new": True, "changed": True, "confidence_below": 0.8}}, "nodes": nodes, "edges": edges}
    edges.extend([
        {"id": "e-fetch-parse", "source": "fetch", "target": "parse"},
        {"id": "e-parse-mapping", "source": "parse", "target": "mapping"},
        {"id": "e-mapping-output", "source": "mapping", "target": "output"},
    ])
    return {"version": 1, "settings": {"source_id": source.id, "review_policy": {"new": True, "changed": True, "confidence_below": 0.8}}, "nodes": nodes, "edges": edges}


def persist_result(db: Session, workflow: Workflow, run: Run, result: dict[str, Any]) -> dict[str, Any]:
    graph = active_graph(db, workflow, run.workflow_version)
    graph_settings = graph.get("settings", {})
    output_node = next((node for node in graph.get("nodes", []) if (node.get("type") or node.get("data", {}).get("type")) == "output"), {})
    output_config = output_node.get("config") or output_node.get("data", {}).get("config", {})
    dataset_id = output_config.get("dataset_id") or graph_settings.get("dataset_id")
    if not dataset_id:
        return {"enabled": False, "created": 0, "updated": 0, "unchanged": 0, "review_tasks": 0}
    dataset = db.get(Dataset, dataset_id)
    if not dataset:
        return {"enabled": True, "created": 0, "updated": 0, "unchanged": 0, "review_tasks": 0,
                "blocked": True, "warning": "Configured dataset was not found"}
    output = result.get("result", {})
    preflight = output.get("preflight") if isinstance(output, dict) else None
    if not isinstance(preflight, dict) or not output.get("business_records"):
        return {"enabled": True, "created": 0, "updated": 0, "unchanged": 0, "review_tasks": 0,
                "blocked": True, "warning": "Save Dataset требует явный Mapping business records"}
    if preflight.get("validation_errors"):
        return {"enabled": True, "created": 0, "updated": 0, "unchanged": 0, "review_tasks": 0,
                "blocked": True, "validation_errors": preflight["validation_errors"]}
    payload = output.get("records", [])
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        return {"enabled": True, "created": 0, "updated": 0, "unchanged": 0, "review_tasks": 0,
                "blocked": True, "warning": "Output records is not a list"}
    key_fields = output_config.get("natural_key_fields") or graph_settings.get("natural_key_fields") or dataset.natural_key_fields or []
    if isinstance(key_fields, str):
        key_fields = [item.strip() for item in key_fields.split(",") if item.strip()]
    if not key_fields:
        return {"enabled": True, "created": 0, "updated": 0, "unchanged": 0, "review_tasks": 0,
                "blocked": True, "warning": "Natural key обязателен перед сохранением и review"}
    missing_keys = [{"row": index, "missing": [key for key in key_fields if item.get(key) in (None, "")]}
                    for index, item in enumerate(payload) if isinstance(item, dict) and any(item.get(key) in (None, "") for key in key_fields)]
    if missing_keys:
        return {"enabled": True, "created": 0, "updated": 0, "unchanged": 0, "review_tasks": 0,
                "blocked": True, "validation_errors": missing_keys}
    natural_key_rows: dict[str, list[int]] = {}
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            continue
        natural_key = "|".join(str(item.get(key, "")) for key in key_fields)
        natural_key_rows.setdefault(natural_key, []).append(index)
    duplicate_keys = [
        {"code": "DUPLICATE_NATURAL_KEY", "natural_key": natural_key, "rows": rows}
        for natural_key, rows in natural_key_rows.items()
        if len(rows) > 1
    ]
    if duplicate_keys:
        return {
            "enabled": True,
            "created": 0,
            "updated": 0,
            "unchanged": 0,
            "review_tasks": 0,
            "blocked": True,
            "warning": "Duplicate natural keys in one run",
            "validation_errors": duplicate_keys,
        }
    if dataset.schema_id:
        schema = db.get(DataSchema, dataset.schema_id)
        schema_errors: list[dict[str, Any]] = []
        if schema:
            for index, item in enumerate(payload):
                try:
                    validate_json_schema(business_record(item), schema.schema_json)
                except ValueError as exc:
                    schema_errors.append({"row": index, "code": "SCHEMA", "message": str(exc)})
        if schema_errors:
            return {"enabled": True, "created": 0, "updated": 0, "unchanged": 0, "review_tasks": 0,
                    "blocked": True, "validation_errors": schema_errors}
    # Merge the three policy levels so a dataset-wide quality setting is not
    # accidentally discarded by a reusable workflow's output node.
    review_policy = {
        "new": False,
        "changed": False,
        "confidence_below": 0.0,
        "sample_unchanged": 0,
        **(dataset.review_policy or {}),
        **(graph_settings.get("review_policy") or {}),
        **(output_config.get("review_policy") or {}),
    }
    counters = {"enabled": True, "created": 0, "updated": 0, "unchanged": 0, "review_tasks": 0, "sampled_for_review": 0}
    unchanged_candidates: list[tuple[Record, dict[str, Any]]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        business_item = business_record(item)
        natural_key = "|".join(str(item.get(key, "")) for key in key_fields) if key_fields else hashlib.sha256(json.dumps(item, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
        data_hash = stable_record_hash(business_item)
        confidence = float(business_item.get("confidence", 1.0) or 0)
        record = db.scalar(
            select(Record)
            .where(Record.dataset_id == dataset_id, Record.natural_key == natural_key)
            .with_for_update()
        )
        if record is None:
            requires_review = bool(review_policy.get("new", True) or business_item.get("requires_review") or confidence < float(review_policy.get("confidence_below", 0.8)))
            record = Record(dataset_id=dataset_id, natural_key=natural_key, current_version=1, data_json=business_item, data_hash=data_hash, confidence=confidence, review_status="PENDING" if requires_review else "APPROVED")
            db.add(record)
            db.flush()
            version = RecordVersion(record_id=record.id, run_id=run.id, version_number=1, data_json=business_item, data_hash=data_hash, confidence=confidence, review_status=record.review_status)
            db.add(version)
            db.flush()
            add_record_observation(db, dataset, record, version, run, item, content_changed=True)
            if requires_review:
                db.add(ReviewTask(project_id=workflow.project_id, record_id=record.id, run_id=run.id, reason="NEW_RECORD", old_data={}, new_data=business_item, evidence=evidence_from_item(item)))
                counters["review_tasks"] += 1
            counters["created"] += 1
            continue
        if record.data_hash == data_hash:
            version = db.scalar(
                select(RecordVersion)
                .where(RecordVersion.record_id == record.id, RecordVersion.data_hash == data_hash)
                .order_by(RecordVersion.version_number.desc())
            )
            if version is None:
                raise ValueError(f"Current version is missing for record {record.id}")
            add_record_observation(db, dataset, record, version, run, item, content_changed=False)
            counters["unchanged"] += 1
            unchanged_candidates.append((record, business_item))
            continue
        requires_review = bool(review_policy.get("changed", True) or business_item.get("requires_review") or confidence < float(review_policy.get("confidence_below", 0.8)))
        next_version = (
            db.scalar(select(func.max(RecordVersion.version_number)).where(RecordVersion.record_id == record.id)) or 0
        ) + 1
        version = RecordVersion(record_id=record.id, run_id=run.id, version_number=next_version, data_json=business_item, data_hash=data_hash, confidence=confidence, review_status="PENDING" if requires_review else "APPROVED")
        db.add(version)
        db.flush()
        add_record_observation(db, dataset, record, version, run, item, content_changed=True)
        if requires_review:
            db.add(ReviewTask(project_id=workflow.project_id, record_id=record.id, run_id=run.id, reason="CHANGED_RECORD", old_data=record.data_json, new_data=business_item, evidence=evidence_from_item(item)))
            counters["review_tasks"] += 1
        else:
            record.data_json = business_item
            record.data_hash = data_hash
            record.current_version = next_version
            record.confidence = confidence
            record.review_status = "APPROVED"
        counters["updated"] += 1
    # A recurring sample catches a broken selector or extraction regression even
    # when the content hash has not changed. Ordering varies by run so repeated
    # scheduled checks eventually cover a whole source.
    try:
        sample_size = max(0, int(review_policy.get("sample_unchanged", 0) or 0))
    except (TypeError, ValueError):
        sample_size = 0
    if sample_size and unchanged_candidates:
        sample = sorted(
            unchanged_candidates,
            key=lambda candidate: hashlib.sha256(f"{run.id}|{candidate[0].natural_key}".encode()).hexdigest(),
        )[:sample_size]
        for record, item in sample:
            db.add(ReviewTask(
                project_id=workflow.project_id,
                record_id=record.id,
                run_id=run.id,
                reason="SAMPLED_RECORD",
                old_data=item,
                new_data=item,
                evidence=evidence_from_item(item),
            ))
        counters["review_tasks"] += len(sample)
        counters["sampled_for_review"] = len(sample)
    dataset_run = db.scalar(select(DatasetRun).where(DatasetRun.run_id == run.id, DatasetRun.dataset_id == dataset.id))
    if dataset_run is None:
        db.add(DatasetRun(
            run_id=run.id,
            dataset_id=dataset.id,
            observed_count=counters["created"] + counters["updated"] + counters["unchanged"],
        ))
    return counters


def add_record_observation(
    db: Session,
    dataset: Dataset,
    record: Record,
    version: RecordVersion,
    run: Run,
    item: dict[str, Any],
    *,
    content_changed: bool,
) -> None:
    raw_document = raw_document_for_item(db, run.id, item)
    fetched_at = metadata_datetime(item.get("fetched_at")) or (raw_document.created_at if raw_document else None)
    db.add(RecordObservation(
        dataset_id=dataset.id,
        record_id=record.id,
        record_version_id=version.id,
        run_id=run.id,
        source_id=run.source_id,
        raw_document_id=raw_document.id if raw_document else None,
        natural_key=record.natural_key,
        content_changed=content_changed,
        source_published_at=metadata_datetime(item.get("source_published_at") or item.get("published_at")),
        source_modified_at=metadata_datetime(item.get("source_modified_at")),
        fetched_at=fetched_at,
        observed_at=utcnow(),
    ))


def metadata_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def raw_document_for_item(db: Session, run_id: str, item: dict[str, Any]) -> RawDocument | None:
    provenance = item.get("__provenance") if isinstance(item.get("__provenance"), dict) else {}
    explicit_id = provenance.get("raw_document_id") or item.get("raw_document_id")
    if explicit_id:
        document = db.get(RawDocument, str(explicit_id))
        if document and document.run_id == run_id:
            return document
    artifact = provenance.get("raw_artifact") if isinstance(provenance.get("raw_artifact"), dict) else (
        item.get("raw_artifact") if isinstance(item.get("raw_artifact"), dict) else {}
    )
    sha256 = artifact.get("sha256")
    if not sha256:
        return None
    stmt = select(RawDocument).where(RawDocument.run_id == run_id)
    stmt = stmt.where(RawDocument.sha256 == str(sha256))
    return db.scalar(stmt.order_by(RawDocument.created_at.desc()))


def evidence_from_item(item: dict[str, Any]) -> dict[str, Any]:
    evidence = item.get("evidence")
    return evidence if isinstance(evidence, dict) else {"text": item.get("evidence_text", ""), "source_url": item.get("source_url", "")}


def node_output_count(output: dict[str, Any]) -> int:
    if not isinstance(output, dict): return 0
    if isinstance(output.get("_contract"), dict) and isinstance(output["_contract"].get("item_count"), int):
        return output["_contract"]["item_count"]
    if isinstance(output.get("count"), int): return output["count"]
    records = output.get("records") or output.get("items")
    return len(records) if isinstance(records, list) else (1 if records is not None else 0)


def node_input_count(inputs: dict[str, Any]) -> int:
    if not isinstance(inputs, dict): return 0
    records = inputs.get("records") or inputs.get("items")
    return len(records) if isinstance(records, list) else (1 if records is not None else 0)


def node_preview(output: dict[str, Any]) -> Any:
    if not isinstance(output, dict): return output
    records = output.get("records") or output.get("items")
    return records[:5] if isinstance(records, list) else {key: value for key, value in output.items() if key not in {"body", "html", "text"}}


def node_warning(node_type: str, output: dict[str, Any], error: dict[str, Any]) -> dict[str, str] | None:
    if error or node_output_count(output) != 0: return None
    if (output.get("_contract") or {}).get("output_type") == "DOCUMENT" or output.get("document_diagnostics"):
        return None
    if node_type == "extract_repeating_list":
        return {"code": "REPEATING_LIST_NO_MATCH", "message": "Selector карточки не совпал ни с одним элементом."}
    return {"code": "NODE_ZERO_OUTPUT", "message": "Узел выполнился, но не вернул ни одного элемента."}


def node_recommendations(node_type: str, output: dict[str, Any], error: dict[str, Any]) -> list[str]:
    if error: return ["Проверьте конфигурацию узла и входные данные."]
    if node_output_count(output) == 0 and node_type == "extract_repeating_list": return ["Проверьте rendered HTML или используйте selector picker."]
    return ["Проверьте входной путь и фильтры узла."] if node_output_count(output) == 0 else []


def determine_run_status(graph: dict[str, Any], result: dict[str, Any], persistence: dict[str, Any]) -> str:
    """Report empty extraction honestly instead of calling it SUCCESS."""
    if persistence.get("blocked"):
        return "FAILED"
    if persistence.get("review_tasks"):
        return "WAITING_FOR_REVIEW"
    outputs = result.get("node_outputs", {})
    if any(output.get("partial") for output in outputs.values() if isinstance(output, dict)):
        return "PARTIAL_SUCCESS"
    output_node = next((node for node in graph.get("nodes", []) if (node.get("type") or node.get("data", {}).get("type")) == "output"), {})
    config = output_node.get("config") or output_node.get("data", {}).get("config", {})
    final = result.get("result", {})
    if isinstance(final, dict) and not final.get("records"):
        return "SUCCESS_EMPTY_ALLOWED" if config.get("on_empty") == "allow" else "SUCCESS_EMPTY_UNEXPECTED"
    extractors = {"json_path", "select_elements", "extract_repeating_list", "parse_table", "follow_links", "crawl_links"}
    required_empty = [
        node_id for node_id, output in outputs.items()
        if next(
            (node.get("type") or node.get("data", {}).get("type")
             for node in graph.get("nodes", []) if str(node.get("id")) == node_id),
            "",
        ) in extractors and node_output_count(output) == 0
    ]
    return "SUCCESS_EMPTY_UNEXPECTED" if required_empty else "SUCCESS"


def stable_record_hash(item: dict[str, Any]) -> str:
    """Hash business data only; run evidence and observation time must not create a revision."""
    volatile = {"fetched_at", "observed_at", "evidence", "raw_artifact", "status_code", "artifacts"}
    comparable = {key: value for key, value in item.items() if key not in volatile}
    return hashlib.sha256(json.dumps(comparable, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def business_record(item: dict[str, Any]) -> dict[str, Any]:
    """Remove engine-owned side channels before schema validation and storage."""
    return {
        key: value
        for key, value in item.items()
        if not key.startswith("__") and key not in {"raw_artifact", "raw_document_id"}
    }


def active_graph(db: Session, workflow: Workflow, workflow_version: int) -> dict[str, Any]:
    version = db.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_id == workflow.id, WorkflowVersion.version == workflow_version))
    return version.graph_json if version else workflow.graph_json


def build_execution_variables(db: Session, source: Source | None) -> tuple[dict[str, Any], dict[str, str]]:
    variables: dict[str, Any] = {
        "source": {
            "id": source.id if source else None,
            "name": source.name if source else "",
            "url": source.entry_url if source else "",
            "base_url": source.base_url if source else "",
            "fetch_mode": source.fetch_mode if source else "",
            "settings": source.settings if source else {},
        },
        "deepseek_base_url": settings.deepseek_base_url,
        "ai_providers": {},
        "database_connections": {},
    }
    secrets: dict[str, str] = {}
    for secret in db.scalars(select(Secret)).all():
        secrets[secret.name] = decrypt_secret(secret.encrypted_value)
    if settings.deepseek_api_key:
        secrets.setdefault("DEEPSEEK_API_KEY", settings.deepseek_api_key)
    from app.routers.settings import connection_url
    for connection in db.scalars(select(DatabaseConnection).where(DatabaseConnection.enabled.is_(True))).all():
        variables["database_connections"][connection.name] = {
            "url": connection_url(connection),
            "engine": connection.engine,
            "schema": connection.schema_name,
            "allowed_tables": connection.allowed_tables,
        }
    for provider in db.scalars(select(AIProviderConfig).where(AIProviderConfig.enabled.is_(True))).all():
        api_key = decrypt_secret(provider.encrypted_api_key) if provider.encrypted_api_key else ""
        variables["ai_providers"][provider.provider_name] = {
            "base_url": provider.base_url,
            "default_model": provider.default_model,
            "timeout": provider.timeout,
            "api_key": api_key,
        }
        if api_key:
            secrets[f"AI_PROVIDER_{provider.provider_name}"] = api_key
    if source and source.settings.get("browser_profile_id"):
        profile = db.get(BrowserProfile, source.settings["browser_profile_id"])
        if profile:
            variables["browser_profile"] = {"viewport": profile.viewport, "locale": profile.locale, "timezone": profile.timezone, "user_agent": profile.user_agent, "proxy": profile.proxy}
    return variables, secrets


async def execute_run(run_id: str) -> None:
    db = SessionLocal()
    run: Run | None = None
    try:
        run = db.get(Run, run_id)
        workflow = db.get(Workflow, run.workflow_id) if run else None
        if not run or not workflow:
            return
        if run.status == "CANCELLED":
            return
        run.status = "RUNNING"
        run.started_at = datetime.now(UTC)
        db.commit()
        source = db.get(Source, run.source_id) if run.source_id else None
        variables, secrets = build_execution_variables(db, source)
        variables.update(run.input_json)
        clock_meta = run.input_json.get("_run_clock", {})
        effective_clock = datetime.fromisoformat(clock_meta["effective"].replace("Z", "+00:00")) if clock_meta.get("effective") else datetime.now(UTC)
        context = ExecutionContext(
            run_id=run.id,
            project_id=workflow.project_id,
            workflow_version_id=str(run.workflow_version),
            user_id=run.created_by,
            variables=variables,
            secrets=secrets,
            artifact_storage=ArtifactStorage(),
            effective_run_clock=effective_clock,
        )
        graph = active_graph(db, workflow, run.workflow_version)

        async def callback(node_id: str, node_type: str, input_json: dict[str, Any], output_json: dict[str, Any], duration_ms: int, error: dict[str, Any]) -> None:
            status = "FAILED" if error else "SUCCESS"
            diagnostics = {
                "input_item_count": node_input_count(input_json), "output_item_count": node_output_count(output_json), "duration_ms": duration_ms,
                "preview": node_preview(output_json), "artifacts": output_json.get("artifacts", []) if isinstance(output_json, dict) else [],
                "warning": node_warning(node_type, output_json, error), "error_code": error.get("code"), "error_message": error.get("message"),
                "retryable": bool(error.get("retryable")), "recommendations": node_recommendations(node_type, output_json, error),
            }
            db.add(NodeRun(run_id=run.id, node_id=node_id, node_type=node_type, status=status, input_json=input_json, output_json={**output_json, "_diagnostics": diagnostics}, duration_ms=duration_ms, error_json=error))
            if node_type.startswith("llm_") and not error:
                usage = output_json.get("usage") or {}
                db.add(LLMCall(
                    run_id=run.id,
                    node_id=node_id,
                    provider=str((nodes_config := next((node.get("config", {}) for node in graph.get("nodes", []) if str(node.get("id")) == node_id), {})).get("provider", "")),
                    model=str(output_json.get("model") or nodes_config.get("model", "")),
                    input_tokens=int(usage.get("prompt_tokens", 0) or 0),
                    output_tokens=int(usage.get("completion_tokens", 0) or 0),
                    latency_ms=duration_ms,
                    estimated_cost=float(output_json.get("estimated_cost", 0) or 0),
                    response_json={"parsed_response": output_json.get("parsed_response"), "usage": usage},
                ))
            db.commit()

        initial_inputs = {**run.input_json, "source": variables["source"]}
        result = await WorkflowEngine().execute(graph, context, initial_inputs, callback)
        for artifact in context.artifacts:
            if artifact.get("storage_key"):
                db.add(RawDocument(run_id=run.id, source_id=run.source_id, url=artifact.get("url", ""), content_type=artifact.get("content_type", ""), sha256=artifact.get("sha256", ""), storage_key=artifact["storage_key"], metadata_json={key: value for key, value in artifact.items() if key != "storage_key"}))
        db.flush()
        persistence = persist_result(db, workflow, run, result)
        run.output_json = {**result, "persistence": persistence}
        run.status = determine_run_status(graph, result, persistence)
        if persistence.get("blocked"):
            run.error_json = {
                "code": "PERSISTENCE_BLOCKED",
                "message": persistence.get("warning") or "Dataset persistence validation failed",
                "details": persistence.get("validation_errors") or [],
            }
        run.finished_at = datetime.now(UTC)
        db.commit()
    except Exception as exc:
        db.rollback()
        failed_run = db.get(Run, run_id)
        if failed_run:
            failed_run.status = "FAILED"
            failed_run.error_json = {"code": "WORKFLOW_ERROR", "message": str(exc)}
            failed_run.finished_at = datetime.now(UTC)
            db.commit()
    finally:
        db.close()


@router.post("/{workflow_id}/run", response_model=RunOut, status_code=201)
async def run_workflow(
    workflow_id: str,
    payload: RunRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER", "OPERATOR")),
) -> Run:
    workflow = db.get(Workflow, workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow не найден")
    source_id = payload.source_id or workflow.graph_json.get("settings", {}).get("source_id")
    if source_id and not db.get(Source, source_id):
        raise HTTPException(status_code=404, detail="Источник не найден")
    # A user-triggered run must validate exactly what is on the canvas after
    # Save.  Background schedules still create their runs from the published
    # version in the worker, so drafts cannot silently alter scheduled jobs.
    if payload.use_published and not workflow.published_version:
        raise HTTPException(status_code=422, detail="Опубликованная версия отсутствует: сначала нажмите «Опубликовать»")
    run_version = workflow.published_version if payload.use_published else workflow.version
    graph = active_graph(db, workflow, run_version)
    if "{{source." in json.dumps(graph, ensure_ascii=False) and not source_id:
        raise HTTPException(status_code=422, detail="Выберите источник запуска: этот workflow использует URL источника")
    try:
        zone = ZoneInfo(payload.timezone)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Некорректный timezone") from exc
    if payload.run_clock_mode == "manual":
        if payload.run_at is None:
            raise HTTPException(status_code=422, detail="Для ручного run clock укажите дату и время")
        clock = payload.run_at.replace(tzinfo=zone) if payload.run_at.tzinfo is None else payload.run_at.astimezone(zone)
    else:
        clock = datetime.now(zone)
    run_inputs = {**payload.inputs, "_run_clock": {"mode": payload.run_clock_mode, "timezone": payload.timezone, "effective": clock.isoformat()}}
    run = Run(workflow_id=workflow.id, workflow_version=run_version, source_id=source_id, input_json=run_inputs, created_by=user.id)
    db.add(run)
    db.commit()
    db.refresh(run)
    if payload.synchronous:
        await execute_run(run.id)
        db.refresh(run)
    else:
        enqueue_run(run.id)
    return run


@router.post("/{workflow_id}/run-all", response_model=list[RunOut], status_code=201)
async def run_workflow_for_all_sources(
    workflow_id: str,
    payload: RunRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER", "OPERATOR")),
) -> list[Run]:
    """Queue one independent run for every public, enabled source of the workflow project.

    A batch is deliberately represented as normal runs: it preserves per-site
    raw artifacts, failures and retry controls while writing all results into
    the workflow's single output dataset.
    """
    workflow = db.get(Workflow, workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if payload.use_published and not workflow.published_version:
        raise HTTPException(status_code=422, detail="Published workflow is required")
    try:
        zone = ZoneInfo(payload.timezone)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Invalid timezone") from exc
    clock = datetime.now(zone)
    if payload.run_clock_mode == "manual":
        if payload.run_at is None:
            raise HTTPException(status_code=422, detail="Manual run requires a date and time")
        clock = payload.run_at.replace(tzinfo=zone) if payload.run_at.tzinfo is None else payload.run_at.astimezone(zone)
    sources = list(db.scalars(select(Source).where(Source.project_id == workflow.project_id, Source.enabled.is_(True))).all())
    sources = [source for source in sources if (source.settings or {}).get("access_status", "PUBLIC") == "PUBLIC"]
    if not sources:
        raise HTTPException(status_code=422, detail="No public enabled sources in this project")
    run_version = workflow.published_version if payload.use_published else workflow.version
    runs = [
        Run(
            workflow_id=workflow.id,
            workflow_version=run_version,
            source_id=source.id,
            input_json={**payload.inputs, "batch": True, "_run_clock": {"mode": payload.run_clock_mode, "timezone": payload.timezone, "effective": clock.isoformat()}},
            created_by=user.id,
        )
        for source in sources
    ]
    db.add_all(runs)
    db.commit()
    for run in runs:
        db.refresh(run)
        enqueue_run(run.id)
    return runs


def enqueue_run(run_id: str) -> None:
    from celery import Celery
    celery = Celery("parser_studio_client", broker=settings.redis_url)
    db = SessionLocal()
    try:
        run = db.get(Run, run_id)
        workflow = db.get(Workflow, run.workflow_id) if run else None
        graph = active_graph(db, workflow, run.workflow_version) if run and workflow else {}
        source = db.get(Source, run.source_id) if run and run.source_id else None
        queue = queue_for_graph(graph, source)
    finally:
        db.close()
    celery.send_task("parser_studio.execute_run", args=[run_id], queue=queue)
