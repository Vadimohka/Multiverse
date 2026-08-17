"""Versioned public workflow contracts and immutable executable plans.

The product surface deliberately remains the seven historic node type keys.
Format-specific implementation details live below this module and do not alter
the graph schema.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

from .types import DataType, NodeContract

CONTRACT_VERSION = 2
ADAPTIVE_MODES = frozenset({"AUTO", "ASSISTED", "MANUAL"})
PUBLIC_PHASES: Mapping[str, str] = MappingProxyType(
    {
        "manual_trigger": "Start",
        "http_request": "Acquire",
        "crawl_links": "Traverse",
        "mapping": "Extract",
        "transform": "Process",
        "validate": "Assure",
        "output": "Output",
    }
)

# Typed phase envelopes.  They are intentionally OBJECT ports in the first
# delivery: adapters keep legacy node implementations executable while a later
# delivery replaces their internal payloads with dedicated model classes.
V2_CONTRACTS: Mapping[str, NodeContract] = MappingProxyType(
    {
        "manual_trigger": NodeContract(DataType.VOID, DataType.OBJECT, "context"),
        "http_request": NodeContract(DataType.OBJECT, DataType.OBJECT, "source_bundle"),
        "crawl_links": NodeContract(DataType.OBJECT, DataType.OBJECT, "source_bundle"),
        "mapping": NodeContract(DataType.OBJECT, DataType.OBJECT, "record_set"),
        "transform": NodeContract(DataType.OBJECT, DataType.OBJECT, "record_set"),
        "validate": NodeContract(DataType.OBJECT, DataType.OBJECT, "assessment"),
        "output": NodeContract(DataType.OBJECT, DataType.OBJECT, "receipt"),
    }
)


class ContractError(ValueError):
    """A graph requests an unsupported or unsafe public contract."""


@dataclass(frozen=True)
class ArtifactReference:
    sha256: str = ""
    storage_key: str = ""
    content_type: str = ""
    kind: str = ""


@dataclass(frozen=True)
class AdaptiveAttempt:
    attempt_id: str
    phase: str
    strategy_id: str
    strategy_version: str
    started_at: str
    finished_at: str
    selected: bool
    postconditions: tuple[dict[str, Any], ...] = ()
    fallback_reason: str = ""
    artifact_refs: tuple[ArtifactReference, ...] = ()
    error: dict[str, Any] | None = None
    request_ref: str = ""
    selection: dict[str, Any] | None = None
    budget_counters: dict[str, int | float] | None = None

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["postconditions"] = [dict(item) for item in self.postconditions]
        return value


@dataclass(frozen=True)
class PlanNode:
    node_id: str
    node_type: str
    phase: str
    contract_version: int
    allowed_strategies: tuple[str, ...]
    preferred_strategies: tuple[str, ...]
    budgets: Mapping[str, int | float]
    config_digest: str = ""
    strategy_revision: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "phase": self.phase,
            "contract_version": self.contract_version,
            "allowed_strategies": list(self.allowed_strategies),
            "preferred_strategies": list(self.preferred_strategies),
            "budgets": dict(self.budgets),
            "config_digest": self.config_digest,
            "strategy_revision": self.strategy_revision,
        }


@dataclass(frozen=True)
class ExecutablePlan:
    """Sanitised, immutable execution identity persisted with every run."""

    digest: str
    contract_version: int
    project_id: str
    workflow_id: str
    workflow_version: int
    source_id: str | None
    nodes: tuple[PlanNode, ...]
    required_capabilities: tuple[str, ...]
    created_at: str
    # The run plan is also the immutable link between a compiled workflow and
    # the reviewed configuration that produced it.  Keeping references (not
    # whole preset documents) here makes a run reproducible without copying
    # source URLs, selectors or bindings into mutable workflow settings.
    revision_refs: tuple[tuple[str, str], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "digest": self.digest,
            "contract_version": self.contract_version,
            "project_id": self.project_id,
            "workflow_id": self.workflow_id,
            "workflow_version": self.workflow_version,
            "source_id": self.source_id,
            "nodes": [node.as_dict() for node in self.nodes],
            "required_capabilities": list(self.required_capabilities),
            "created_at": self.created_at,
            "revision_refs": {key: value for key, value in self.revision_refs},
        }


def graph_contract_version(graph: Mapping[str, Any]) -> int:
    """Read both accepted envelope shapes while keeping v1 the default."""

    settings = graph.get("settings") if isinstance(graph.get("settings"), Mapping) else {}
    raw = graph.get("contractVersion", settings.get("contractVersion", 1))
    try:
        version = int(raw)
    except (TypeError, ValueError) as exc:
        raise ContractError("contractVersion must be an integer") from exc
    if version not in {1, CONTRACT_VERSION}:
        raise ContractError(f"Unsupported contractVersion: {version}")
    return version


def node_type(node: Mapping[str, Any]) -> str:
    data = node.get("data") if isinstance(node.get("data"), Mapping) else {}
    return str(node.get("type") or data.get("type") or "")


def node_config(node: Mapping[str, Any]) -> dict[str, Any]:
    data = node.get("data") if isinstance(node.get("data"), Mapping) else {}
    config = node.get("config") if isinstance(node.get("config"), Mapping) else data.get("config")
    return dict(config) if isinstance(config, Mapping) else {}


def normalise_graph(graph: Mapping[str, Any]) -> dict[str, Any]:
    """Canonicalise legacy ``data`` shapes without mutating persisted JSON."""

    result = {key: value for key, value in graph.items() if key not in {"nodes", "edges"}}
    result["nodes"] = [
        {
            **dict(node),
            "id": str(node.get("id", "")),
            "type": node_type(node),
            "config": node_config(node),
        }
        for node in graph.get("nodes", [])
        if isinstance(node, Mapping)
    ]
    result["edges"] = [dict(edge) for edge in graph.get("edges", []) if isinstance(edge, Mapping)]
    return result


def v2_envelope(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return safe defaults for the v2 envelope; never return credential data."""

    raw_strategies = config.get("strategies") if isinstance(config.get("strategies"), Mapping) else {}
    raw_budgets = config.get("budgets") if isinstance(config.get("budgets"), Mapping) else {}
    mode = str(config.get("mode", "AUTO")).upper()
    if mode not in ADAPTIVE_MODES:
        raise ContractError("mode must be AUTO, ASSISTED, or MANUAL")
    try:
        budgets = {
            key: int(value)
            for key, value in raw_budgets.items()
            if key in {"maxRequests", "maxBytes", "maxPages", "maxItems", "maxDepth", "deadlineSeconds"}
            and int(value) >= 0
        }
    except (TypeError, ValueError) as exc:
        raise ContractError("budgets must contain non-negative numeric values") from exc
    return {
        "contractVersion": CONTRACT_VERSION,
        "mode": mode,
        "goal": str(config.get("goal", "")),
        "strategies": {
            "allow": _string_tuple(raw_strategies.get("allow")),
            "deny": _string_tuple(raw_strategies.get("deny")),
            "prefer": _string_tuple(raw_strategies.get("prefer")),
            "fallbackPolicy": str(raw_strategies.get("fallbackPolicy", "ON_POSTCONDITION_FAILURE")).upper(),
        },
        "budgets": budgets,
        "successCriteria": _mapping_list(config.get("successCriteria")),
        "errorPolicy": str(config.get("errorPolicy", "FAIL_REQUIRED_SCOPE")),
        "evidencePolicy": dict(config.get("evidencePolicy") or {"retainRaw": True, "retainAttempts": True}),
    }


