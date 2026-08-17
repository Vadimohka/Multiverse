"""Fixture helpers for declarative Belarus market news profiles."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx
from app.services.belarus_market_pack import _preset_config, passport_sources
from app.services.news_profile_graph import load_news_profile_graph
from app.services.preset_compiler import compile_preset
from bs4 import BeautifulSoup
from workflow_engine.nodes import (
    CrawlLinksNode,
    HTTPRequestNode,
    MappingNode,
    TransformNode,
    ValidateNode,
    canonical_url,
)
from workflow_engine.strategies import TraverseFacadeStrategy
from workflow_engine.types import ExecutionContext


@dataclass(frozen=True)
class NewsFixturePagination:
    visited_pages: int


@dataclass(frozen=True)
class NewsFixtureResult:
    records: list[dict[str, Any]]
    assessment_status: str
    assessment_codes: list[str]
    traversal: dict[str, Any]
    pagination: NewsFixturePagination


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
    *,
    pages: Mapping[str, str] | None = None,
    graph: Mapping[str, Any] | None = None,
) -> NewsFixtureResult:
    """Execute an existing news profile against a hermetic HTML transport.

    ``max_pages`` is a fixture-run budget: a single listing fixture is one
    page unless a test explicitly requests more.  The adapter also renders
    the profile's run-window placeholders because individual node calls take
    their config as already-resolved values.
    """

    source, config = _fixture_config(source_key, window)
    if graph is not None and any(
        node.get("id") == "crawl" and node.get("type") == "crawl_links"
        for node in graph.get("nodes", [])
    ):
        return await _run_legacy_installed_fixture(
            source,
            graph,
            listing_html,
            details,
            window,
            pages=pages,
        )
    compiled = compile_preset(None, config).graph
    nodes = {node["id"]: node["config"] for node in compiled["nodes"]}
    def public_resolver(_host: str, _port: int) -> list[str]:
        return ["93.184.216.34"]
    for phase in ("acquire", "traverse"):
        nodes[phase]["egress_resolver"] = public_resolver
    expected_details = {canonical_url(str(url)): body for url, body in details.items()}
    expected_pages = {
        canonical_url(str(url)): body for url, body in (pages or {}).items()
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if canonical_url(url) == canonical_url(source.url):
            body = listing_html
        elif canonical_url(url) in expected_pages:
            body = expected_pages[canonical_url(url)]
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
    traversal = traversed["traversal"]
    return NewsFixtureResult(
        records=_display_source_dates(
            assessment["records"], str(published_field.get("timezone") or "UTC")
        ),
        assessment_status=str(assessment["assessment_status"]),
        assessment_codes=list(dict.fromkeys(codes)),
        traversal=traversal,
        pagination=NewsFixturePagination(
            visited_pages=len(traversal.get("checkpoint", {}).get("completed_urls", []))
        ),
    )


async def _run_legacy_installed_fixture(
    source: Any,
    graph: Mapping[str, Any],
    listing_html: str,
    details: Mapping[str, str],
    window: Mapping[str, Any],
    *,
    pages: Mapping[str, str] | None = None,
) -> NewsFixtureResult:
    """Execute the installed legacy crawl/mapping/validation node contract."""

    nodes = {node["id"]: deepcopy(node["config"]) for node in graph["nodes"]}
    crawl_config = nodes["crawl"]
    crawl_config["pagination_max_pages"] = int(window.get("max_pages", 1))
    crawl_config["max_pages"] = int(window.get("max_pages", 1))
    crawl_config["egress_resolver"] = lambda _host, _port: ["93.184.216.34"]
    expected_details = {canonical_url(str(url)): body for url, body in details.items()}
    expected_pages = {
        canonical_url(str(url)): body for url, body in (pages or {}).items()
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if canonical_url(url) == canonical_url(source.url):
            return httpx.Response(
                200,
                request=request,
                text=listing_html,
                headers={"content-type": "text/html; charset=utf-8"},
            )
        if canonical_url(url) in expected_pages:
            return httpx.Response(
                200,
                request=request,
                text=expected_pages[canonical_url(url)],
                headers={"content-type": "text/html; charset=utf-8"},
            )
        if request.url.path == "/solo/calendar":
            record_id = str(request.url.params.get("link") or "")
            detail_body = next(
                (
                    body
                    for detail_url, body in expected_details.items()
                    if f"/{record_id}/" in urlsplit(detail_url).path
                ),
                None,
            )
            if detail_body is None:
                return httpx.Response(404, request=request, json={"solo": {"notFound": True}})
            detail = BeautifulSoup(detail_body, "lxml")
            article = detail.select_one("article") or detail.body or detail
            title = detail.select_one("h1")
            return httpx.Response(
                200,
                request=request,
                json={
                    "day": str(request.url.params.get("sDay") or ""),
                    "solo": {
                        "title": title.get_text(" ", strip=True) if title else "",
                        "html": str(article),
                        "tags": [],
                        "categoryName": "",
                        "notFound": False,
                    },
                },
            )
        if canonical_url(url) in expected_details:
            return httpx.Response(
                200,
                request=request,
                text=expected_details[canonical_url(url)],
                headers={"content-type": "text/html; charset=utf-8"},
            )
        return httpx.Response(404, request=request, text="fixture URL not supplied")

    original_async_client = httpx.AsyncClient

    def fixture_client(**kwargs: Any) -> httpx.AsyncClient:
        return original_async_client(transport=httpx.MockTransport(handler), **kwargs)

    async def fixture_browser(_self: Any, _context: Any, _inputs: Any, config: Any) -> dict:
        url = str(config.get("url") or source.url)
        return {
            "url": url,
            "html": listing_html,
            "body": listing_html,
            "network": [],
            "tab_count": 0,
            "tab_labels": [],
        }

    context = ExecutionContext(
        run_id="news-fixture",
        project_id="belarus-market",
        workflow_version_id="fixture-installed",
        variables={
            "source": {
                "url": source.url,
                "base_url": source.url,
                "fetch_mode": "PLAYWRIGHT",
            }
        },
    )
    with ExitStack() as stack:
        stack.enter_context(patch("workflow_engine.nodes.httpx.AsyncClient", fixture_client))
        stack.enter_context(
            patch("workflow_engine.nodes.BrowserOpenNode.execute", fixture_browser)
        )
        crawled = await CrawlLinksNode().execute(context, {}, crawl_config)

    selected = crawled
    if "select" in nodes:
        selected = await TransformNode().execute(context, crawled, nodes["select"])
    mapped = await MappingNode().execute(context, selected, nodes["mapping"])
    traversal = {
        "reconciliation": {
            "discovered": int(crawled.get("discovered") or 0),
            "succeeded": int(crawled.get("count") or 0),
            "intentionally_skipped": max(
                int(crawled.get("count") or 0) - len(mapped.get("records") or []), 0
            ),
            "failed": len(crawled.get("errors") or []),
            "duplicate": 0,
        },
        "checkpoint": {"completed_urls": list(crawled.get("completed_urls") or [])},
        "stop_reason": "COMPLETED",
    }
    assessed = await ValidateNode().execute(
        context,
        {**mapped, "traversal": traversal, "errors": crawled.get("errors") or []},
        {**nodes["validate"], "fail_on_error": False},
    )
    codes = list(assessed["assessment_codes"])
    if crawled.get("errors"):
        codes.append("DETAIL_FAILURE")
    published_field = next(
        (
            field
            for field in crawl_config.get("detail_fields", [])
            if field.get("name") == "source_published_at"
        ),
        {},
    )
    return NewsFixtureResult(
        records=_display_source_dates(
            assessed["records"], str(published_field.get("timezone") or "UTC")
        ),
        assessment_status=str(assessed["assessment_status"]),
        assessment_codes=list(dict.fromkeys(codes)),
        traversal=traversal,
        pagination=NewsFixturePagination(
            visited_pages=1 if crawled.get("listing_diagnostics") else 0
        ),
    )


async def run_news_fixture_from_files(
    source_key: str, *, graph: Mapping[str, Any] | None = None
) -> NewsFixtureResult:
    """Load a retained fixture set using generic filename/profile conventions."""

    root = Path(__file__).resolve().parent
    listing = (root / f"{source_key}-list.html").read_text(encoding="utf-8")
    detail = (root / f"{source_key}-detail.html").read_text(encoding="utf-8")
    detail_variants = {
        path.stem.removeprefix(f"{source_key}-detail-"): path.read_text(
            encoding="utf-8"
        )
        for path in root.glob(f"{source_key}-detail-*.html")
    }
    source, config = _fixture_config(
        source_key,
        {"from": "2000-01-01T00:00:00+03:00", "to": "2100-01-01T00:00:00+03:00"},
    )
    if graph is None:
        try:
            graph = load_news_profile_graph(
                source_key, source_id="fixture-source", dataset_id="fixture-dataset"
            )
        except ValueError:
            graph = None
    graph_nodes = {
        node["id"]: node.get("config", {}) for node in (graph or {}).get("nodes", [])
    }
    traverse = config["nodes"]["traverse"]
    crawl = graph_nodes.get("crawl") or {}
    selector = str(
        crawl.get("link_selector") or traverse.get("detail", {}).get("selector") or ""
    )
    pagination = crawl or traverse.get("pagination") or {}
    fixture_pages = sorted(root.glob(f"{source_key}-page-*.html"))
    page_bodies: dict[str, str] = {}
    bodies = [listing]
    template = str(pagination.get("urlTemplate") or "")
    for page_number, path in enumerate(fixture_pages[1:], start=2):
        body = path.read_text(encoding="utf-8")
        if template:
            page_bodies[template.replace("{{page}}", str(page_number))] = body
        bodies.append(body)

    details: dict[str, str] = {}
    for body in bodies:
        soup = BeautifulSoup(body, "lxml")
        for link in soup.select(selector):
            href = str(link.get("href") or "").strip()
            if href:
                detail_slug = Path(urlsplit(href).path).name
                details[urljoin(source.url, href)] = detail_variants.get(
                    detail_slug, detail
                )

    window = {
        "from": "2000-01-01T00:00:00+03:00",
        "to": "2100-01-01T00:00:00+03:00",
        "max_pages": max(1, len(fixture_pages)),
    }
    return await run_news_fixture(
        source_key, listing, details, window, pages=page_bodies, graph=graph
    )
