from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from workflow_engine import graph_contract_version, standard_v2_graph, validate_dag

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
from app.services.authorization import (
    require_project,
    require_project_object,
    require_same_project,
    scope_to_projects,
)

router = APIRouter(prefix="/workflow-templates", tags=["Workflow templates"])


_DEFAULT_BUDGETS = {
    "maxRequests": 50,
    "maxBytes": 20_000_000,
    "maxPages": 25,
    "maxItems": 500,
    "deadlineSeconds": 600,
}


def _phase_config(
    allow: list[str],
    *,
    goal: str,
    prefer: list[str] | None = None,
    **config: Any,
) -> dict[str, Any]:
    """Build a neutral v2 facade configuration for a system template."""

    return {
        "contractVersion": 2,
        "mode": "AUTO",
        "goal": goal,
        "strategies": {
            "allow": allow,
            "deny": [],
            "prefer": prefer or [],
            "fallbackPolicy": "ON_POSTCONDITION_FAILURE",
        },
        "budgets": dict(_DEFAULT_BUDGETS),
        "successCriteria": [],
        "errorPolicy": "FAIL_REQUIRED_SCOPE",
        "evidencePolicy": {"retainRaw": True, "retainAttempts": True},
        **config,
    }


def _universal_graph(
    *,
    acquire: dict[str, Any],
    traverse: dict[str, Any],
    extract: dict[str, Any],
) -> dict[str, Any]:
    """Return a seven-phase, source-agnostic template graph.

    The selected Source supplies the URL only after template instantiation.
    Selectors, API endpoint details and all source semantics are intentionally
    empty so that a template never silently claims support for one domain.
    """

    graph = standard_v2_graph(settings={
        "review_policy": {"new": True, "changed": True, "confidence_below": 0.8},
    })
    nodes = {node["id"]: node for node in graph["nodes"]}
    nodes["start"]["config"] = _phase_config(["start-input"], goal="Контекст ручного запуска")
    nodes["acquire"]["config"] = acquire
    nodes["traverse"]["config"] = traverse
    nodes["extract"]["config"] = extract
    nodes["process"]["config"] = _phase_config(
        ["process-operations"],
        goal="Нормализовать и дедуплицировать записи",
        input_path="records",
        operations=[],
        identityFields=[],
    )
    nodes["assure"]["config"] = _phase_config(
        ["assure-validation"],
        goal="Не публиковать пустой или частичный результат как полный",
        input_path="records",
        required=[],
        schema={},
        fail_on_error=False,
        expectedScope={"allowEmpty": False, "requireComplete": False},
    )
    nodes["output"]["config"] = _phase_config(
        ["output-dataset"],
        goal="Сохранить явно извлечённые записи в выбранный dataset",
        input_path="records",
        natural_key_fields=["url"],
        minimum_expected_records=0,
        on_empty="warning",
        name="records",
    )
    return graph


def _web_acquire(*, browser_only: bool = False, feed: bool = False) -> dict[str, Any]:
    if browser_only:
        allow, prefer, goal = ["acquire-browser"], ["acquire-browser"], "Получить публичную JavaScript-страницу"
    elif feed:
        allow, prefer, goal = ["acquire-feed"], ["acquire-feed"], "Получить публичную RSS или XML-ленту"
    else:
        allow, prefer, goal = ["acquire-http", "acquire-browser"], ["acquire-http"], "Получить публичное HTML-представление"
    return _phase_config(allow, prefer=prefer, goal=goal, url="{{source.url}}", method="GET", timeout=45)


def _http_traverse(*, detail: bool = False) -> dict[str, Any]:
    return _phase_config(
        ["traverse-links"],
        goal="Обойти публичные страницы и detail-ссылки" if detail else "Передать публичное представление на извлечение",
        pagination={"enabled": False, "mode": "next", "maxPages": 25},
        detail={"enabled": detail, "selector": "", "itemsPath": "", "urlPath": "url", "maxItems": 100, "fields": []},
        drop_query_params=[],
    )


