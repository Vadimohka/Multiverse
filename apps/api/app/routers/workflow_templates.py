from __future__ import annotations

from copy import deepcopy
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit
from app.database import get_db
from app.dependencies import get_current_user, require_roles
from app.models import User, Workflow, WorkflowTemplate
from app.schemas import (
    WorkflowOut,
    WorkflowTemplateCreate,
    WorkflowTemplateFromWorkflowRequest,
    WorkflowTemplateInstantiateRequest,
    WorkflowTemplateOut,
    WorkflowTemplateUpdate,
)
from workflow_engine import validate_dag

router = APIRouter(prefix="/workflow-templates", tags=["Workflow templates"])


def _graph(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Any]:
    return {"version": 1, "settings": {"review_policy": {"new": True, "changed": True, "confidence_below": 0.8}}, "nodes": nodes, "edges": edges}


# These are source- and dataset-independent starter blueprints.  They stay in
# code so every project receives the same reviewed starting point, while user
# templates below are immutable snapshots stored in the database.
SYSTEM_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "system-web-page",
        "name": "Веб-страница: таблицы и карточки",
        "description": "HTTP → HTML → повторяющиеся карточки → нормализация → dataset. Укажите CSS-селекторы после создания.",
        "tags": ["web", "html", "starter"],
        "is_system": True,
        "graph_json": _graph(
            [
                {"id": "trigger", "type": "manual_trigger", "position": {"x": 20, "y": 160}, "config": {}},
                {"id": "fetch", "type": "http_request", "position": {"x": 220, "y": 160}, "config": {"url": "{{source.url}}", "method": "GET", "timeout": 45}},
                {"id": "parse", "type": "parse_html", "position": {"x": 430, "y": 160}, "config": {"input_path": "body"}},
                {"id": "extract", "type": "extract_repeating_list", "position": {"x": 650, "y": 160}, "config": {"input_path": "html", "container_selector": "", "fields": []}},
                {"id": "transform", "type": "transform", "position": {"x": 880, "y": 160}, "config": {"input_path": "records", "operations": []}},
                {"id": "mapping", "type": "mapping", "position": {"x": 1080, "y": 160}, "config": {"input_path": "records", "fields": []}},
                {"id": "output", "type": "output", "position": {"x": 1280, "y": 160}, "config": {"input_path": "records", "on_empty": "warning"}},
            ],
            [
                {"id": "e1", "source": "trigger", "target": "fetch"}, {"id": "e2", "source": "fetch", "target": "parse"},
                {"id": "e3", "source": "parse", "target": "extract"}, {"id": "e4", "source": "extract", "target": "transform"},
                {"id": "e5", "source": "transform", "target": "mapping"}, {"id": "e6", "source": "mapping", "target": "output"},
            ],
        ),
    },
    {
        "id": "system-json-api",
        "name": "JSON API / XHR",
        "description": "HTTP API → JSONPath → сопоставление полей → dataset. Для API, которые возвращают JSON.",
        "tags": ["json", "api", "starter"],
        "is_system": True,
        "graph_json": _graph(
            [
                {"id": "trigger", "type": "manual_trigger", "position": {"x": 20, "y": 160}, "config": {}},
                {"id": "fetch", "type": "http_request", "position": {"x": 250, "y": 160}, "config": {"url": "{{source.url}}", "method": "GET", "timeout": 45}},
                {"id": "json", "type": "json_path", "position": {"x": 490, "y": 160}, "config": {"input_path": "body", "path": "$"}},
                {"id": "mapping", "type": "mapping", "position": {"x": 720, "y": 160}, "config": {"input_path": "records", "fields": []}},
                {"id": "output", "type": "output", "position": {"x": 950, "y": 160}, "config": {"input_path": "records", "on_empty": "warning"}},
            ],
            [{"id": "e1", "source": "trigger", "target": "fetch"}, {"id": "e2", "source": "fetch", "target": "json"}, {"id": "e3", "source": "json", "target": "mapping"}, {"id": "e4", "source": "mapping", "target": "output"}],
        ),
    },
    {
        "id": "system-document",
        "name": "Документ: PDF, XLSX, CSV, DOCX",
        "description": "Файл источника → разбор документа → сопоставление полей → dataset.",
        "tags": ["document", "file", "starter"],
        "is_system": True,
        "graph_json": _graph(
            [
                {"id": "trigger", "type": "manual_trigger", "position": {"x": 20, "y": 160}, "config": {}},
                {"id": "fetch", "type": "download_file", "position": {"x": 250, "y": 160}, "config": {"url": "{{source.url}}", "timeout": 60}},
                {"id": "parse", "type": "parse_document", "position": {"x": 480, "y": 160}, "config": {"input_path": "content_base64", "filename_path": "filename"}},
                {"id": "mapping", "type": "mapping", "position": {"x": 720, "y": 160}, "config": {"input_path": "records", "fields": []}},
                {"id": "output", "type": "output", "position": {"x": 950, "y": 160}, "config": {"input_path": "records", "on_empty": "warning"}},
            ],
            [{"id": "e1", "source": "trigger", "target": "fetch"}, {"id": "e2", "source": "fetch", "target": "parse"}, {"id": "e3", "source": "parse", "target": "mapping"}, {"id": "e4", "source": "mapping", "target": "output"}],
        ),
    },
]