def v2_node_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Merge envelope defaults with adapter-specific legacy configuration."""

    return {**dict(config), **v2_envelope(config)}


def adapt_v2_output(
    node_kind: str,
    output: Mapping[str, Any],
    *,
    run_context: Mapping[str, Any],
) -> dict[str, Any]:
    """Add a typed phase envelope while preserving legacy output keys.

    The compatibility adapter is deliberately additive: old configs still read
    ``body``/``records`` directly, while a v2 successor can depend on the
    stable, self-describing envelope during the incremental migration.
    """

    result = dict(output)
    records = result.get("records")
    artifacts = _artifact_refs(result)
    if node_kind == "manual_trigger":
        result["context"] = {
            "kind": "RunContext@2",
            "run_id": run_context.get("run_id"),
            "project_id": run_context.get("project_id"),
            "workflow_version_id": run_context.get("workflow_version_id"),
            "effective_run_clock": run_context.get("effective_run_clock"),
        }
    elif node_kind in {"http_request", "crawl_links"}:
        traversal = result.get("traversal") if isinstance(result.get("traversal"), Mapping) else {}
        result["source_bundle"] = {
            "kind": "SourceBundle@2",
            "seed_url": result.get("requested_url") or result.get("url", ""),
            "final_url": result.get("url", ""),
            "body": result.get("body"),
            "content_base64": result.get("content_base64"),
            "filename": result.get("filename"),
            "content_type": result.get("content_type", ""),
            "status_code": result.get("status_code"),
            "redirect_chain": result.get("redirect_chain", []),
            "artifacts": artifacts,
            "records": records if isinstance(records, list) else [],
            "errors": result.get("errors", []),
            "representations": result.get("representations", []),
            "pages": result.get("pages", []),
            "traversal": traversal,
        }
        # Preserve the actual envelope at the top level as well.  The fixed
        # v2 ports describe compatibility, not an instruction to discard
        # useful transport fields between adjacent phases.  In particular,
        # Traverse must receive Acquire.body/content_type on its first pass;
        # otherwise an HTML list only becomes visible via a nested bundle and
        # pagination/detail configuration appears to run against no listing.
        for key in ("body", "content_base64", "filename", "content_type", "status_code", "url"):
            if key not in result and key in result["source_bundle"]:
                result[key] = result["source_bundle"][key]
    elif node_kind in {"mapping", "transform"}:
        result["record_set"] = {
            "kind": "RecordSet@2",
            "records": records if isinstance(records, list) else [],
            "mapping_errors": result.get("mapping_errors", []),
            "business_records": bool(result.get("business_records")),
            "artifacts": artifacts,
        }
    elif node_kind == "validate":
        result["assessment"] = {
            "kind": "RunAssessment@2",
            "valid": bool(result.get("valid")),
            "records": records if isinstance(records, list) else [],
            "quarantined_records": result.get("quarantined_records", []),
            "errors": result.get("errors", []),
            "status": result.get("assessment_status", "PASS" if result.get("valid") else "FAIL"),
            "commit_allowed": bool(result.get("commit_allowed", result.get("valid"))),
            "reconciliation": result.get("reconciliation") or {"extracted": len(records) if isinstance(records, list) else 0},
        }
    elif node_kind == "output":
        result["receipt"] = {
            "kind": "OutputReceipt@2",
            "records": records if isinstance(records, list) else [],
            "preflight": result.get("preflight", {}),
            "artifacts": artifacts,
            "idempotency_key": result.get("idempotency_key", ""),
        }
    return result


def validate_v2_graph(graph: Mapping[str, Any], *, known_strategies: set[str] | None = None) -> list[str]:
    errors: list[str] = []
    if graph_contract_version(graph) != CONTRACT_VERSION:
        return errors
    for node in graph.get("nodes", []):
        if not isinstance(node, Mapping):
            errors.append("Every v2 node must be an object")
            continue
        kind = node_type(node)
        if kind not in PUBLIC_PHASES:
            errors.append(f"contractVersion 2 allows only public phase nodes; got {kind or '<missing>'}")
            continue
        try:
            envelope = v2_envelope(node_config(node))
        except ContractError as exc:
            errors.append(f"{kind}: {exc}")
            continue
        allowed = set(envelope["strategies"]["allow"])
        denied = set(envelope["strategies"]["deny"])
        if allowed & denied:
            errors.append(f"{kind}: a strategy cannot be both allowed and denied")
        if envelope["strategies"]["fallbackPolicy"] not in {
            "ON_POSTCONDITION_FAILURE", "ON_STRATEGY_ERROR", "ALWAYS", "NEVER",
        }:
            errors.append(f"{kind}: unsupported fallbackPolicy")
        if known_strategies and (
            unknown := (allowed | set(envelope["strategies"]["prefer"])) - known_strategies
        ):
            errors.append(f"{kind}: unknown strategies: {', '.join(sorted(unknown))}")
        allowed_by_phase = {
            "manual_trigger": {"start-input"},
            "http_request": {"acquire-http", "acquire-api", "http-api", "acquire-feed", "acquire-browser", "browser-render", "acquire-browser-xhr", "acquire-file"},
            "crawl_links": {"traverse-links", "traverse-browser"},
            "mapping": {"extract-mapping", "extract-dom", "extract-json", "extract-table", "extract-document"},
            "transform": {"process-operations"},
            "validate": {"assure-validation"},
            "output": {"output-dataset"},
        }
        # Custom registries used by deployments/tests may add their own
        # strategy IDs.  Apply this guard to the built-in catalog only; the
        # registry's Strategy.phase remains the authority for injected plugins.
        builtin_ids = set().union(*allowed_by_phase.values())
        misplaced = ((allowed | set(envelope["strategies"]["prefer"])) & builtin_ids) - allowed_by_phase[kind]
        if misplaced:
            errors.append(f"{kind}: strategies do not belong to this phase: {', '.join(sorted(misplaced))}")
    return errors


def contract_for(node_kind: str, graph: Mapping[str, Any], legacy: NodeContract) -> NodeContract:
    return V2_CONTRACTS.get(node_kind, legacy) if graph_contract_version(graph) == CONTRACT_VERSION else legacy


def compile_executable_plan(
    graph: Mapping[str, Any],
    *,
    project_id: str,
    workflow_id: str,
    workflow_version: int,
    source_id: str | None,
    revision_refs: Mapping[str, str] | None = None,
) -> ExecutablePlan:
    """Build a deterministic plan that never embeds graph secrets or values."""

    normalized = normalise_graph(graph)
    version = graph_contract_version(normalized)
    nodes: list[PlanNode] = []
    capabilities: set[str] = set()
    for node in normalized["nodes"]:
        kind = node["type"]
        config = node["config"]
        if version == CONTRACT_VERSION:
            envelope = v2_envelope(config)
            allowed = tuple(envelope["strategies"]["allow"])
            preferred = tuple(envelope["strategies"]["prefer"])
            budgets = MappingProxyType(dict(envelope["budgets"]))
        else:
            allowed = ()
            preferred = ()
            budgets = MappingProxyType({})
        nodes.append(
            PlanNode(
                node_id=node["id"],
                node_type=kind,
                phase=PUBLIC_PHASES.get(kind, "Legacy"),
                contract_version=version,
                allowed_strategies=allowed,
                preferred_strategies=preferred,
                budgets=budgets,
                config_digest=_config_digest(config),
                strategy_revision=str(config.get("strategyRevision") or config.get("strategy_revision") or ""),
            )
        )
        _required_capabilities(kind, config, capabilities)
    graph_refs = normalized.get("settings", {}).get("presetRefs", {})
    inherited_refs = graph_refs if isinstance(graph_refs, Mapping) else {}
    resolved_revision_refs = {**inherited_refs, **dict(revision_refs or {})}
    frozen_revision_refs = tuple(sorted(
        (str(key), str(value))
        for key, value in resolved_revision_refs.items()
        if value not in (None, "")
    ))
    payload = {
        "contract_version": version,
        "project_id": project_id,
        "workflow_id": workflow_id,
        "workflow_version": workflow_version,
        "source_id": source_id,
        "nodes": [node.as_dict() for node in nodes],
        "required_capabilities": sorted(capabilities),
        "revision_refs": frozen_revision_refs,
    }
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return ExecutablePlan(
        digest=digest,
        contract_version=version,
        project_id=project_id,
        workflow_id=workflow_id,
        workflow_version=workflow_version,
        source_id=source_id,
        nodes=tuple(nodes),
        required_capabilities=tuple(sorted(capabilities)),
        created_at=datetime.now(UTC).isoformat(),
        revision_refs=frozen_revision_refs,
    )


def standard_v2_graph(*, settings: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return the fixed seven-phase skeleton used by new v2 authoring flows."""

    nodes = [
        {"id": "start", "type": "manual_trigger", "config": {"contractVersion": 2}},
        {"id": "acquire", "type": "http_request", "config": {"contractVersion": 2}},
        {"id": "traverse", "type": "crawl_links", "config": {"contractVersion": 2}},
        {"id": "extract", "type": "mapping", "config": {"contractVersion": 2}},
        {"id": "process", "type": "transform", "config": {"contractVersion": 2}},
        {"id": "assure", "type": "validate", "config": {"contractVersion": 2}},
        {"id": "output", "type": "output", "config": {"contractVersion": 2}},
    ]
    return {
        "version": 1,
        "contractVersion": CONTRACT_VERSION,
        "settings": dict(settings or {}),
        "nodes": nodes,
        "edges": [
            {"source": nodes[index]["id"], "target": nodes[index + 1]["id"]}
            for index in range(len(nodes) - 1)
        ],
    }


