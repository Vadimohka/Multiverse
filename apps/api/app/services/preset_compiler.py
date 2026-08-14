"""Compilation of immutable source presets onto universal v2 blueprints.

This module intentionally accepts declarative JSON only.  It never evaluates
user code and has no source-specific branches: a site becomes configuration
merged into the fixed seven public facades.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from workflow_engine import PUBLIC_PHASES, normalise_graph, standard_v2_graph, validate_dag
from workflow_engine.contracts import node_config, node_type
from workflow_engine.strategies import DEFAULT_STRATEGIES

PHASE_TYPES = {phase.lower(): node_kind for node_kind, phase in PUBLIC_PHASES.items()}
ALLOWED_STATUSES = frozenset({"DRAFT", "VERIFIED", "BLOCKED", "DEPRECATED"})
FORBIDDEN_KEYS = frozenset({
    "python", "javascript", "script", "code", "callable", "encrypted_api_key",
    "encrypted_password", "encrypted_value", "storage_state",
})


class PresetCompilationError(ValueError):
    """A preset cannot be safely compiled into an executable universal graph."""


@dataclass(frozen=True)
class CompilationResult:
    graph: dict[str, Any]
    report: dict[str, Any]


def validate_status(status: str) -> str:
    normalized = str(status or "DRAFT").upper()
    if normalized not in ALLOWED_STATUSES:
        raise PresetCompilationError("status must be DRAFT, VERIFIED, BLOCKED, or DEPRECATED")
    return normalized


def validate_blueprint_graph(graph: Mapping[str, Any]) -> dict[str, Any]:
    normalized = normalise_graph(graph)
    if int(normalized.get("contractVersion", normalized.get("settings", {}).get("contractVersion", 1))) != 2:
        raise PresetCompilationError("A blueprint must use contractVersion 2")
    expected = list(PUBLIC_PHASES)
    actual = [node_type(node) for node in normalized.get("nodes", [])]
    if actual != expected:
        raise PresetCompilationError("A blueprint must contain the ordered seven public phases exactly once")
    errors = validate_dag(normalized)
    if errors:
        raise PresetCompilationError("; ".join(errors))
    return normalized


def compile_preset(
    blueprint_graph: Mapping[str, Any] | None,
    preset: Mapping[str, Any],
    *,
    known_strategies: set[str] | None = None,
) -> CompilationResult:
    """Apply a source preset and validate the resulting seven-role graph.

    ``nodes`` is keyed by phase name (``acquire``) or historic public type
    key (``http_request``).  No other node names may be smuggled through.
    Settings intentionally carry refs and policy values, while concrete node
    behavior stays under the matching public phase.
    """

    base = (
        validate_blueprint_graph(blueprint_graph)
        if blueprint_graph
        else standard_v2_graph()
    )
    config = _mapping(preset.get("config_json") if "config_json" in preset else preset)
    if config.get("apiVersion") and str(config["apiVersion"]) != "multiverse.io/v2":
        raise PresetCompilationError("Unsupported preset apiVersion")
    if config.get("kind") and str(config["kind"]) != "SourcePreset":
        raise PresetCompilationError("Preset kind must be SourcePreset")
    _reject_forbidden(config)
    raw_nodes = _mapping(config.get("nodes"))
    allowed_node_keys = set(PHASE_TYPES) | set(PUBLIC_PHASES)
    unknown_nodes = sorted(set(raw_nodes) - allowed_node_keys)
    if unknown_nodes:
        raise PresetCompilationError(f"Unknown preset phase(s): {', '.join(unknown_nodes)}")

    graph = deepcopy(base)
    graph["contractVersion"] = 2
    graph.setdefault("settings", {})["contractVersion"] = 2
    bindings = _mapping(config.get("bindings"))
    policies = _mapping(config.get("policies"))
    graph["settings"] = {
        **graph["settings"],
        "presetRefs": {
            **_string_mapping(bindings),
            **_string_mapping({
                "sourcePolicyRef": preset.get("source_policy_ref") or config.get("sourcePolicyRef"),
                "datasetSchemaRef": preset.get("dataset_schema_ref") or config.get("datasetSchemaRef"),
            }),
        },
        "policies": deepcopy(policies),
    }
    if "budgets" in policies:
        graph["settings"]["budgets"] = deepcopy(policies["budgets"])

    applied: list[str] = []
    for node in graph["nodes"]:
        node_kind = node_type(node)
        phase_key = PUBLIC_PHASES[node_kind].lower()
        override = raw_nodes.get(phase_key, raw_nodes.get(node_kind, {}))
        if override is None:
            override = {}
        if not isinstance(override, Mapping):
            raise PresetCompilationError(f"{phase_key} config must be an object")
        merged = _deep_merge(node_config(node), dict(override))
        # Global budgets establish an upper capability budget; a phase can set
        # a smaller one, never introduce arbitrary executable settings.
        if isinstance(policies.get("budgets"), Mapping):
            merged["budgets"] = {**dict(policies["budgets"]), **_mapping(merged.get("budgets"))}
        _reject_forbidden(merged, phase_key)
        node["config"] = merged
        if override:
            applied.append(phase_key)

    errors = validate_dag(graph, known_strategies=known_strategies or DEFAULT_STRATEGIES.known_ids())
    if errors:
        raise PresetCompilationError("; ".join(errors))
    return CompilationResult(
        graph=graph,
        report={
            "contractVersion": 2,
            "kind": "SourcePreset",
            "appliedPhases": applied,
            "warnings": [],
            "unresolved": [],
        },
    )


def legacy_conversion_report(graph: Mapping[str, Any]) -> dict[str, Any]:
    """Explain exactly which legacy mechanics need a preset decision."""

    normalized = normalise_graph(graph)
    nodes = normalized.get("nodes", [])
    public_types = set(PUBLIC_PHASES)
    unresolved = [
        {"nodeId": node.get("id"), "nodeType": node_type(node), "reason": "legacy_node_requires_facade_configuration"}
        for node in nodes
        if node_type(node) not in public_types
    ]
    return {
        "sourceContractVersion": normalized.get("contractVersion", normalized.get("settings", {}).get("contractVersion", 1)),
        "targetContractVersion": 2,
        "warnings": ["Legacy graph was not executed or silently rewritten."],
        "unresolved": unresolved,
        "convertible": not unresolved,
    }


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _string_mapping(value: Mapping[str, Any]) -> dict[str, str]:
    return {str(key): str(item) for key, item in value.items() if item not in (None, "")}


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(base))
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[str(key)] = _deep_merge(result[key], value)
        else:
            result[str(key)] = deepcopy(value)
    return result


def _reject_forbidden(value: Any, path: str = "") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            name = str(key)
            if name.lower() in FORBIDDEN_KEYS:
                raise PresetCompilationError(f"Forbidden executable or secret field at {path + '.' if path else ''}{name}")
            _reject_forbidden(child, f"{path}.{name}" if path else name)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_forbidden(child, f"{path}[{index}]")
