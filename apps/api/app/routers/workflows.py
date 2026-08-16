from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from workflow_engine import (
    NODE_CATALOG,
    PUBLIC_PHASES,
    WorkflowEngine,
    compile_executable_plan,
    standard_v2_graph,
    validate_dag,
)
from workflow_engine.nodes import validate_json_schema
from workflow_engine.types import (
    ExecutionContext,
    RunCancelledError,
    RunDeadlineExceededError,
    RunLeaseLostError,
)

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
from app.services.authorization import (
    require_project,
    require_project_object,
    require_same_project,
    scope_to_projects,
)
from app.services.run_lifecycle import (
    claim_run,
    finalize_owned_run,
    mark_cancelled_if_owned,
    should_stop_run,
)
from app.services.run_routing import queue_for_graph

router = APIRouter(prefix="/workflows", tags=["Workflows"])
settings = get_settings()


def assert_graph_capability_bindings(db: Session, project_id: str, graph: dict[str, Any]) -> None:
    """Reject literal credentials and foreign project capability references."""
    graph_text = json.dumps(graph, ensure_ascii=False)
    forbidden = ("encrypted_api_key", "encrypted_password", "encrypted_value", "storage_state")
    if any(f'"{key}"' in graph_text for key in forbidden):
        raise HTTPException(status_code=422, detail="Workflow configuration must reference project capabilities, not embed credentials")
    for node in graph.get("nodes", []):
        config = node.get("config") or node.get("data", {}).get("config", {})
        node_type = node.get("type") or node.get("data", {}).get("type")
        if not isinstance(config, dict):
            continue
        if node_type == "save_external_db" and config.get("connection"):
            connection = db.scalar(select(DatabaseConnection).where(DatabaseConnection.project_id == project_id, DatabaseConnection.name == str(config["connection"])))
            if not connection:
                raise HTTPException(status_code=422, detail="Database connection is not available in this project")
        if node_type in {"llm_extract", "llm_classify"} and str(config.get("provider") or "deepseek") != "mock":
            provider = db.scalar(select(AIProviderConfig).where(AIProviderConfig.project_id == project_id, AIProviderConfig.provider_name == str(config.get("provider") or "deepseek"), AIProviderConfig.enabled.is_(True)))
            if not provider:
                raise HTTPException(status_code=422, detail="AI provider is not available in this project")
        refs = secret_template_references(config)
        if refs:
            allowed_nodes = {"http_request", "download_file", "follow_links", "crawl_links"}
            if node_type not in allowed_nodes or any(path.split(".", 1)[0] not in {"headers", "cookies"} for _, path in refs):
                raise HTTPException(status_code=422, detail="Secrets may be used only in HTTP authentication headers or cookies")
            names = {name for name, _ in refs}
            found = set(db.scalars(select(Secret.name).where(Secret.project_id == project_id, Secret.name.in_(names))).all())
            if found != names:
                raise HTTPException(status_code=422, detail="Referenced secret is not available in this project")


def secret_template_references(value: Any, path: str = "") -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(name, path) for name in re.findall(r"{{\s*secret\.([A-Za-z_][A-Za-z0-9_]*)\s*}}", value)]
    if isinstance(value, list):
        return [item for index, child in enumerate(value) for item in secret_template_references(child, f"{path}.{index}".strip("."))]
    if isinstance(value, dict):
        return [item for key, child in value.items() for item in secret_template_references(child, f"{path}.{key}".strip("."))]
    return []


