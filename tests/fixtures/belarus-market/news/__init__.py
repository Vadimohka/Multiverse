"""Fixture helpers for declarative Belarus market news profiles."""

from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx
from app.services.belarus_market_pack import _preset_config, passport_sources
from app.services.preset_compiler import compile_preset
from workflow_engine.nodes import HTTPRequestNode, MappingNode, TransformNode, ValidateNode, canonical_url
from workflow_engine.strategies import TraverseFacadeStrategy
from workflow_engine.types import ExecutionContext


@dataclass(frozen=True)
class NewsFixtureResult:
    records: list[dict[str, Any]]
    assessment_status: str
    assessment_codes: list[str]
    traversal: dict[str, Any]


def _display_source_dates(records: list[dict[str, Any]], timezone: str) -> list[dict[str, Any]]:
    """Present normalized engine timestamps in the profile's source timezone."""
    localized: list[dict[str, Any]] = []
    for record in records:
        row = dict(record)
        value = row.get("source_published_at")
        if value:
            row["source_published_at"] = datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            ).astimezone(ZoneInfo(timezone)).isoformat()
        localized.append(row)
    return localized


def _fixture_config(source_key: str, window: Mapping[str, Any]) -> tuple[Any, dict[str, Any]]:
    source = {item.key: item for item in passport_sources()}[source_key]
    config = deepcopy(_preset_config(source))
    lower = str(window["from"])
    upper = str(window["to"])
    traverse = config["nodes"]["traverse"]
    traverse["dateBoundary"].update({"lowerBound": lower, "upperBound": upper})
    traverse["pagination"] = {
        **dict(traverse.get("pagination") or {}),
        "maxPages": int(window.get("max_pages", 1)),
    }
    config["nodes"]["assure"]["dateWindow"].update({"from": lower, "to": upper})
    return source, config


async def run_news_fixture(
    source_key: str,
    listing_html: str,
    details: Mapping[str, str],
    window: Mapping[str, Any],
) -> NewsFixtureResult:
    """Execute an existing news profile against a hermetic HTML transport.

    ``max_pages`` is a fixture-run budget: a single listing fixture is one
    page unless a test explicitly requests more.  The adapter also renders
    the profile's run-window placeholders because individual node calls take
    their config as already-resolved values.
    """

    source, config = _fixture_config(source_key, window)
    compiled = compile_preset(None, config).graph
    nodes = {node["id"]: node["config"] for node in compiled["nodes"]}
    public_resolver = lambda _host, _port: ["93.184.216.34"]
    for phase in ("acquire", "traverse"):
        nodes[phase]["egress_resolver"] = public_resolver
    expected_details = {canonical_url(str(url)): body for url, body in details.items()}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if canonical_url(url) == canonical_url(source.url):
            body = listing_html
        elif canonical_url(url) in expected_details:
            body = expected_details[canonical_url(url)]
        else:
            return httpx.Response(404, request=request, text="fixture URL not supplied")
        return httpx.Response(
            200,
            request=request,
            text=body,
            headers={"content-type": "text/html; charset=utf-8"},
        )

    original_async_client = httpx.AsyncClient

    def fixture_client(**kwargs: Any) -> httpx.AsyncClient:
        return original_async_client(transport=httpx.MockTransport(handler), **kwargs)

    context = ExecutionContext(
        run_id="news-fixture",
        project_id="belarus-market",
        workflow_version_id="fixture-v2",
        variables={"source": {"url": source.url}},
    )
    with ExitStack() as stack:
        stack.enter_context(patch("workflow_engine.nodes.httpx.AsyncClient", fixture_client))
        acquired = await HTTPRequestNode().execute(context, {}, nodes["acquire"])
        traversed = await TraverseFacadeStrategy("traverse-links", "Traverse", version="2").execute(
            None, None, context, acquired, nodes["traverse"]
        )

    extracted = await MappingNode().execute(context, traversed, nodes["extract"])
    processed = await TransformNode().execute(context, extracted, nodes["process"])
    assessment = await ValidateNode().execute(
        context,
        {**processed, "traversal": traversed["traversal"]},
        {**nodes["assure"], "fail_on_error": False},
    )
    codes = list(assessment["assessment_codes"])
    if any(str(error.get("state") or "").startswith("detail:") for error in traversed["errors"]):
        codes.append("DETAIL_FAILURE")
    if traversed["traversal"].get("stop_reason") == "REPEATED_PAGE":
        codes.append("REPEATED_PAGE")
    detail_fields = nodes["traverse"].get("detail", {}).get("fields", [])
    published_field = next(
        (field for field in detail_fields if field.get("name") == "source_published_at"), {}
    )
    return NewsFixtureResult(
        records=_display_source_dates(
            assessment["records"], str(published_field.get("timezone") or "UTC")
        ),
        assessment_status=str(assessment["assessment_status"]),
        assessment_codes=list(dict.fromkeys(codes)),
        traversal=traversed["traversal"],
    )
