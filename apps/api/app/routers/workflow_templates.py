from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from workflow_engine import validate_dag

from app.audit import audit
from app.database import get_db
from app.dependencies import get_current_user, require_roles
from app.models import Dataset, Source, User, Workflow, WorkflowTemplate
from app.schemas import (
    WorkflowOut,
    WorkflowTemplateCreate,
    WorkflowTemplateFromWorkflowRequest,
    WorkflowTemplateInstantiateRequest,
    WorkflowTemplateOut,
    WorkflowTemplateUpdate,
)
from app.seed_templates import bcse_news_graph

router = APIRouter(prefix="/workflow-templates", tags=["Workflow templates"])


def _graph(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Any]:
    return {"version": 1, "settings": {"review_policy": {"new": True, "changed": True, "confidence_below": 0.8}}, "nodes": nodes, "edges": edges}


# System entries are either source-independent starter blueprints or explicitly
# labelled site presets. They stay in code so reviewed presets can evolve while
# user templates below remain immutable snapshots stored in the database.
SYSTEM_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "system-bcse-news",
        "name": "Новости БВФБ",
        "description": "Готовый site preset: все карточки и страницы пагинации пресс-центра БВФБ → полный текст, HTML и вложения → versioned dataset.",
        "tags": ["site-preset", "БВФБ", "news", "list-detail", "pagination"],
        "is_system": True,
        "site_preset": True,
        "preset_defaults": {
            "project_slug": "bcse-news",
            "source_entry_url": "https://www.bcse.by/press-center/releases",
            "dataset_slug": "bcse-news",
        },
        "graph_json": bcse_news_graph("", "", incremental=True),
    },
    {
        "id": "system-list-detail-crawl",
        "name": "Список ссылок → detail-карточки",
        "description": "Универсальный fan-out/fan-in: выбранный источник даёт список ссылок, каждая detail-страница превращается в запись через Mapping и сохраняется в результат workflow.",
        "tags": ["web", "crawl", "detail", "fan-out"],
        "is_system": True,
        "graph_json": _graph(
            [
                {"id": "trigger", "type": "manual_trigger", "position": {"x": 20, "y": 160}, "config": {}},
                {"id": "crawl", "type": "crawl_links", "position": {"x": 300, "y": 160}, "config": {
                    "listing_url": "", "listing_query": {}, "items_path": "", "url_path": "url", "link_selector": "", "detail_fetch_mode": "AUTO",
                    "pagination_enabled": True, "pagination_max_pages": 25, "tabs_enabled": False, "tabs_wait_ms": 700,
                    "pagination_next_selector": "li[aria-label='Next page'] a", "pagination_wait_ms": 500,
                    "url_pattern": "", "max_items": 5000, "concurrency": 10, "delay_ms": 250, "request_retries": 2,
                    "request_timeout": 45, "timeout": 900, "same_origin_only": True, "headers": {},
                    "detail_fields": [], "detail_constants": {}, "include_listing_fields": True,
                    "drop_query_params": [], "save_artifacts": True,
                }},
                {"id": "mapping", "type": "mapping", "position": {"x": 620, "y": 160}, "config": {"input_path": "records", "fields": [
                    {"target": "record_id", "source_path": "record_id"},
                    {"target": "url", "source_path": "url"},
                ]}},
                {"id": "output", "type": "output", "position": {"x": 940, "y": 160}, "config": {"input_path": "records", "natural_key_fields": ["url"], "on_empty": "warning", "name": "detail_records"}},
            ],
            [
                {"id": "e1", "source": "trigger", "target": "crawl"}, {"id": "e2", "source": "crawl", "target": "mapping"},
                {"id": "e3", "source": "mapping", "target": "output"},
            ],
        ),
    },
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


_LITERAL_URL = re.compile(r"https?://[^\s}]+", re.I)