@router.get("/catalog")
def node_catalog(_: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    return NODE_CATALOG


@router.get("/v2-skeleton")
def v2_skeleton(_: User = Depends(get_current_user)) -> dict[str, Any]:
    """Expose the fixed public canvas for guided v2 authoring clients."""

    return {
        "contractVersion": 2,
        "roles": PUBLIC_PHASES,
        "graph": standard_v2_graph(),
    }


@router.get("", response_model=list[WorkflowOut])
def list_workflows(
    project_id: str | None = None,
    include_legacy: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[Workflow]:
    if project_id:
        require_project(db, user, project_id)
    stmt = select(Workflow).order_by(Workflow.updated_at.desc())
    if project_id:
        stmt = stmt.where(Workflow.project_id == project_id)
    workflows = list(db.scalars(scope_to_projects(stmt, Workflow.project_id, db, user)).all())
    return workflows


@router.post("", response_model=WorkflowOut, status_code=201)
def create_workflow(
    payload: WorkflowCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER")),
) -> Workflow:
    require_project(db, user, payload.project_id)
    assert_graph_capability_bindings(db, payload.project_id, payload.graph_json)
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
    require_project(db, user, payload.project_id)
    graph = deepcopy(payload.graph_json)
    graph.setdefault("settings", {}).pop("source_id", None)
    errors = validate_dag(graph)
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    assert_graph_capability_bindings(db, payload.project_id, graph)
    workflow = Workflow(project_id=payload.project_id, name=payload.name, description=payload.description, graph_json=graph)
    db.add(workflow); db.flush()
    audit(db, user.id, "IMPORT", "workflow", workflow.id, after={"name": workflow.name})
    db.commit(); db.refresh(workflow)
    return workflow


@router.get("/{workflow_id}/export")
def export_workflow(workflow_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Response:
    workflow = require_project_object(db, user, Workflow, workflow_id, label="Workflow")
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
    source = require_project_object(db, user, Source, payload.source_id, label="Source")
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
    user: User = Depends(get_current_user),
) -> Workflow:
    workflow = require_project_object(db, user, Workflow, workflow_id, label="Workflow")
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
    workflow = require_project_object(db, user, Workflow, workflow_id, label="Workflow")
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow не найден")
    if payload.graph_json is not None:
        assert_graph_capability_bindings(db, workflow.project_id, payload.graph_json)
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
    user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER")),
) -> dict[str, Any]:
    workflow = require_project_object(db, user, Workflow, workflow_id, label="Workflow")
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow не найден")
    assert_graph_capability_bindings(db, workflow.project_id, workflow.graph_json)
    errors = validate_dag(workflow.graph_json)
    known = {item["type"] for item in NODE_CATALOG}
    for node in workflow.graph_json.get("nodes", []):
        node_type = node.get("type") or node.get("data", {}).get("type")
        if node_type not in known:
            errors.append(f"Неизвестный тип узла: {node_type}")
    return {
        "valid": not errors,
        "errors": errors,
        "contract_version": workflow.graph_json.get(
            "contractVersion", (workflow.graph_json.get("settings") or {}).get("contractVersion", 1)
        ),
        "public_phases": [
            (item.get("type") or item.get("data", {}).get("type"))
            for item in workflow.graph_json.get("nodes", [])
        ],
    }


@router.post("/{workflow_id}/publish", response_model=WorkflowOut)
def publish(
    workflow_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER")),
) -> Workflow:
    workflow = require_project_object(db, user, Workflow, workflow_id, label="Workflow")
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow не найден")
    errors = validate_dag(workflow.graph_json)
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    assert_graph_capability_bindings(db, workflow.project_id, workflow.graph_json)
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
    source = require_project_object(db, user, Source, payload.source_id, label="Source") if payload.source_id else None
    if source:
        assert_graph_capability_bindings(db, source.project_id, graph)
    elif secret_template_references(graph):
        raise HTTPException(status_code=422, detail="Node test with secrets requires a project source")
    variables, secrets, capabilities = build_execution_variables(db, source, graph)
    context = ExecutionContext(
        run_id="node-test",
        project_id=source.project_id if source else "test",
        workflow_version_id="test",
        user_id=user.id,
        variables={**variables, **payload.inputs},
        secrets=secrets,
        capabilities=capabilities,
        artifact_storage=ArtifactStorage(),
    )
    result = await WorkflowEngine().execute(graph, context, payload.inputs)
    return compact_node_test_result(result) if payload.response_preview else result


def compact_node_test_result(result: dict[str, Any]) -> dict[str, Any]:
    """Return UI-safe node-test evidence without sending rendered pages twice.

    Node tests retain raw pages as artifacts.  Sending every raw HTML page in
    the synchronous JSON response makes the browser inspector unresponsive on
    normal public listings, so the UI receives a bounded diagnostic preview
    while records, counts, URLs and artifact references stay intact.
    """
    def compact(value: Any, depth: int = 0) -> Any:
        if depth >= 6:
            return "… nested value omitted from preview"
        if isinstance(value, str):
            if len(value) > 2_000:
                return f"{value[:2_000]}\n… preview truncated ({len(value):,} characters total); use retained artifact for raw content."
            return value
        if isinstance(value, list):
            items = [compact(item, depth + 1) for item in value[:25]]
            if len(value) > 25:
                items.append({"_preview": f"{len(value) - 25} more items omitted"})
            return items
        if isinstance(value, dict):
            item = {str(key): compact(child, depth + 1) for key, child in list(value.items())[:50]}
            if len(value) > 50:
                item["_preview"] = f"{len(value) - 50} more keys omitted"
            return item
        return value

    # The full upstream envelopes are useful to the engine but exceptionally
    # noisy in the editor (the Source object can include profiler evidence
    # such as XHR payloads).  Keep a compact node-by-node inspection surface;
    # JSONPath still receives a bounded upstream ``body`` preview.
    def node_preview(output: Any) -> dict[str, Any]:
        if not isinstance(output, dict):
            return {"value": compact(output)}
        records = output.get("records")
        if isinstance(records, list):
            # Detail traversal places the full fetched response in every
            # record for the following Extract phase.  Raw files/pages are
            # already retained as artifacts; duplicating them in the node-test
            # response is neither useful nor responsive in the editor.
            preview_records: list[Any] = []
            for row in records[:25]:
                if not isinstance(row, dict):
                    preview_records.append(compact(row))
                    continue
                preview = {
                    str(key): compact(value)
                    for key, value in row.items()
                    if key not in {"body", "html", "content_base64"}
                }
                if any(key in row for key in {"body", "html", "content_base64"}):
                    preview["_raw_content"] = "retained as an artifact; omitted from node-test preview"
                preview_records.append(preview)
            if len(records) > 25:
                preview_records.append({"_preview": f"{len(records) - 25} more items omitted"})
        else:
            preview_records = compact(records) if records is not None else None
        pages = output.get("pages")
        if isinstance(pages, list):
            preview_pages = [
                {
                    key: compact(page[key])
                    for key in ("url", "state", "origin", "status", "content_type", "artifacts")
                    if isinstance(page, dict) and key in page
                }
                for page in pages[:25]
            ]
            if len(pages) > 25:
                preview_pages.append({"_preview": f"{len(pages) - 25} more pages omitted"})
        else:
            preview_pages = None
        visible_keys = (
            "url", "title", "content_type", "count", "partial", "errors",
            "body", "text", "traversal",
            "artifacts", "_contract", "_adaptive_attempts",
        )
        preview = {key: compact(output[key]) for key in visible_keys if key in output}
        if records is not None:
            preview["records"] = preview_records
        if pages is not None:
            preview["pages"] = preview_pages
        return preview

    return {
        "node_outputs": {
            str(node_id): node_preview(output)
            for node_id, output in (result.get("node_outputs") or {}).items()
        },
        "result": node_preview(result.get("result")),
        "result_node_id": result.get("result_node_id"),
        "skipped_nodes": compact(result.get("skipped_nodes") or []),
        "artifacts": compact(result.get("artifacts") or []),
        "logs": compact(result.get("logs") or []),
    }


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
        "contractVersion": payload.graph.get("contractVersion", payload.graph.get("settings", {}).get("contractVersion", 1)),
        "settings": payload.graph.get("settings", {}),
        "nodes": [node for node in payload.graph.get("nodes", []) if str(node.get("id")) in included],
        "edges": [
            edge for edge in payload.graph.get("edges", [])
            if str(edge.get("source")) in included and str(edge.get("target")) in included
        ],
    }