def _browser_traverse() -> dict[str, Any]:
    return _phase_config(
        ["traverse-browser"],
        goal="Обойти публичные tabs, filters, pages и detail-карточки",
        browserTraversal={
            "listing": {"itemSelector": "", "linkSelector": "a[href]", "fields": []},
            "states": [],
            "pagination": {"enabled": False, "maxPages": 25},
            "loadMore": {"selector": "", "times": 0},
            "scroll": {"times": 0},
            "detail": {"enabled": False, "maxItems": 100, "includeListingFields": True, "fields": []},
        },
    )


# System templates are portable v2 blueprints, never single-site presets.
# A user selects a Source while creating a copy and configures only the public
# representation actually delivered by that source.
SYSTEM_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "system-universal-html-cards",
        "name": "Публичные HTML-карточки",
        "description": "Для публичных статических страниц с карточками: новости, продукты, предложения и каталоги. Выберите Source, затем настройте CSS карточки и поля.",
        "tags": ["universal", "public", "html", "cards"],
        "is_system": True,
        "graph_json": _universal_graph(
            acquire=_web_acquire(),
            traverse=_http_traverse(),
            extract=_phase_config(
                ["extract-dom"],
                goal="Извлечь повторяющиеся HTML-карточки",
                dom={"inputPath": "body", "itemSelector": "", "fields": []},
            ),
        ),
    },
    {
        "id": "system-universal-html-list-detail",
        "name": "Публичный список → detail-страницы",
        "description": "Для HTML-списков с пагинацией и отдельными материалами. В UI задайте ссылки detail, страницы и поля detail; запросы идут только к публичным URL.",
        "tags": ["universal", "public", "html", "list-detail", "pagination"],
        "is_system": True,
        "graph_json": _universal_graph(
            acquire=_web_acquire(),
            traverse=_http_traverse(detail=True),
            extract=_phase_config(
                ["extract-mapping"],
                goal="Сформировать записи из listing и detail-полей",
                input_path="records",
                fields=[],
            ),
        ),
    },
    {
        "id": "system-universal-html-table",
        "name": "Публичные HTML-таблицы",
        "description": "Для курсов, тарифов, котировок и других публичных HTML-таблиц. При необходимости переключите Acquire на browser render и выберите таблицу в UI.",
        "tags": ["universal", "public", "html", "table", "rates"],
        "is_system": True,
        "graph_json": _universal_graph(
            acquire=_web_acquire(),
            traverse=_http_traverse(),
            extract=_phase_config(
                ["extract-table"],
                goal="Извлечь строки публичной HTML-таблицы",
                table={"inputPath": "body", "selector": "table", "header_row": 0, "normalize_fields": True},
            ),
        ),
    },
    {
        "id": "system-universal-json-api",
        "name": "Публичный JSON API / XHR",
        "description": "Для открытого REST/JSON endpoint или JSON, полученного при обычном browser render. Настройте endpoint/XHR selector, JSONPath и pagination в UI.",
        "tags": ["universal", "public", "json", "api", "xhr", "pagination"],
        "is_system": True,
        "graph_json": _universal_graph(
            acquire=_phase_config(
                ["acquire-api", "acquire-browser-xhr"],
                prefer=["acquire-api"],
                goal="Получить публичное JSON-представление",
                url="{{source.url}}",
                method="GET",
                timeout=45,
                endpoint="",
                xhr={"urlContains": "", "path": ""},
            ),
            traverse=_http_traverse(detail=False),
            extract=_phase_config(
                ["extract-json"],
                goal="Извлечь записи по JSONPath",
                json={"inputPath": "body", "path": "$.items[*]"},
            ),
        ),
    },
    {
        "id": "system-universal-public-feed",
        "name": "Публичная RSS / XML-лента",
        "description": "Для RSS, Atom и XML-лент. Укажите CSS/XML-селектор элемента и его поля; URL ленты берётся из выбранного Source.",
        "tags": ["universal", "public", "rss", "xml", "feed"],
        "is_system": True,
        "graph_json": _universal_graph(
            acquire=_web_acquire(feed=True),
            traverse=_http_traverse(),
            extract=_phase_config(
                ["extract-dom"],
                goal="Извлечь повторяющиеся элементы RSS или XML",
                dom={"inputPath": "body", "itemSelector": "", "fields": []},
            ),
        ),
    },
    {
        "id": "system-universal-browser-list-detail",
        "name": "Публичный browser: карточки, состояния, detail",
        "description": "Для публичных JavaScript-сайтов с tabs, filters, load-more, scroll или кнопочной пагинацией. Настройка разрешает только declarative CSS/actions, без JavaScript и обхода доступа.",
        "tags": ["universal", "public", "browser", "javascript", "list-detail"],
        "is_system": True,
        "graph_json": _universal_graph(
            acquire=_web_acquire(browser_only=True),
            traverse=_browser_traverse(),
            extract=_phase_config(
                ["extract-mapping"],
                goal="Сформировать записи из публичных browser listing/detail-полей",
                input_path="records",
                fields=[],
            ),
        ),
    },
    {
        "id": "system-universal-public-document",
        "name": "Публичный документ: PDF, DOCX, XLSX, CSV",
        "description": "Для публично скачиваемого документа или файла. Source задаёт URL файла; в UI выберите лист XLSX, строку заголовков и при необходимости OCR PDF.",
        "tags": ["universal", "public", "document", "pdf", "xlsx", "csv", "docx"],
        "is_system": True,
        "graph_json": _universal_graph(
            acquire=_phase_config(
                ["acquire-file"],
                prefer=["acquire-file"],
                goal="Скачать публичный документ",
                url="{{source.url}}",
                timeout=60,
            ),
            traverse=_http_traverse(),
            extract=_phase_config(
                ["extract-document"],
                goal="Извлечь записи из публичного документа",
                document={"inputPath": "content_base64", "filenamePath": "filename", "header_row": 0, "ocr": False},
            ),
        ),
    },
    {
        "id": "system-universal-html-document-inventory",
        "name": "Публичный каталог → документы",
        "description": "Для страницы-каталога, где карточки или ссылки ведут к публичным PDF, DOCX, XLSX или CSV. В UI укажите ссылки на документы, пагинацию при наличии и параметры разбора файла.",
        "tags": ["universal", "public", "html", "catalog", "document", "pdf", "xlsx", "csv", "docx"],
        "is_system": True,
        "graph_json": _universal_graph(
            acquire=_web_acquire(),
            traverse=_http_traverse(detail=True),
            extract=_phase_config(
                ["extract-document"],
                goal="Скачать и извлечь записи из всех публичных документов каталога",
                document={"inputPath": "content_base64", "filenamePath": "filename", "header_row": 0, "ocr": False},
            ),
        ),
    },
]


