from typing import Any

from app.models import Source
from workflow_engine import compile_executable_plan


def queue_for_graph(graph: dict[str, Any], source: Source | None = None) -> str:
    """Select a worker from explicit graph/source capabilities."""
    nodes = graph.get("nodes", [])
    # Compiled v2 capability declarations win over legacy heuristic routing.
    # The placeholder IDs are intentionally not persisted: routing only needs
    # the deterministic plan capabilities and source-scoped policy context.
    plan = compile_executable_plan(
        graph,
        project_id=source.project_id if source else "",
        workflow_id="routing",
        workflow_version=0,
        source_id=source.id if source else None,
    )
    required = set(plan.required_capabilities)
    if "browser" in required:
        return "browser"
    if "documents" in required:
        return "documents"
    if "llm" in required:
        return "llm"
    if "exports" in required:
        return "exports"
    types = {node.get("type") or node.get("data", {}).get("type") for node in nodes}
    profile = (source.settings or {}).get("profile", {}) if source else {}
    configured_browser = any(
        str((node.get("config") or node.get("data", {}).get("config", {})).get(key) or "").upper() == "PLAYWRIGHT"
        for node in nodes
        for key in ("listing_fetch_mode", "detail_fetch_mode")
    )
    source_browser = bool(source and (source.fetch_mode or "").upper() == "PLAYWRIGHT")
    if "browser_open" in types or configured_browser or profile.get("requires_javascript") or source_browser:
        return "browser"
    if types & {"parse_document", "download_file"}:
        return "documents"
    if types & {"llm_extract", "llm_classify"}:
        return "llm"
    if "export_file" in types:
        return "exports"
    return "default"