def build_source_template(source: Source, template: str) -> dict[str, Any]:
    """Create the portable seven-phase starter for a newly selected Source.

    Source profiling remains advisory: a site-specific selector is never
    copied into the graph without an operator decision in the v2 editor.
    This endpoint is retained for older clients, but it no longer creates a
    legacy chain with hidden site-dependent behaviour.
    """

    del template
    graph = standard_v2_graph(settings={
        "source_id": source.id,
        "review_policy": {"new": True, "changed": True, "confidence_below": 0.8},
    })
    nodes = {node["id"]: node for node in graph["nodes"]}
    common = {
        "contractVersion": 2,
        "mode": "AUTO",
        "budgets": {"maxRequests": 50, "maxBytes": 20_000_000, "maxPages": 25, "maxItems": 500, "deadlineSeconds": 600},
        "successCriteria": [],
        "errorPolicy": "FAIL_REQUIRED_SCOPE",
        "evidencePolicy": {"retainRaw": True, "retainAttempts": True},
    }
    def configure(node_id: str, *, goal: str, allow: list[str], prefer: list[str] | None = None, **config: Any) -> None:
        nodes[node_id]["config"] = {
            **common,
            "goal": goal,
            "strategies": {"allow": allow, "deny": [], "prefer": prefer or [], "fallbackPolicy": "ON_POSTCONDITION_FAILURE"},
            **config,
        }

    if source.fetch_mode == "DOCUMENT":
        configure("acquire", goal="Скачать публичный документ", allow=["acquire-file"], prefer=["acquire-file"], url="{{source.url}}", timeout=60)
        configure("traverse", goal="Передать документ на извлечение", allow=["traverse-links"], pagination={"enabled": False}, detail={"enabled": False})
        configure("extract", goal="Извлечь записи из документа", allow=["extract-document"], document={"inputPath": "content_base64", "filenamePath": "filename", "header_row": 0, "ocr": False})
    elif source.fetch_mode == "XHR_JSON":
        request = source.settings.get("http_request") if isinstance(source.settings, dict) and isinstance(source.settings.get("http_request"), dict) else {}
        configure("acquire", goal="Получить публичное JSON-представление", allow=["acquire-api", "acquire-browser-xhr"], prefer=["acquire-api"], url="{{source.url}}", timeout=45, endpoint="", xhr={"urlContains": "", "path": ""}, **{key: value for key, value in request.items() if key in {"method", "headers", "query_params", "json_body", "timeout"}})
        configure("traverse", goal="Передать JSON на извлечение", allow=["traverse-links"], pagination={"enabled": False, "mode": "next", "maxPages": 25}, detail={"enabled": False})
        configure("extract", goal="Извлечь JSON-записи", allow=["extract-json"], json={"inputPath": "body", "path": "$.items[*]"})
    else:
        browser = source.fetch_mode == "PLAYWRIGHT"
        configure("acquire", goal="Получить публичное представление источника", allow=["acquire-browser"] if browser else ["acquire-http", "acquire-browser"], prefer=["acquire-browser"] if browser else ["acquire-http"], url="{{source.url}}", method="GET", timeout=45)
        configure("traverse", goal="Передать HTML на извлечение", allow=["traverse-links"], pagination={"enabled": False, "mode": "next", "maxPages": 25}, detail={"enabled": False})
        configure("extract", goal="Извлечь HTML-карточки", allow=["extract-dom"], dom={"inputPath": "body", "itemSelector": "", "fields": []})
    configure("start", goal="Контекст ручного запуска", allow=["start-input"])
    configure("process", goal="Нормализовать записи", allow=["process-operations"], input_path="records", operations=[], identityFields=[])
    configure("assure", goal="Проверить полноту результата", allow=["assure-validation"], input_path="records", required=[], schema={}, fail_on_error=False, expectedScope={"allowEmpty": False, "requireComplete": False})
    configure("output", goal="Сохранить явно извлечённые записи", allow=["output-dataset"], input_path="records", natural_key_fields=["url"], on_empty="warning", name="records")
    return graph