def _clean_graph(graph: dict[str, Any]) -> dict[str, Any]:
    """Return a portable graph suitable for a reusable template.

    A workflow may contain source-specific tuning, but a template is a
    contract, not a copy of one site's implementation. Known source-bound
    crawl/fetch settings are reset to source placeholders/fallbacks before
    the graph is persisted or instantiated.
    """
    result = deepcopy(graph)
    settings = result.setdefault("settings", {})
    settings.pop("source_id", None)
    settings.pop("dataset_id", None)
    if isinstance(settings.get("natural_key_fields"), (str, list)):
        settings["natural_key_fields"] = ["url"]
    for node in result.get("nodes", []):
        config = node.get("config") if isinstance(node, dict) else None
        if not isinstance(config, dict):
            continue
        node_type = str(node.get("type") or "")
        config.pop("source_id", None)
        config.pop("dataset_id", None)
        if node_type in {"http_request", "browser_open", "download_file"} and "url" in config:
            config["url"] = "{{source.url}}"
        if node_type == "crawl_links":
            config.update({
                "listing_url": "",
                "listing_query": {},
                "items_path": "",
                "url_path": "url",
                "link_selector": "",
                "url_pattern": "",
                "base_url": "",
                "detail_fields": [],
                "detail_constants": {},
                "date_range_query": {},
                "detail_fetch_mode": "AUTO",
            })
            for legacy_key in ("title_selector", "date_selector", "body_selector", "tag_selector", "language", "source_name", "attachment_selector", "lookback_days"):
                config.pop(legacy_key, None)
        if node_type == "validate":
            config["schema"] = {}
            config["required"] = []
            config["fail_on_error"] = False
        if node_type == "output":
            config.pop("dataset_id", None)
            config["natural_key_fields"] = ["url"]
            config["name"] = "records"
    return result


def _template_issues(graph: dict[str, Any]) -> list[str]:
    """Find literal source bindings that must never enter a template."""
    issues: list[str] = []

    def visit(value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "$schema":
                    continue
                if key in {"source_id", "dataset_id"} and child:
                    issues.append(f"binding at {path}.{key}" if path else f"binding at {key}")
                visit(child, f"{path}.{key}" if path else key)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")
        elif isinstance(value, str):
            if _LITERAL_URL.search(value) and "{{source." not in value:
                issues.append(f"literal URL at {path}")

    visit(graph)
    return issues


def _system_template(template_id: str) -> dict[str, Any] | None:
    return next((item for item in SYSTEM_TEMPLATES if item["id"] == template_id), None)


def _template_out(item: WorkflowTemplate) -> dict[str, Any]:
    return {
        "id": item.id, "project_id": item.project_id, "name": item.name,
        "description": item.description, "tags": item.tags, "graph_json": _clean_graph(item.graph_json),
        "is_system": item.is_builtin, "created_at": item.created_at, "updated_at": item.updated_at,
    }


@router.get("", response_model=list[WorkflowTemplateOut])
def list_templates(project_id: str | None = None, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = [deepcopy(item) for item in SYSTEM_TEMPLATES]
    stmt = select(WorkflowTemplate).order_by(WorkflowTemplate.updated_at.desc())
    # A template is a portable graph snapshot: source and dataset bindings are
    # stripped on save.  Its project is only the ownership/audit context, not
    # an access boundary, so a proven parser can be reused in another project.
    # Legacy/site-specific snapshots remain auditable in the database, but
    # must not appear in the reusable-template picker.
    items.extend(_template_out(item) for item in db.scalars(stmt).all() if not _template_issues(item.graph_json))
    return items


@router.post("", response_model=WorkflowTemplateOut, status_code=201)
def create_template(payload: WorkflowTemplateCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> WorkflowTemplate:
    graph = _clean_graph(payload.graph_json)
    issues = _template_issues(graph)
    if issues:
        raise HTTPException(status_code=422, detail={"message": "Шаблон должен быть универсальным", "issues": issues[:20]})
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
    issues = _template_issues(graph)
    if issues:
        raise HTTPException(status_code=422, detail={"message": "Workflow содержит site-specific настройки; очистите их перед сохранением шаблона", "issues": issues[:20]})
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
        if _template_issues(item.graph_json):
            raise HTTPException(status_code=422, detail="Этот legacy-шаблон содержит привязку к сайту и больше недоступен. Используйте системный универсальный шаблон.")
        name, description, graph = item.name, item.description, item.graph_json
    copied_graph = deepcopy(graph) if system and system.get("site_preset") else _clean_graph(graph)
    settings = copied_graph.setdefault("settings", {})
    settings.pop("source_id", None)
    settings.pop("dataset_id", None)
    if payload.source_id:
        source = db.get(Source, payload.source_id)
        if not source or source.project_id != payload.project_id:
            raise HTTPException(status_code=422, detail="Источник должен принадлежать выбранному проекту")
        settings["source_id"] = source.id
    if payload.dataset_id:
        dataset = db.get(Dataset, payload.dataset_id)
        if not dataset or dataset.project_id != payload.project_id:
            raise HTTPException(status_code=422, detail="Dataset должен принадлежать выбранному проекту")
        settings["dataset_id"] = dataset.id
    workflow = Workflow(project_id=payload.project_id, name=payload.name or f"{name} — копия", description=description, graph_json=copied_graph)
    db.add(workflow); db.flush(); audit(db, user.id, "CREATE", "workflow", workflow.id, after={"template_id": template_id, "name": workflow.name}); db.commit(); db.refresh(workflow)
    return workflow
