"""Idempotent installer for the declarative Belarus Market source registry.

The source passports are retained as human-reviewable data.  This installer
only turns their records into generic Sources, immutable SourcePresetRevision
rows, per-source seven-phase workflows and dataset membership.  It does not
put a site name or hostname conditional into the workflow engine.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.models import (
    DataSchema,
    Dataset,
    DatasetSourceMembership,
    Project,
    Schedule,
    Source,
    SourcePresetRevision,
    User,
    Workflow,
    WorkflowBlueprintRevision,
)
from app.services.preset_compiler import compile_preset
from sqlalchemy import select
from sqlalchemy.orm import Session
from workflow_engine import compile_executable_plan, standard_v2_graph
from app.seed_templates import bcse_market_news_category_graph, bcse_market_news_graph

ROOT = Path(__file__).resolve().parents[4]
PACK_ROOT = ROOT / "presets" / "belarus-market"
PASSPORTS = {
    "legal": ROOT / "PASSPORT_UL_DEPOSITS.md",
    "retail": ROOT / "PASSPORT_FL_DEPOSITS.md",
    "news": ROOT / "PASSPORT_MARKET_NEWS.md",
}
DATASETS = {
    "legal": ("deposit-offers-legal", "ЮЛ: предложения по депозитам", "bank-deposit-offer-v2.json"),
    "retail": ("deposit-offers-retail", "ФЛ: предложения по депозитам", "bank-deposit-offer-v2.json"),
    "news": ("market-news", "Новости рынка", "market-news-v1.json"),
    "indicators": ("market-indicators", "Рыночные индикаторы", "market-indicator-v1.json"),
}
URL_OVERRIDES = {
    "ul-08": "https://www.belveb.by/small-business/deposits/deposits-small-business/",
    "ul-11": "https://neobank.by/business/razmeshchenie-sredstv/depozity/",
    "ul-14": "https://www.rrb.by/korporativnim-klientam/depoziti",
}
INDICATOR_REGISTRY = PACK_ROOT / "indicators" / "nbrb-sources.json"
VERIFICATION_REGISTRY = PACK_ROOT / "verification.json"
NEWS_PROFILE_REGISTRY = PACK_ROOT / "news" / "source-profiles.json"


@dataclass(frozen=True)
class PassportSource:
    key: str
    name: str
    group: str
    url: str
    status: str = "DRAFT"
    fixture_refs: tuple[str, ...] = ()

    @property
    def dataset_group(self) -> str:
        return "indicators" if self.key == "news-03" or self.group == "indicators" else self.group


def passport_sources() -> list[PassportSource]:
    """Read all source rows from the canonical passport documents."""

    sources: list[PassportSource] = []
    verification = json.loads(VERIFICATION_REGISTRY.read_text(encoding="utf-8"))
    prefixes = {"legal": "UL", "retail": "FL", "news": "NEWS"}
    for group, passport in PASSPORTS.items():
        text = passport.read_text(encoding="utf-8")
        for block in re.split(r"(?=^## )", text, flags=re.MULTILINE):
            match = re.match(r"^## ((?:UL|FL|NEWS)-\d+|NEWS-TG-\d+)\s+—\s+(.+)", block)
            url = re.search(r"https?://[^\s)`]+", block)
            if (
                not match
                or not url
                or not match.group(1).startswith(prefixes[group])
                or match.group(1).startswith("NEWS-TG-")
            ):
                continue
            key = match.group(1).lower()
            evidence = verification.get(key) if isinstance(verification.get(key), dict) else {}
            sources.append(PassportSource(
                key=key,
                name=match.group(2).strip(),
                group=group,
                url=URL_OVERRIDES.get(key, url.group(0)),
                status=str(evidence.get("status") or "DRAFT").upper(),
                fixture_refs=tuple(str(ref) for ref in evidence.get("fixture_refs", []) if ref),
            ))
    for item in json.loads(INDICATOR_REGISTRY.read_text(encoding="utf-8")):
        sources.append(PassportSource(
            key=str(item["key"]), name=str(item["name"]), group="indicators",
            url=str(item["url"]), status=str(item.get("status") or "DRAFT").upper(),
        ))
    return sources


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _preset_config(source: PassportSource) -> dict:
    segment = "LEGAL_ENTITY" if source.group == "legal" else "INDIVIDUAL" if source.group == "retail" else None
    allow_empty = source.dataset_group == "news"
    config = {
        "apiVersion": "multiverse.io/v2",
        "kind": "SourcePreset",
        "bindings": {"sourceKey": source.key, "dataset": DATASETS[source.dataset_group][0]},
        "policies": {"budgets": {"maxRequests": 50, "maxBytes": 20_000_000, "maxPages": 25, "maxItems": 500, "deadlineSeconds": 600}},
        "nodes": {
            "acquire": {
                "url": "{{source.url}}", "method": "GET",
                "strategies": {"allow": ["acquire-http"], "deny": [], "prefer": ["acquire-http"], "fallbackPolicy": "ON_POSTCONDITION_FAILURE"},
                "successCriteria": [{"path": "body", "operator": "exists"}],
            },
            "process": {"operations": ([{"type": "constant", "field": "segment", "value": segment}] if segment else [])},
            "assure": {"expectedScope": {"allowEmpty": allow_empty, "minRecords": 0 if allow_empty else 1}, "sourceRole": {"expected": segment} if segment else {}},
        },
        "metadata": {"name": source.name, "canonicalUrl": source.url, "status": source.status},
    }
    override_path = PACK_ROOT / "overrides" / f"{source.key}.json"
    if override_path.exists():
        config = _deep_merge(config, json.loads(override_path.read_text(encoding="utf-8")))
    if source.dataset_group == "news":
        config = _deep_merge(config, _news_preset_config(source))
    return config


def _news_preset_config(source: PassportSource) -> dict:
    """Build a news revision only from reviewed, editable passport data.

    ``source-profiles.json`` is intentionally the sole place that carries
    selectors, link patterns, pagination and business rules.  The engine and
    installer only understand generic list/detail, access and rule semantics.
    """

    registry = json.loads(NEWS_PROFILE_REGISTRY.read_text(encoding="utf-8"))
    common = registry.get("common") if isinstance(registry.get("common"), dict) else {}
    profiles = registry.get("sources") if isinstance(registry.get("sources"), dict) else {}
    profile = profiles.get(source.key)
    if not isinstance(profile, dict):
        raise ValueError(f"News source {source.key} has no declarative profile")
    selection = profile.get("selection") if isinstance(profile.get("selection"), dict) else {}
    pagination = profile.get("pagination") if isinstance(profile.get("pagination"), dict) else {}
    common_nodes = common.get("nodes") if isinstance(common.get("nodes"), dict) else {}
    common_process = common_nodes.get("process") if isinstance(common_nodes.get("process"), dict) else {}
    common_operations = common_process.get("operations") if isinstance(common_process.get("operations"), list) else []
    nodes = {
        "traverse": {
            "url_pattern": str(profile.get("urlPattern") or ""),
            "detail": {"selector": str(profile.get("listingSelector") or "")},
        },
        "process": {
            "operations": [*common_operations,
                {"type": "constant", "field": "source_id", "value": str(profile.get("source_id") or source.key)},
                {"type": "constant", "field": "source_name", "value": source.name},
                {"type": "constant", "field": "source_section", "value": str(profile.get("source_section") or "")},
                {"type": "constant", "field": "source_authority", "value": "PRIMARY_OR_MEDIA"},
                {"type": "coalesce", "to": "identity_key", "fields": ["external_id", "canonical_url"]},
                {"type": "constant", "field": "selection_rule_version", "value": str(registry.get("version") or "news-passport-v1")},
                {"type": "select_by_rules", "fields": ["title", "summary_raw", "body_text", "tags"], **selection},
            ],
        },
    }
    if pagination:
        nodes["traverse"]["pagination"] = pagination
    return _deep_merge(common, {"nodes": nodes, "metadata": {"newsProfileVersion": str(registry.get("version") or "")}})


def _schedule_defaults(source: PassportSource) -> tuple[str, str]:
    """Return editable pack defaults; users can change them in the Schedule UI."""

    return ("0 8 * * 1-5", "Europe/Minsk") if source.dataset_group == "news" else ("0 8 * * 1", "Europe/Minsk")


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        result[key] = _deep_merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else value
    return result


def _find_pack_source(db: Session, project_id: str, key: str) -> Source | None:
    """Find a pack source by its ``source_key`` setting, portably.

    ``Source.settings`` is a JSON (not JSONB) column: on PostgreSQL a JSON-path
    comparison like ``settings['source_key'].as_string() == key`` matches the
    quoted JSON literal and silently fails, orphaning pack sources on every
    re-import.  Filtering in Python keeps the pairing identical on SQLite and
    PostgreSQL.
    """

    for source in db.scalars(select(Source).where(Source.project_id == project_id)).all():
        if (source.settings or {}).get("source_key") == key:
            return source
    return None


def install_belarus_market_pack(db: Session, admin: User) -> dict[str, int]:
    """Install or update only changed immutable preset revisions.

    DRAFT/BLOCKED presets intentionally get no fixture references and therefore
    cannot be accidentally promoted to VERIFIED by this bootstrap.
    """

    project = db.scalar(select(Project).where(Project.slug == "belarus-market-data"))
    if project is None:
        project = Project(name="Belarus Market Data", slug="belarus-market-data", description="Declarative Belarus market source pack", created_by=admin.id)
        db.add(project); db.flush()
    schemas: dict[str, DataSchema] = {}
    datasets: dict[str, Dataset] = {}
    for group, (slug, name, filename) in DATASETS.items():
        schema_json = json.loads((PACK_ROOT / "schemas" / filename).read_text(encoding="utf-8"))
        schema = db.scalar(select(DataSchema).where(DataSchema.project_id == project.id, DataSchema.name == filename))
        if schema is None:
            schema = DataSchema(project_id=project.id, name=filename, description=name, schema_json=schema_json, published=True)
            db.add(schema); db.flush()
        schemas[group] = schema
        dataset = db.scalar(select(Dataset).where(Dataset.slug == slug))
        if dataset is None:
            natural_key = (
                ["source_id", "identity_key"] if group == "news"
                else ["series_id", "effective_at", "source_url"] if group == "indicators"
                else ["institution_id", "segment", "product_id", "product_variant", "currency", "term_raw", "min_amount"]
            )
            dataset = Dataset(project_id=project.id, schema_id=schema.id, name=name, slug=slug, natural_key_fields=natural_key, review_policy={"new": False, "changed": True, "confidence_below": 0.8})
            db.add(dataset); db.flush()
        datasets[group] = dataset
    blueprint = db.scalar(select(WorkflowBlueprintRevision).where(WorkflowBlueprintRevision.project_id == project.id, WorkflowBlueprintRevision.slug == "belarus-market-v2").order_by(WorkflowBlueprintRevision.revision.desc()))
    if blueprint is None:
        blueprint = WorkflowBlueprintRevision(project_id=project.id, slug="belarus-market-v2", name="Belarus Market v2", description="Universal seven-phase blueprint", revision=1, status="DRAFT", graph_json=standard_v2_graph(), created_by=admin.id)
        db.add(blueprint); db.flush()
    counters = {"sources": 0, "presets_created": 0, "presets_unchanged": 0, "workflows": 0, "schedules": 0}
    # A prior preview of this pack included Telegram mirrors.  Keep historical
    # rows auditable but make them non-required and non-runnable when the
    # importer is rerun under the websites-only scope.
    for membership in db.scalars(
        select(DatasetSourceMembership).where(DatasetSourceMembership.source_key.like("news-tg-%"))
    ):
        membership.required = False
        if membership.workflow_id and (workflow := db.get(Workflow, membership.workflow_id)):
            workflow.is_active = False
    for descriptor in passport_sources():
        if descriptor.status == "VERIFIED" and not descriptor.fixture_refs:
            raise ValueError(f"VERIFIED source {descriptor.key} requires a fixture reference")
        for reference in descriptor.fixture_refs:
            if not (ROOT / reference).is_file():
                raise ValueError(f"Fixture reference for {descriptor.key} does not exist: {reference}")
        dataset = datasets[descriptor.dataset_group]
        source = _find_pack_source(db, project.id, descriptor.key)
        if source is None:
            source = Source(project_id=project.id, name=descriptor.name, source_type="WEB_PAGE", entry_url=descriptor.url, base_url=descriptor.url, fetch_mode="PLAYWRIGHT" if descriptor.key in {"news-01", "news-02"} else "HTTP", settings={"source_key": descriptor.key, "authority": "SECONDARY" if "tg-" in descriptor.key else "PRIMARY", "access": "PUBLIC"})
            db.add(source); db.flush()
        elif source.entry_url != descriptor.url:
            # The passports (and their URL overrides) are the pack's canonical
            # entry points; keep an existing source row in sync on re-import.
            source.entry_url = descriptor.url
            source.base_url = descriptor.url
        if descriptor.key in {"news-01", "news-02"}:
            source.fetch_mode = "PLAYWRIGHT"
        config = _preset_config(descriptor)
        config_hash = _hash(config)
        latest = db.scalar(select(SourcePresetRevision).where(SourcePresetRevision.project_id == project.id, SourcePresetRevision.slug == descriptor.key).order_by(SourcePresetRevision.revision.desc()))
        if latest is None or _hash(latest.config_json) != config_hash:
            revision = 1 if latest is None else latest.revision + 1
            preset = SourcePresetRevision(project_id=project.id, blueprint_revision_id=blueprint.id, slug=descriptor.key, name=descriptor.name, revision=revision, status=descriptor.status, config_json=config, source_policy_ref="public-anonymous-only", dataset_schema_ref=DATASETS[descriptor.dataset_group][2], fixture_refs=list(descriptor.fixture_refs), created_by=admin.id)
            db.add(preset); db.flush(); counters["presets_created"] += 1
        else:
            preset = latest; counters["presets_unchanged"] += 1
        workflow = db.scalar(select(Workflow).where(Workflow.project_id == project.id, Workflow.name == f"{descriptor.key}: {descriptor.name}"))
        if workflow is None:
            graph = (
                bcse_market_news_graph(source.id, dataset.id, incremental=True)
                if descriptor.key == "news-01"
                else bcse_market_news_category_graph(source.id, dataset.id, incremental=True)
                if descriptor.key == "news-02"
                else compile_preset(blueprint.graph_json, preset.__dict__).graph
            )
            graph["settings"]["source_id"] = source.id
            graph["settings"]["dataset_id"] = dataset.id
            workflow = Workflow(project_id=project.id, name=f"{descriptor.key}: {descriptor.name}", description=f"Compiled {descriptor.status} preset {descriptor.key}@{preset.revision}", graph_json=graph, is_active=descriptor.status != "BLOCKED")
            db.add(workflow); db.flush()
            workflow.graph_json["settings"]["compiledPlanDigest"] = compile_executable_plan(graph, project_id=project.id, workflow_id=workflow.id, workflow_version=workflow.version, source_id=source.id, revision_refs={"sourcePresetRevisionId": preset.id}).digest
            counters["workflows"] += 1
        elif descriptor.key == "news-01" and (
            not any(node.get("id") == "crawl" for node in (workflow.graph_json or {}).get("nodes", []))
            or "(?:news|releases)" in str(
                next(
                    (
                        node.get("config", {}).get("url_pattern", "")
                        for node in (workflow.graph_json or {}).get("nodes", [])
                        if node.get("id") == "crawl"
                    ),
                    "",
                )
            )
        ):
            # Repair the original universal-v2 NEWS-01 workflow.  The BCSE
            # releases page is a JS shell, so the generic HTTP-first graph
            # cannot discover cards; use the reviewed browser→detail preset.
            # It also keeps the shared calendar endpoint constrained to
            # ``/press-center/releases`` rather than mixing in news cards.
            graph = bcse_market_news_graph(source.id, dataset.id, incremental=True)
            workflow.graph_json = graph
            workflow.version += 1
            workflow.published_version = None
            workflow.graph_json["settings"]["compiledPlanDigest"] = compile_executable_plan(graph, project_id=project.id, workflow_id=workflow.id, workflow_version=workflow.version, source_id=source.id, revision_refs={"sourcePresetRevisionId": preset.id}).digest
        elif descriptor.key == "news-02" and (
            not any(node.get("id") == "crawl" for node in (workflow.graph_json or {}).get("nodes", []))
            or "/press-center/news/" not in str(
                next(
                    (
                        node.get("config", {}).get("url_pattern", "")
                        for node in (workflow.graph_json or {}).get("nodes", [])
                        if node.get("id") == "crawl"
                    ),
                    "",
                )
            )
            or "bcse-news-category-v1" not in str((workflow.graph_json or {}).get("nodes", []))
        ):
            # NEWS-02 used to be compiled from the generic list/detail
            # profile.  Repair that legacy row in-place with the reviewed
            # browser → detail graph and keep the workflow id stable.
            graph = bcse_market_news_category_graph(source.id, dataset.id, incremental=True)
            workflow.graph_json = graph
            workflow.version += 1
            workflow.published_version = None
            workflow.graph_json["settings"]["compiledPlanDigest"] = compile_executable_plan(graph, project_id=project.id, workflow_id=workflow.id, workflow_version=workflow.version, source_id=source.id, revision_refs={"sourcePresetRevisionId": preset.id}).digest
        elif (workflow.graph_json or {}).get("settings", {}).get("source_id") != source.id:
            # Repair a workflow paired with the wrong segment's source (the
            # JSON-path lookup failure could bind UL workflows to stray rows).
            # Assignment replaces the JSON column value so SQLAlchemy notices.
            settings = {**((workflow.graph_json or {}).get("settings") or {}), "source_id": source.id, "dataset_id": dataset.id}
            workflow.graph_json = {**(workflow.graph_json or {}), "settings": settings}
        cron, timezone = _schedule_defaults(descriptor)
        schedule_name = f"{descriptor.key}: {descriptor.name}"
        schedule = db.scalar(select(Schedule).where(Schedule.workflow_id == workflow.id, Schedule.name == schedule_name))
        if schedule is None:
            # DRAFT rows are intentionally opt-in: their schedule is visible
            # and editable in no-code UI but cannot execute an unverified
            # parser until an operator enables it after fixture/smoke review.
            db.add(Schedule(workflow_id=workflow.id, name=schedule_name, cron=cron, timezone=timezone, enabled=descriptor.status == "VERIFIED"))
            counters["schedules"] += 1
        membership = db.scalar(select(DatasetSourceMembership).where(DatasetSourceMembership.dataset_id == dataset.id, DatasetSourceMembership.source_key == descriptor.key))
        if membership is None:
            db.add(DatasetSourceMembership(dataset_id=dataset.id, source_id=source.id, workflow_id=workflow.id, source_preset_revision_id=preset.id, source_key=descriptor.key, required=descriptor.status != "BLOCKED"))
        counters["sources"] += 1
    db.commit()
    return counters