def _required_capabilities(kind: str, config: Mapping[str, Any], capabilities: set[str]) -> None:
    strategies = config.get("strategies") if isinstance(config.get("strategies"), Mapping) else {}
    selected = " ".join(
        str(item).lower()
        for value in (strategies.get("allow", []), strategies.get("prefer", []))
        for item in (value if isinstance(value, list) else [value])
    )
    if kind == "http_request" and ("browser" in selected or str(config.get("transport", "")).upper() == "PLAYWRIGHT"):
        capabilities.add("browser")
    if kind == "crawl_links" and (
        "browser" in selected
        or any(str(config.get(key, "")).upper() == "PLAYWRIGHT" for key in ("listing_fetch_mode", "detail_fetch_mode"))
    ):
        capabilities.add("browser")
    if kind == "mapping" and any("document" in item for item in selected.split()):
        capabilities.add("documents")
    if kind == "transform" and ("llm" in selected or bool(config.get("allowSemantic"))):
        capabilities.add("llm")
    if kind == "output" and "export" in selected:
        capabilities.add("exports")


def _string_tuple(value: Any) -> tuple[str, ...]:
    values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    return tuple(dict.fromkeys(str(item) for item in values if str(item).strip()))


def _mapping_list(value: Any) -> tuple[dict[str, Any], ...]:
    return tuple(dict(item) for item in value if isinstance(item, Mapping)) if isinstance(value, list) else ()