_ROW_IDENTITY_FIELDS = ("page_url", "table_id", "row_index")


def _record_natural_key(item: dict[str, Any], key_fields: list[str]) -> str | None:
    """Resolve a record's natural key with the structural row fallback.

    When declared identity fields have no value on the record, the key is
    completed from the row identity emitted by table/card extraction
    (``page_url``, ``table_id``, ``row_index``) plus any declared fields that
    are present.  Distinct rows therefore never collapse into one key, while
    fully mapped records keep their business identity (and dedupe) intact.
    """

    present = [key for key in key_fields if item.get(key) not in (None, "")]
    if len(present) == len(key_fields):
        return "|".join(str(item.get(key, "")) for key in key_fields)
    fallback = [str(item.get(key)) for key in _ROW_IDENTITY_FIELDS if item.get(key) not in (None, "")]
    if not fallback:
        return None
    return "|".join([str(item.get(key)) for key in present] + fallback)


def _natural_key_rows(payload: list[Any], key_fields: list[str]) -> list[tuple[dict[str, Any], str, list[str]]]:
    rows: list[tuple[dict[str, Any], str, list[str]]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        natural_key = _record_natural_key(item, key_fields)
        if natural_key is None:
            missing = [key for key in key_fields if item.get(key) in (None, "")]
        else:
            missing = []
        rows.append((item, natural_key or "", missing))
    return rows


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
    missing_keys = [{"row": index, "missing": missing}
                    for index, (_row, _key, missing) in enumerate(_natural_key_rows(payload, key_fields)) if missing]
    if missing_keys:
        return {"enabled": True, "created": 0, "updated": 0, "unchanged": 0, "review_tasks": 0,
                "blocked": True, "validation_errors": missing_keys}
    natural_key_rows: dict[str, list[int]] = {}
    for index, (_row, natural_key, _missing) in enumerate(_natural_key_rows(payload, key_fields)):
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
        natural_key = _record_natural_key(item, key_fields) if key_fields else hashlib.sha256(json.dumps(item, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
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
        evidence=evidence_from_item(item),
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
        if not key.startswith("__") and key not in {"raw_artifact", "raw_document_id", "evidence"}
    }


def active_graph(db: Session, workflow: Workflow, workflow_version: int) -> dict[str, Any]:
    version = db.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_id == workflow.id, WorkflowVersion.version == workflow_version))
    return version.graph_json if version else workflow.graph_json


