from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
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
    LLMCall,
    NodeRun,
    RawDocument,
    Record,
    RecordVersion,
    ReviewTask,
    Run,
    Secret,
    Source,
    SourceProfile,
    User,
    Workflow,
    WorkflowVersion,
)
from app.schemas import (
    NodeTestRequest,
    RunOut,
    RunRequest,
    WorkflowCreate,
    WorkflowOut,
    WorkflowTemplateRequest,
    WorkflowUpdate,
)
from app.security import decrypt_secret
from app.services.artifact_storage import ArtifactStorage

router = APIRouter(prefix="/workflows", tags=["Workflows"])
settings = get_settings()


@router.get("/catalog")
def node_catalog(_: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    return NODE_CATALOG


@router.get("", response_model=list[WorkflowOut])
def list_workflows(
    project_id: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[Workflow]:
    stmt = select(Workflow).order_by(Workflow.updated_at.desc())
    if project_id:
        stmt = stmt.where(Workflow.project_id == project_id)
    return list(db.scalars(stmt).all())


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
    version = (workflow.published_version or 0) + 1
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
    # Belinvestbank keeps the product name and detail URL in the same link.
    # Give this stable site markup the dataset-friendly name while preserving
    # the profiler's selector and href extraction.
    for index, field in enumerate(fields):
        selector = str(field.get("selector") or "")
        if field.get("name") == "title" and ("item-description-link" in selector or "deposit_name_" in selector):
            fields[index] = {**field, "name": "product_name"}
    link_field = next((item for item in fields if item.get("name") in {"url", "link", "href"} and item.get("attribute") == "href"), None)
    if link_field and link_field.get("name") != "url":
        link_field = {**link_field, "name": "url"}
        fields = [link_field if item.get("name") in {"link", "href"} else item for item in fields]
    detail = configured.get("detail") if isinstance(configured.get("detail"), dict) else settings.get("detail")
    if not isinstance(detail, dict):
        detail = {}
    if link_field and not detail.get("table"):
        # Detail pages contain the published rate/term/currency table.  The
        # node tolerates pages without a table, but when one exists it exposes
        # stable aliases (rate/term/currency) alongside raw headings.
        detail = {
            **detail,
            "table": {"selector": "table", "header_row": 0, "normalize_fields": True},
            "field_names": ["rate", "term", "currency"],
        }
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
        return {"enabled": False, "created": 0, "updated": 0, "unchanged": 0, "review_tasks": 0, "warning": "Dataset не найден"}
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
        return {"enabled": True, "created": 0, "updated": 0, "unchanged": 0, "review_tasks": 0, "warning": "Output records is not a list"}
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
    if dataset.schema_id:
        schema = db.get(DataSchema, dataset.schema_id)
        schema_errors: list[dict[str, Any]] = []
        if schema:
            for index, item in enumerate(payload):
                try:
                    validate_json_schema(item, schema.schema_json)
                except ValueError as exc:
                    schema_errors.append({"row": index, "code": "SCHEMA", "message": str(exc)})
        if schema_errors:
            return {"enabled": True, "created": 0, "updated": 0, "unchanged": 0, "review_tasks": 0,
                    "blocked": True, "validation_errors": schema_errors}
    review_policy = output_config.get("review_policy") or graph_settings.get("review_policy") or dataset.review_policy or {"new": False, "changed": False, "confidence_below": 0.0}
    counters = {"enabled": True, "created": 0, "updated": 0, "unchanged": 0, "review_tasks": 0}
    for item in payload:
        if not isinstance(item, dict):
            continue
        natural_key = "|".join(str(item.get(key, "")) for key in key_fields) if key_fields else hashlib.sha256(json.dumps(item, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
        data_hash = stable_record_hash(item)
        confidence = float(item.get("confidence", 1.0) or 0)
        record = db.scalar(select(Record).where(Record.dataset_id == dataset_id, Record.natural_key == natural_key))
        if record is None:
            requires_review = bool(review_policy.get("new", True) or item.get("requires_review") or confidence < float(review_policy.get("confidence_below", 0.8)))
            record = Record(dataset_id=dataset_id, natural_key=natural_key, current_version=1, data_json=item, data_hash=data_hash, confidence=confidence, review_status="PENDING" if requires_review else "APPROVED")
            db.add(record)
            db.flush()
            db.add(RecordVersion(record_id=record.id, run_id=run.id, version_number=1, data_json=item, data_hash=data_hash, confidence=confidence, review_status=record.review_status))
            if requires_review:
                db.add(ReviewTask(project_id=workflow.project_id, record_id=record.id, run_id=run.id, reason="NEW_RECORD", old_data={}, new_data=item, evidence=evidence_from_item(item)))
                counters["review_tasks"] += 1
            counters["created"] += 1
            continue
        if record.data_hash == data_hash:
            counters["unchanged"] += 1
            continue
        requires_review = bool(review_policy.get("changed", True) or item.get("requires_review") or confidence < float(review_policy.get("confidence_below", 0.8)))
        next_version = record.current_version + 1
        db.add(RecordVersion(record_id=record.id, run_id=run.id, version_number=next_version, data_json=item, data_hash=data_hash, confidence=confidence, review_status="PENDING" if requires_review else "APPROVED"))
        if requires_review:
            db.add(ReviewTask(project_id=workflow.project_id, record_id=record.id, run_id=run.id, reason="CHANGED_RECORD", old_data=record.data_json, new_data=item, evidence=evidence_from_item(item)))
            counters["review_tasks"] += 1
        else:
            record.data_json = item
            record.data_hash = data_hash
            record.current_version = next_version
            record.confidence = confidence
            record.review_status = "APPROVED"
        counters["updated"] += 1
    return counters


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
    volatile = {"observed_at", "evidence", "raw_artifact", "status_code", "artifacts"}
    comparable = {key: value for key, value in item.items() if key not in volatile}
    return hashlib.sha256(json.dumps(comparable, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def active_graph(db: Session, workflow: Workflow, workflow_version: int) -> dict[str, Any]:
    version = db.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_id == workflow.id, WorkflowVersion.version == workflow_version))
    return version.graph_json if version else workflow.graph_json


def build_execution_variables(db: Session, source: Source | None) -> tuple[dict[str, Any], dict[str, str]]:
    variables: dict[str, Any] = {
        "source": {
            "id": source.id if source else None,
            "url": source.entry_url if source else "",
            "base_url": source.base_url if source else "",
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
        persistence = persist_result(db, workflow, run, result)
        run.output_json = {**result, "persistence": persistence}
        run.status = determine_run_status(graph, result, persistence)
        run.finished_at = datetime.now(UTC)
        db.commit()
    except Exception as exc:
        if run:
            run.status = "FAILED"
            run.error_json = {"code": "WORKFLOW_ERROR", "message": str(exc)}
            run.finished_at = datetime.now(UTC)
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


def enqueue_run(run_id: str) -> None:
    from celery import Celery
    celery = Celery("parser_studio_client", broker=settings.redis_url)
    db = SessionLocal()
    try:
        run = db.get(Run, run_id)
        workflow = db.get(Workflow, run.workflow_id) if run else None
        graph = active_graph(db, workflow, run.workflow_version) if run and workflow else {}
        node_types = {node.get("type") or node.get("data", {}).get("type") for node in graph.get("nodes", [])}
        queue = "browser" if "browser_open" in node_types else "documents" if node_types & {"parse_document", "download_file"} else "llm" if node_types & {"llm_extract", "llm_classify"} else "exports" if "export_file" in node_types else "default"
    finally:
        db.close()
    celery.send_task("parser_studio.execute_run", args=[run_id], queue=queue)
