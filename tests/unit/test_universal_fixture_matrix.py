import json
from pathlib import Path

import httpx
import pytest
from app.services import source_profiler
from app.services.source_profiler import build_extractor_suggestion, detect_repeating_candidates
from bs4 import BeautifulSoup
from workflow_engine.nodes import (
    CrawlLinksNode,
    ExtractRepeatingListNode,
    JSONPathNode,
    PaginationNode,
    ParseTableNode,
    extract_article_record,
)
from workflow_engine.types import ExecutionContext

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "universal"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def context() -> ExecutionContext:
    return ExecutionContext(run_id="fixtures", project_id="project", workflow_version_id="1")


@pytest.mark.asyncio
async def test_simple_repeating_cards_fixture():
    result = await ExtractRepeatingListNode().execute(
        context(),
        {"html": fixture("cards.html")},
        {
            "container_selector": "article.card",
            "fields": [
                {"name": "title", "selector": "h2"},
                {"name": "url", "selector": "a[href]", "attribute": "href"},
                {"name": "value", "selector": ".value"},
            ],
        },
    )
    assert [{key: row[key] for key in ("title", "url", "value")} for row in result["records"]] == [
        {"title": "First item", "url": "/details/one", "value": "10"},
        {"title": "Second item", "url": "/details/two", "value": "20"},
    ]


@pytest.mark.asyncio
async def test_list_to_detail_fixture(monkeypatch):
    details = {
        "/details/one": fixture("detail-one.html"),
        "/details/two": fixture("detail-two.html"),
    }

    async def request(_client, method, url, _policy, **_kwargs):
        response = httpx.Response(
            200,
            request=httpx.Request(method, url),
            text=details[httpx.URL(url).path],
            headers={"content-type": "text/html"},
        )
        response.extensions["fetch_attempts"] = []
        return response

    monkeypatch.setattr("workflow_engine.nodes.request_with_policy", request)
    result = await CrawlLinksNode().execute(
        context(),
        {"url": "https://example.test/list", "html": fixture("list.html")},
        {
            "input_path": "html",
            "link_selector": "a.entry",
            "detail_fields": [
                {"name": "title", "selector": "h1"},
                {"name": "body", "selector": ".content"},
            ],
            "save_artifacts": False,
            "delay_ms": 0,
        },
    )
    assert sorted((row["title"], row["body"]) for row in result["records"]) == [
        ("First detail", "Alpha body"),
        ("Second detail", "Beta body"),
    ]


@pytest.mark.asyncio
async def test_html_table_fixture():
    result = await ParseTableNode().execute(
        context(),
        {"html": fixture("table.html")},
        {"selector": "#measurements"},
    )
    assert result["records"] == [{"Code": "A", "Value": "10"}, {"Code": "B", "Value": "20"}]


@pytest.mark.asyncio
async def test_http_next_link_fixture_is_bounded_and_merged(monkeypatch):
    pages = {"/pages/1": fixture("next-1.html"), "/pages/2": fixture("next-2.html")}
    async_client = httpx.AsyncClient

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            text=pages[request.url.path],
            headers={"content-type": "text/html"},
        )

    monkeypatch.setattr(
        "workflow_engine.nodes.httpx.AsyncClient",
        lambda **kwargs: async_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    listing, _ = await CrawlLinksNode()._load_listing(
        context(),
        {},
        {
            "listing_url": "https://example.test/pages/1",
            "listing_fetch_mode": "HTTP",
            "pagination_enabled": True,
            "pagination_next_selector": "a[rel='next']",
            "pagination_max_pages": 2,
            "save_artifacts": False,
        },
    )
    assert "Page one" in listing and "Page two" in listing


@pytest.mark.asyncio
async def test_query_parameter_pagination_fixture(monkeypatch):
    expected = json.loads(fixture("query-pages.json"))["pages"]

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url):
            page = int(httpx.URL(url).params["page"])
            return httpx.Response(200, request=httpx.Request("GET", url), text=f"<main>Page {page}</main>")

    monkeypatch.setattr("workflow_engine.nodes.httpx.AsyncClient", lambda **_kwargs: Client())
    result = await PaginationNode().execute(
        context(),
        {},
        {"url_template": "https://example.test/items?page={{page}}", "max_pages": 2},
    )
    assert [item["url"] for item in result["pages"]] == [item["url"] for item in expected]


def test_tab_fixture_exposes_all_semantic_collections():
    items = CrawlLinksNode()._listing_items(
        fixture("tabs.html"),
        {"link_selector": "[role='tabpanel'] a[href]"},
    )
    assert [item["url"] for item in items] == ["/one", "/two"]


@pytest.mark.asyncio
async def test_js_shell_fixture_recommends_browser(monkeypatch):
    response = httpx.Response(
        200,
        request=httpx.Request("GET", "https://example.test/app"),
        text=fixture("js-shell.html"),
        headers={"content-type": "text/html"},
    )

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            return response

    async def unavailable(result, _url, _timeout):
        result["rendered_text_length"] = None

    monkeypatch.setattr(source_profiler.httpx, "AsyncClient", lambda **_kwargs: Client())
    monkeypatch.setattr(source_profiler, "enrich_with_playwright", unavailable)
    profile = await source_profiler.profile_url("https://example.test/app")
    assert profile["recommended_fetch_mode"] == "PLAYWRIGHT"
    assert profile["requires_javascript"] is True


@pytest.mark.asyncio
async def test_json_api_fixture():
    result = await JSONPathNode().execute(
        context(),
        {"body": json.loads(fixture("json-api.json"))},
        {"path": "$.items[*]"},
    )
    assert result["records"] == [
        {"id": "one", "title": "First"},
        {"id": "two", "title": "Second"},
    ]


def test_jsonld_publication_and_missing_publication_fixtures():
    candidate = {"record_id": "one", "item": {}, "fetched_at": "2026-08-11T06:00:00Z"}
    timed = extract_article_record(
        fixture("jsonld-date.html"),
        "https://example.test/one",
        candidate,
        {},
        None,
    )
    undated = extract_article_record(
        fixture("no-date.html"),
        "https://example.test/two",
        candidate,
        {},
        None,
    )
    assert timed["published_at"] == "2026-08-10T09:34:56Z"
    assert undated["published_at"] == ""


def test_unusual_classes_do_not_break_profiler():
    candidates = detect_repeating_candidates(BeautifulSoup(fixture("unusual-classes.html"), "lxml"))
    assert any(item["selector"] == "section.x7_q-item" for item in candidates)


def test_profiler_selects_structured_content_over_competing_navigation():
    candidates = detect_repeating_candidates(BeautifulSoup(fixture("competing-containers.html"), "lxml"))
    suggestion = build_extractor_suggestion(candidates)
    assert suggestion["container_selector"] == "article.content-unit"
    assert {field["name"] for field in suggestion["fields"]} >= {
        "url",
        "title",
        "source_published_at",
    }