def build_execution_variables(
    db: Session,
    source: Source | None,
    graph: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
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
    }
    secrets: dict[str, str] = {"_CRAWL_RESUME_SECRET": settings.app_secret_key}
    capabilities: dict[str, Any] = {"ai_providers": {}, "database_connections": {}}
    project_id = source.project_id if source else None
    secret_names = {name for name, _ in secret_template_references(graph or {})}
    connection_names = {
        str(node.get("config", {}).get("connection"))
        for node in (graph or {}).get("nodes", [])
        if (node.get("type") or node.get("data", {}).get("type")) == "save_external_db"
        and str(node.get("config", {}).get("connection") or "")
    }
    provider_names = {
        str(node.get("config", {}).get("provider") or "deepseek")
        for node in (graph or {}).get("nodes", [])
        if (node.get("type") or node.get("data", {}).get("type")) in {"llm_extract", "llm_classify"}
    }
    if project_id:
        for secret in db.scalars(select(Secret).where(Secret.project_id == project_id, Secret.name.in_(secret_names))).all():
            secrets[secret.name] = decrypt_secret(secret.encrypted_value)
    if settings.deepseek_api_key and not project_id:
        secrets.setdefault("DEEPSEEK_API_KEY", settings.deepseek_api_key)
    from app.routers.settings import connection_url
    if project_id:
        connections = db.scalars(select(DatabaseConnection).where(DatabaseConnection.project_id == project_id, DatabaseConnection.enabled.is_(True), DatabaseConnection.name.in_(connection_names))).all()
    else:
        connections = []
    for connection in connections:
        password = decrypt_secret(connection.encrypted_password) if connection.encrypted_password else ""
        if password:
            secrets[f"_CONNECTION_{connection.id}_PASSWORD"] = password
        connection_string = connection_url(connection)
        secrets[f"_CONNECTION_{connection.id}_URL"] = connection_string
        capabilities["database_connections"][connection.name] = {
            "url": connection_string,
            "engine": connection.engine,
            "schema": connection.schema_name,
            "allowed_tables": connection.allowed_tables,
        }
    if project_id:
        providers = db.scalars(select(AIProviderConfig).where(AIProviderConfig.project_id == project_id, AIProviderConfig.enabled.is_(True), AIProviderConfig.provider_name.in_(provider_names))).all()
    else:
        providers = []
    for provider in providers:
        api_key = decrypt_secret(provider.encrypted_api_key) if provider.encrypted_api_key else ""
        capabilities["ai_providers"][provider.provider_name] = {
            "base_url": provider.base_url,
            "default_model": provider.default_model,
            "timeout": provider.timeout,
        }
        if api_key:
            secrets[f"AI_PROVIDER_{provider.provider_name}"] = api_key
    if source and source.settings.get("browser_profile_id"):
        profile = db.get(BrowserProfile, source.settings["browser_profile_id"])
        if profile is None or profile.project_id != source.project_id or not profile.enabled:
            raise ValueError("Configured browser profile is unavailable in this project")
        if profile:
            storage_state: dict[str, Any] | None = None
            if profile.encrypted_storage_state:
                try:
                    decoded = json.loads(decrypt_secret(profile.encrypted_storage_state))
                    storage_state = decoded if isinstance(decoded, dict) else None
                except (ValueError, TypeError, json.JSONDecodeError):
                    storage_state = None
            for value in nested_strings({"storage_state": storage_state, "proxy": profile.proxy}):
                secrets[f"_BROWSER_{profile.id}_{len(secrets)}"] = value
            capabilities["browser_profile"] = {"viewport": profile.viewport, "locale": profile.locale, "timezone": profile.timezone, "user_agent": profile.user_agent, "proxy": profile.proxy, "storage_state": storage_state}
    return variables, secrets, capabilities