_LITERAL_URL = re.compile(r"https?://[^\s}]+", re.I)


def _clean_v2_source_config(node_type: str, config: dict[str, Any]) -> None:
    """Remove source-specific choices from an adaptive public facade.

    Strategies describe reusable capabilities and may stay selected. URLs,
    selectors, request payloads, field mappings and expected business schema
    describe one source and must be configured only in the workflow copy.
    """

    config.pop("selectedStrategy", None)
    config["goal"] = ""
    config["successCriteria"] = []
    if node_type == "http_request":
        config["url"] = "{{source.url}}"
        for key in ("endpoint", "apiUrl", "api_url", "entry", "seedUrl"):
            config[key] = ""
        for key in ("headers", "cookies", "query_params", "json_body"):
            if key in config:
                config[key] = {}
        if "actions" in config:
            config["actions"] = []
        if "xhr" in config or "browserXhr" in config:
            config["xhr"] = {"urlContains": "", "path": ""}
            config.pop("browserXhr", None)
    elif node_type == "crawl_links":
        config["pagination"] = {"enabled": False, "mode": "next", "maxPages": 25}
        config["detail"] = {
            "enabled": False,
            "selector": "",
            "itemsPath": "",
            "urlPath": "url",
            "maxItems": 100,
            "fields": [],
        }
        if "browserTraversal" in config or "browser_traversal" in config:
            config["browserTraversal"] = {
                "listing": {"itemSelector": "", "linkSelector": "a[href]", "fields": []},
                "states": [],
                "pagination": {"enabled": False, "maxPages": 25},
                "loadMore": {"selector": "", "times": 0},
                "scroll": {"times": 0},
                "detail": {"enabled": False, "maxItems": 100, "includeListingFields": True, "fields": []},
            }
            config.pop("browser_traversal", None)
    elif node_type == "mapping":
        for key in ("fieldCandidates", "field_candidates", "selectedCandidates", "targetSchemaRef", "target_schema_ref"):
            config.pop(key, None)
        config["fields"] = []
        if "mapping" in config:
            config["mapping"] = {}
        if "dom" in config:
            config["dom"] = {"inputPath": "body", "itemSelector": "", "fields": []}
        if "json" in config:
            config["json"] = {"inputPath": "body", "path": "$.items[*]"}
        if "table" in config:
            config["table"] = {"inputPath": "body", "selector": "table", "header_row": 0, "normalize_fields": True}
        if "document" in config:
            config["document"] = {"inputPath": "content_base64", "filenamePath": "filename", "header_row": 0, "ocr": False}
    elif node_type == "transform":
        config["operations"] = []
        config["filters"] = []
        config["identityFields"] = []
    elif node_type == "validate":
        config["schema"] = {}
        config["required"] = []
        config["expectedScope"] = {"allowEmpty": False, "requireComplete": False}
        config["fail_on_error"] = False
    elif node_type == "output":
        config["natural_key_fields"] = ["url"]
        config["name"] = "records"