def _clean_graph(graph: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(graph)
    settings = result.setdefault("settings", {})
    settings.pop("source_id", None)
    settings.pop("dataset_id", None)
    # Dataset/source bindings may live on output or fetch nodes as well as in
    # graph settings.  A template must be a real blueprint, never a hidden
    # pointer to the data space of the workflow it was saved from.
    for node in result.get("nodes", []):
        config = node.get("config") if isinstance(node, dict) else None
        if isinstance(config, dict):
            config.pop("source_id", None)
            config.pop("dataset_id", None)
    return result


def _system_template(template_id: str) -> dict[str, Any] | None:
    return next((item for item in SYSTEM_TEMPLATES if item["id"] == template_id), None)


def _template_out(item: WorkflowTemplate) -> dict[str, Any]:
    return {
        "id": item.id, "project_id": item.project_id, "name": item.name,
        "description": item.description, "tags": item.tags, "graph_json": item.graph_json,
        "is_system": item.is_builtin, "created_at": item.created_at, "updated_at": item.updated_at,
    }


@router.get("", response_model=list[WorkflowTemplateOut])
def list_templates(project_id: str | None = None, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = [deepcopy(item) for item in SYSTEM_TEMPLATES]
    stmt = select(WorkflowTemplate).order_by(WorkflowTemplate.updated_at.desc())
    # A template is a portable graph snapshot: source and dataset bindings are
    # stripped on save.  Its project is only the ownership/audit context, not
    # an access boundary, so a proven parser can be reused in another project.
    items.extend(_template_out(item) for item in db.scalars(stmt).all())
    return items


@router.post("", response_model=WorkflowTemplateOut, status_code=201)
def create_template(payload: WorkflowTemplateCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> WorkflowTemplate:
    graph = _clean_graph(payload.graph_json)
    errors = validate_dag(graph)
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    item = WorkflowTemplate(**payload.model_dump(exclude={"graph_json"}), graph_json=graph, created_by=user.id)
    db.add(item); db.flush(); audit(db, user.id, "CREATE", "workflow_template", item.id, after={"name": item.name}); db.commit(); db.refresh(item)
    return item


@router.post("/from-workflow/{workflow_id}", response_model=WorkflowTemplateOut, status_code=201)
def save_workflow_as_template(workflow_id: str, payload: WorkflowTemplateFromWorkflowRequest, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> WorkflowTemplate:
    workflow = db.get(Workflow, workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow не найден")
    if workflow.project_id != payload.project_id:
        raise HTTPException(status_code=422, detail="Шаблон можно сохранить только в проект этого workflow")
    graph = _clean_graph(workflow.graph_json)
    item = WorkflowTemplate(project_id=workflow.project_id, name=payload.name, description=payload.description or workflow.description, tags=payload.tags, graph_json=graph, created_by=user.id)
    db.add(item); db.flush(); audit(db, user.id, "CREATE", "workflow_template", item.id, after={"workflow_id": workflow.id, "name": item.name}); db.commit(); db.refresh(item)
    return item


@router.patch("/{template_id}", response_model=WorkflowTemplateOut)
def update_template(template_id: str, payload: WorkflowTemplateUpdate, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> WorkflowTemplate:
    item = db.get(WorkflowTemplate, template_id)
    if not item:
        raise HTTPException(status_code=404, detail="Шаблон не найден или является встроенным")
    if item.is_builtin:
        raise HTTPException(status_code=403, detail="Встроенный шаблон защищён от изменений")
    before = {"name": item.name, "description": item.description, "tags": item.tags}
    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(item, key, value)
    audit(db, user.id, "UPDATE", "workflow_template", item.id, before=before, after=payload.model_dump(exclude_none=True)); db.commit(); db.refresh(item)
    return item


@router.delete("/{template_id}", status_code=204)
def delete_template(template_id: str, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> None:
    item = db.get(WorkflowTemplate, template_id)
    if not item:
        raise HTTPException(status_code=404, detail="Встроенные шаблоны удалить нельзя")
    if item.is_builtin:
        raise HTTPException(status_code=403, detail="Встроенный шаблон удалить нельзя")
    audit(db, user.id, "DELETE", "workflow_template", item.id, before={"name": item.name}); db.delete(item); db.commit()


@router.post("/{template_id}/instantiate", response_model=WorkflowOut, status_code=201)
def instantiate_template(template_id: str, payload: WorkflowTemplateInstantiateRequest, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> Workflow:
    system = _system_template(template_id)
    if system:
        name, description, graph = system["name"], system["description"], system["graph_json"]
    else:
        item = db.get(WorkflowTemplate, template_id)
        if not item:
            raise HTTPException(status_code=404, detail="Шаблон не найден")
        name, description, graph = item.name, item.description, item.graph_json
    workflow = Workflow(project_id=payload.project_id, name=payload.name or f"{name} — копия", description=description, graph_json=_clean_graph(graph))
    db.add(workflow); db.flush(); audit(db, user.id, "CREATE", "workflow", workflow.id, after={"template_id": template_id, "name": workflow.name}); db.commit(); db.refresh(workflow)
    return workflow