def nested_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for child in value for item in nested_strings(child)]
    if isinstance(value, dict):
        return [item for child in value.values() for item in nested_strings(child)]
    return []


async def execute_run(run_id: str) -> None:
    db = SessionLocal()
    lease_token: str | None = None
    secrets: dict[str, str] = {}
    try:
        lease_token = claim_run(db, run_id)
        if lease_token is None:
            return
        run = db.get(Run, run_id)
        workflow = db.get(Workflow, run.workflow_id) if run else None
        if not run or not workflow:
            return
        source = db.get(Source, run.source_id) if run.source_id else None
        graph = active_graph(db, workflow, run.workflow_version)
        variables, secrets, capabilities = build_execution_variables(db, source, graph)
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
            capabilities=capabilities,
            artifact_storage=ArtifactStorage(),
            effective_run_clock=effective_clock,
            deadline_at=run.deadline_at,
            heartbeat_interval_seconds=max(0.2, float(settings.run_heartbeat_interval_seconds)),
            executable_plan=run.executable_plan_json or None,
        )

        async def stop_check() -> str | None:
            return should_stop_run(db, run_id, lease_token)

        context.stop_check = stop_check

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
        stop_reason = should_stop_run(db, run_id, lease_token)
        if stop_reason == "CANCELLED":
            raise RunCancelledError("Run cancellation was requested")
        if stop_reason == "DEADLINE_EXCEEDED":
            raise RunDeadlineExceededError("Run deadline was exceeded")
        if stop_reason == "LEASE_LOST":
            raise RunLeaseLostError("Run lease was lost")
        for artifact in context.artifacts:
            if artifact.get("storage_key"):
                db.add(RawDocument(run_id=run.id, source_id=run.source_id, url=artifact.get("url", ""), content_type=artifact.get("content_type", ""), sha256=artifact.get("sha256", ""), storage_key=artifact["storage_key"], metadata_json={key: value for key, value in artifact.items() if key != "storage_key"}))
        db.flush()
        persistence = persist_result(db, workflow, run, result)
        output_json = {**result, "persistence": persistence}
        error_json: dict[str, Any] | None = None
        if persistence.get("blocked"):
            error_json = {
                "code": "PERSISTENCE_BLOCKED",
                "message": persistence.get("warning") or "Dataset persistence validation failed",
                "details": persistence.get("validation_errors") or [],
            }
        if not finalize_owned_run(
            db,
            run_id,
            lease_token,
            status=determine_run_status(graph, result, persistence),
            output_json=output_json,
            error_json=error_json,
            record_counts={
                "created": int(persistence.get("created") or 0),
                "updated": int(persistence.get("updated") or 0),
                "unchanged": int(persistence.get("unchanged") or 0),
            },
        ):
            # The competing cancellation transition wins.  Rolling back also
            # prevents a half-completed persistence transaction from escaping.
            db.rollback()
            mark_cancelled_if_owned(db, run_id, lease_token)
        db.commit()
    except RunCancelledError:
        db.rollback()
        if lease_token:
            mark_cancelled_if_owned(db, run_id, lease_token)
            db.commit()
    except RunDeadlineExceededError:
        db.rollback()
        if lease_token:
            if finalize_owned_run(
                db,
                run_id,
                lease_token,
                status="TIMED_OUT",
                error_json={"code": "RUN_DEADLINE_EXCEEDED", "message": "Run deadline was exceeded"},
            ):
                db.commit()
            else:
                db.rollback()
                mark_cancelled_if_owned(db, run_id, lease_token)
                db.commit()
    except RunLeaseLostError:
        db.rollback()
    except Exception as exc:
        db.rollback()
        if lease_token:
            from workflow_engine.redaction import redact_value

            error_json = redact_value(
                {"code": "WORKFLOW_ERROR", "message": str(exc)},
                list(secrets.values()),
            )
            if finalize_owned_run(db, run_id, lease_token, status="FAILED", error_json=error_json):
                db.commit()
            else:
                db.rollback()
                mark_cancelled_if_owned(db, run_id, lease_token)
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
    workflow = require_project_object(db, user, Workflow, workflow_id, label="Workflow")
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow не найден")
    source_id = payload.source_id or workflow.graph_json.get("settings", {}).get("source_id")
    if source_id:
        source = require_project_object(db, user, Source, source_id, label="Source")
        require_same_project(workflow.project_id, source)
    if source_id and not source:  # pragma: no cover - defensive invariant
        raise HTTPException(status_code=404, detail="Источник не найден")
    # A user-triggered run must validate exactly what is on the canvas after
    # Save.  Background schedules still create their runs from the published
    # version in the worker, so drafts cannot silently alter scheduled jobs.
    if payload.use_published and not workflow.published_version:
        raise HTTPException(status_code=422, detail="Опубликованная версия отсутствует: сначала нажмите «Опубликовать»")
    run_version = workflow.published_version if payload.use_published else workflow.version
    graph = active_graph(db, workflow, run_version)
    assert_graph_capability_bindings(db, workflow.project_id, graph)
    errors = validate_dag(graph)
    if errors:
        raise HTTPException(status_code=422, detail=errors)
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
    requested_deadline = payload.deadline_seconds or settings.run_default_deadline_seconds
    if not 1 <= requested_deadline <= settings.run_max_deadline_seconds:
        raise HTTPException(status_code=422, detail="deadline_seconds is outside the allowed run budget")
    plan = compile_executable_plan(
        graph,
        project_id=workflow.project_id,
        workflow_id=workflow.id,
        workflow_version=run_version,
        source_id=source_id,
        revision_refs=graph.get("settings", {}).get("presetRefs", {}),
    )
    run = Run(
        workflow_id=workflow.id,
        workflow_version=run_version,
        source_id=source_id,
        input_json=run_inputs,
        created_by=user.id,
        deadline_at=datetime.now(UTC) + timedelta(seconds=requested_deadline),
        executable_plan_json=plan.as_dict(),
    )
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
    workflow = require_project_object(db, user, Workflow, workflow_id, label="Workflow")
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
    graph = active_graph(db, workflow, run_version)
    assert_graph_capability_bindings(db, workflow.project_id, graph)
    errors = validate_dag(graph)
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    requested_deadline = payload.deadline_seconds or settings.run_default_deadline_seconds
    if not 1 <= requested_deadline <= settings.run_max_deadline_seconds:
        raise HTTPException(status_code=422, detail="deadline_seconds is outside the allowed run budget")
    plan_by_source = {
        source.id: compile_executable_plan(
            graph,
            project_id=workflow.project_id,
            workflow_id=workflow.id,
            workflow_version=run_version,
            source_id=source.id,
            revision_refs=graph.get("settings", {}).get("presetRefs", {}),
        ).as_dict()
        for source in sources
    }
    runs = [
        Run(
            workflow_id=workflow.id,
            workflow_version=run_version,
            source_id=source.id,
            input_json={**payload.inputs, "batch": True, "_run_clock": {"mode": payload.run_clock_mode, "timezone": payload.timezone, "effective": clock.isoformat()}},
            created_by=user.id,
            deadline_at=datetime.now(UTC) + timedelta(seconds=requested_deadline),
            executable_plan_json=plan_by_source[source.id],
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
