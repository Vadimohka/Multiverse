"""Compile declarative news profile contracts into legacy executable graphs.

Some official sources need ``crawl_links`` capabilities that the seven-phase
facade does not expose (detail API requests, document attachments and related
JSON resources).  Their profiles retain that executable contract as data; this
module only supplies the source-independent topology and runtime bindings.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

NEWS_PROFILE_REGISTRY = (
    Path(__file__).resolve().parents[4]
    / "presets"
    / "belarus-market"
    / "news"
    / "source-profiles.json"
)


def compile_news_profile_graph(
    profile: Mapping[str, Any], *, source_id: str, dataset_id: str
) -> dict[str, Any]:
    """Compile one profile's declarative ``installedGraph`` contract."""

    contract = profile.get("installedGraph")
    if not isinstance(contract, Mapping):
        raise ValueError("News profile has no declarative installedGraph contract")

    required = ("settings", "crawl", "mapping", "validate", "output")
    missing = [key for key in required if not isinstance(contract.get(key), Mapping)]
    if missing:
        raise ValueError(
            "News installedGraph is missing mapping sections: " + ", ".join(missing)
        )

    has_select = isinstance(contract.get("select"), Mapping)
    if has_select:
        stages = [
            ("trigger", "manual_trigger", {}, 30),
            ("crawl", "crawl_links", contract["crawl"], 280),
            ("select", "transform", contract["select"], 540),
            ("mapping", "mapping", contract["mapping"], 780),
            ("validate", "validate", contract["validate"], 1030),
            ("output", "output", contract["output"], 1270),
        ]
    else:
        stages = [
            ("trigger", "manual_trigger", {}, 30),
            ("crawl", "crawl_links", contract["crawl"], 280),
            ("mapping", "mapping", contract["mapping"], 590),
            ("validate", "validate", contract["validate"], 850),
            ("output", "output", contract["output"], 1120),
        ]

    settings = deepcopy(dict(contract["settings"]))
    settings.update({"source_id": source_id, "dataset_id": dataset_id})
    nodes = [
        {
            "id": node_id,
            "type": node_type,
            "position": {"x": x, "y": 180},
            "config": deepcopy(dict(config)),
        }
        for node_id, node_type, config, x in stages
    ]
    edges = [
        {
            "id": f"e{index}",
            "source": stages[index - 1][0],
            "target": stages[index][0],
        }
        for index in range(1, len(stages))
    ]
    return {"version": 1, "settings": settings, "nodes": nodes, "edges": edges}


def load_news_profile_graph(
    source_key: str, *, source_id: str, dataset_id: str
) -> dict[str, Any]:
    """Load and compile one installed graph from the canonical registry."""

    registry = json.loads(NEWS_PROFILE_REGISTRY.read_text(encoding="utf-8"))
    profile = (registry.get("sources") or {}).get(source_key)
    if not isinstance(profile, Mapping):
        raise ValueError(f"News source {source_key} has no declarative profile")
    return compile_news_profile_graph(
        profile, source_id=source_id, dataset_id=dataset_id
    )