def _clean_graph(graph: dict[str, Any], *, reset_v2_source_config: bool = True) -> dict[str, Any]:
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
    if reset_v2_source_config:
        settings.pop("presetRefs", None)
        settings.pop("policies", None)
    if isinstance(settings.get("natural_key_fields"), (str, list)):
        settings["natural_key_fields"] = ["url"]
    for node in result.get("nodes", []):
        config = node.get("config") if isinstance(node, dict) else None
        if not isinstance(config, dict):
            continue
        node_type = str(node.get("type") or "")
        config.pop("source_id", None)
        config.pop("dataset_id", None)
        is_v2 = str(config.get("contractVersion") or result.get("contractVersion") or settings.get("contractVersion") or "") == "2"
        if is_v2 and reset_v2_source_config:
            _clean_v2_source_config(node_type, config)
            continue
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


def _is_portable_v2_template(graph: dict[str, Any]) -> bool:
    """Only v2, source-independent graphs may appear in the reusable picker."""

    try:
        return graph_contract_version(graph) == 2 and not _template_issues(graph)
    except ValueError:
        return False


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
            if "{{source.url}}" in value and value != "{{source.url}}":
                issues.append(f"source-derived path at {path}")

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
def list_templates(project_id: str | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    if project_id:
        require_project(db, user, project_id)
    items: list[dict[str, Any]] = [deepcopy(item) for item in SYSTEM_TEMPLATES]
    stmt = select(WorkflowTemplate).order_by(WorkflowTemplate.updated_at.desc())
    # A template is a portable graph snapshot: source and dataset bindings are
    # stripped on save.  Its project is only the ownership/audit context, not
    # an access boundary, so a proven parser can be reused in another project.
    # Legacy/site-specific snapshots remain auditable in the database, but
    # must not appear in the reusable-template picker.
    items.extend(
        _template_out(item)
        for item in db.scalars(scope_to_projects(stmt, WorkflowTemplate.project_id, db, user)).all()
        if _is_portable_v2_template(item.graph_json)
    )
    return items


@router.post("", response_model=WorkflowTemplateOut, status_code=201)
def create_template(payload: WorkflowTemplateCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> WorkflowTemplate:
    require_project(db, user, payload.project_id)
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
    workflow = require_project_object(db, user, Workflow, workflow_id, label="Workflow")
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow не найден")
    require_same_project(payload.project_id, workflow)
    graph = _clean_graph(workflow.graph_json)
    issues = _template_issues(graph)
    if issues:
        raise HTTPException(status_code=422, detail={"message": "Workflow содержит site-specific настройки; очистите их перед сохранением шаблона", "issues": issues[:20]})
    item = WorkflowTemplate(project_id=workflow.project_id, name=payload.name, description=payload.description or workflow.description, tags=payload.tags, graph_json=graph, created_by=user.id)
    db.add(item); db.flush(); audit(db, user.id, "CREATE", "workflow_template", item.id, after={"workflow_id": workflow.id, "name": item.name}); db.commit(); db.refresh(item)
    return item


@router.patch("/{template_id}", response_model=WorkflowTemplateOut)
def update_template(template_id: str, payload: WorkflowTemplateUpdate, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> WorkflowTemplate:
    item = require_project_object(db, user, WorkflowTemplate, template_id, label="Шаблон")
    if not item:
        raise HTTPException(status_code=404, detail="Шаблон не найден или является встроенным")
    if item.is_builtin:
        raise HTTPException(status_code=403, detail="Встроенный шаблон защищён от изменений")
    before = {"name": item.name, "description": item.description, "tags": item.tags}
    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(item, key, value)
    audit(db, user.id, "UPDATE", "workflow_template", item.id, before=before, after=payload.model_dump(exclude_none=True)); db.commit(); db.refresh(item)
    return item


@router.delete("/{template_id}", status_code=204, response_model=None)
def delete_template(template_id: str, db: Session = Depends(get_db), user: User = Depends(require_roles("ADMINISTRATOR", "DEVELOPER"))) -> None:
    item = require_project_object(db, user, WorkflowTemplate, template_id, label="Шаблон")
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
        item = require_project_object(db, user, WorkflowTemplate, template_id, label="Шаблон")
        if not item:
            raise HTTPException(status_code=404, detail="Шаблон не найден")
        if not _is_portable_v2_template(item.graph_json):
            raise HTTPException(status_code=422, detail="Этот legacy-шаблон больше не доступен для повторного использования. Используйте системный универсальный шаблон.")
        name, description, graph = item.name, item.description, item.graph_json
    # System templates are already neutral. A v2 custom template was made
    # neutral at save time too, so preserve its generic capability choices
    # (e.g. list→detail) while binding Source/Dataset only on this copy.
    copied_graph = _clean_graph(graph, reset_v2_source_config=False)
    settings = copied_graph.setdefault("settings", {})
    settings.pop("source_id", None)
    settings.pop("dataset_id", None)
    if payload.source_id:
        source = require_project_object(db, user, Source, payload.source_id, label="Source")
        require_same_project(payload.project_id, source)
        settings["source_id"] = source.id
    if payload.dataset_id:
        dataset = require_project_object(db, user, Dataset, payload.dataset_id, label="Dataset")
        require_same_project(payload.project_id, dataset)
        settings["dataset_id"] = dataset.id
    require_project(db, user, payload.project_id)
    workflow = Workflow(project_id=payload.project_id, name=payload.name or f"{name} — копия", description=description, graph_json=copied_graph)
    db.add(workflow); db.flush(); audit(db, user.id, "CREATE", "workflow", workflow.id, after={"template_id": template_id, "name": workflow.name}); db.commit(); db.refresh(workflow)
    return workflow