def _artifact_refs(output: Mapping[str, Any]) -> list[dict[str, str]]:
    candidates: list[Any] = []
    if isinstance(output.get("artifact"), Mapping):
        candidates.append(output["artifact"])
    if isinstance(output.get("artifacts"), list):
        candidates.extend(output["artifacts"])
    refs = [
        asdict(
            ArtifactReference(
                sha256=str(item.get("sha256", "")),
                storage_key=str(item.get("storage_key", "")),
                content_type=str(item.get("content_type", "")),
                kind=str(item.get("kind", "")),
            )
        )
        for item in candidates
        if isinstance(item, Mapping)
    ]
    return [dict(item) for item in refs]


def _config_digest(config: Mapping[str, Any]) -> str:
    """Fingerprint immutable behaviour without serialising credentials.

    A plan must change when a selector, assertion, URL policy, schema or
    operation changes, but must never persist the corresponding raw secrets.
    """

    stable = _sanitise_plan_config(config)
    return hashlib.sha256(
        json.dumps(stable, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


def _sanitise_plan_config(value: Any, key: str = "") -> Any:
    lowered = key.lower()
    if any(token in lowered for token in ("secret", "token", "password", "cookie", "authorization", "api_key")):
        return "<redacted>"
    if isinstance(value, Mapping):
        return {str(item_key): _sanitise_plan_config(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitise_plan_config(item) for item in value]
    if isinstance(value, str) and "{{secret." in value.lower():
        return "<secret-template>"
    if callable(value):
        return "<runtime-callable>"
    return value
