from typing import Any

from app.models import Source


def queue_for_graph(graph: dict[str, Any], source: Source | None = None) -> str:
    """Select a worker from explicit graph/source capabilities."""
    nodes = graph.get("nodes", [])
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
