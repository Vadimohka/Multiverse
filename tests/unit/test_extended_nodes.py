import base64
import io

import pytest
from openpyxl import Workbook
from workflow_engine import WorkflowEngine
from workflow_engine.nodes import (
    BrowserOpenNode,
    CrawlLinksNode,
    FormulaNode,
    LLMExtractNode,
    MappingNode,
    ParseDocumentNode,
    ParseTableNode,
    collect_paginated_html,
    dedupe_extracted_records,
    extract_article_record,
)
from workflow_engine.types import ExecutionContext


def context() -> ExecutionContext:
    return ExecutionContext(run_id="test", project_id="project", workflow_version_id="1")


@pytest.mark.asyncio
async def test_parse_table_with_headers():
    html = """
    <table id="rates"><tr><th>Банк</th><th>Ставка</th></tr>
    <tr><td>А</td><td>12,5%</td></tr><tr><td>Б</td><td>10%</td></tr></table>
    """
    result = await ParseTableNode().execute(context(), {"html": html}, {"selector": "#rates"})
    assert result["records"] == [
        {"Банк": "А", "Ставка": "12,5%"},
        {"Банк": "Б", "Ставка": "10%"},
    ]


@pytest.mark.asyncio
async def test_parse_xlsx_document_node():
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["bank", "rate"])
    sheet.append(["Demo", 12.5])
    buffer = io.BytesIO()
    workbook.save(buffer)
    result = await ParseDocumentNode().execute(
        context(),
        {
            "content_base64": base64.b64encode(buffer.getvalue()).decode(),
            "filename": "rates.xlsx",
        },
        {},
    )
    assert result["records"][0]["bank"] == "Demo"


@pytest.mark.asyncio
async def test_formula_node_without_eval():
    result = await FormulaNode().execute(
        context(),
        {"records": [{"amount": 100, "rate": 0.12}]},
        {"target": "income", "expression": "amount * rate", "input_path": "records"},
    )
    assert result["records"][0]["income"] == 12


@pytest.mark.asyncio
async def test_mock_llm_node():
    result = await LLMExtractNode().execute(
        context(),
        {"text": "demo"},
        {"provider": "mock", "input_path": "text", "mock_response": {"records": [{"ok": True}]}},
    )
    assert result["records"] == [{"ok": True}]


@pytest.mark.asyncio
async def test_condition_skips_inactive_branch():
    graph = {
        "nodes": [
            {"id": "trigger", "type": "manual_trigger", "config": {}},
            {"id": "condition", "type": "condition", "config": {"field": "value", "operator": "eq", "value": 1}},
            {"id": "yes", "type": "output", "config": {"input_path": "value", "name": "yes"}},
            {"id": "no", "type": "output", "config": {"input_path": "value", "name": "no"}},
        ],
        "edges": [
            {"source": "trigger", "target": "condition"},
            {"source": "condition", "sourceHandle": "true", "target": "yes"},
            {"source": "condition", "sourceHandle": "false", "target": "no"},
        ],
    }
    result = await WorkflowEngine().execute(graph, context(), {"value": 1})
    assert result["result"]["records"] == 1
    assert result["skipped_nodes"] == ["no"]


def test_compatibility_article_extractor_uses_standard_semantics_only():
    record = extract_article_record(
        """<html><article itemscope><h1 itemprop='headline'> Release&nbsp;note </h1>
        <time itemprop='datePublished' datetime='2026-07-28T17:28:35Z'>28 July</time>
        <div itemprop='articleBody'><p>First paragraph.</p><p>Second paragraph.</p>
        <a href='/files/rules.pdf'>Rules</a></div></article></html>""",
        "https://news.example.test/releases/42?utm_source=test",
        {"record_id": "42", "item": {}},
        {},
        None,
    )
    assert record["record_id"] == "42"
    assert record["title"] == "Release note"
    assert record["published_at"] == "2026-07-28T17:28:35Z"
    assert record["url"] == "https://news.example.test/releases/42"
    assert record["body_text"] == "First paragraph.\n\nSecond paragraph.\n\nRules"
    assert record["attachments_json"] == '[{"title": "Rules", "url": "https://news.example.test/files/rules.pdf"}]'


@pytest.mark.asyncio
async def test_browser_tabs_are_opt_in(monkeypatch):
    calls = []

    async def discover(_page):
        calls.append(True)
        return []

    class Page:
        url = "https://example.test/list"

        async def content(self):
            return "<main>ready</main>"

    monkeypatch.setattr("workflow_engine.nodes.discover_tab_descriptors", discover)

    html = await collect_paginated_html(Page(), {}, 1000)

    assert html == "<main>ready</main>"
    assert calls == []


def test_detail_extractor_emits_configured_arbitrary_fields_and_aware_timestamp():
    record = extract_article_record(
        """<html><h2 class='headline'>System status</h2>
        <time class='released' datetime='2026-08-10T15:34:56'>10 August</time>
        <div class='payload'>All services operational</div></html>""",
        "https://status.example.test/incidents/42?region=eu",
        {
            "record_id": "42",
            "fetched_at": "2026-08-10T15:35:02.123456Z",
            "item": {"category": "availability"},
        },
        {
            "detail_fields": [
                {"name": "headline", "selector": ".headline"},
                {"name": "message", "selector": ".payload"},
                {
                    "name": "source_published_at",
                    "selector": "time.released",
                    "attribute": "datetime",
                    "timezone": "Europe/Minsk",
                },
                {
                    "name": "attachments_json",
                    "selector": ".payload a[href]",
                    "multiple": True,
                    "value": "links",
                },
            ],
            "include_listing_fields": True,
        },
        None,
    )

    assert record == {
        "record_id": "42",
        "category": "availability",
        "headline": "System status",
        "message": "All services operational",
        "source_published_at": "2026-08-10T12:34:56Z",
        "attachments_json": "[]",
        "fetched_at": "2026-08-10T15:35:02.123456Z",
        "url": "https://status.example.test/incidents/42?region=eu",
    }


@pytest.mark.asyncio
async def test_crawl_links_filters_and_deduplicates_json_items():
    node = CrawlLinksNode()
    items = node._listing_items({"tabs": [{"contents": [{"url": "press-center/news/n010120261"}]}]}, {"items_path": "tabs.0.contents"})
    assert items == [{"url": "press-center/news/n010120261"}]


@pytest.mark.asyncio
async def test_playwright_detail_does_not_require_successful_http_probe(monkeypatch):
    browser_urls: list[str] = []

    async def browser_execute(_self, _context, _inputs, config):
        browser_urls.append(config["url"])
        return {
            "url": config["url"],
            "html": "<main><h1>Browser-only detail</h1></main>",
        }

    async def forbidden_http_get(*_args, **_kwargs):
        raise AssertionError("detail HTTP must not run in explicit PLAYWRIGHT mode")

    monkeypatch.setattr(BrowserOpenNode, "execute", browser_execute)
    monkeypatch.setattr("httpx.AsyncClient.get", forbidden_http_get)
    result = await CrawlLinksNode().execute(
        context(),
        {
            "url": "https://example.test/list",
            "records": [{"url": "https://example.test/details/one"}],
        },
        {
            "input_path": "records",
            "url_path": "url",
            "detail_fetch_mode": "PLAYWRIGHT",
            "detail_fields": [{"name": "title", "selector": "h1"}],
            "save_artifacts": False,
            "delay_ms": 0,
        },
    )

    assert browser_urls == ["https://example.test/details/one"]
    assert result["errors"] == []
    assert result["records"][0]["title"] == "Browser-only detail"


@pytest.mark.asyncio
async def test_explicit_http_listing_overrides_browser_source_profile(monkeypatch):
    class Response:
        url = "https://example.test/list"
        content = b"<main></main>"
        text = "<main></main>"
        headers = {"content-type": "text/html"}

        def raise_for_status(self):
            return None

    async def http_get(_self, _url, params=None):
        return Response()

    async def forbidden_browser(*_args, **_kwargs):
        raise AssertionError("explicit HTTP listing must not open a browser")

    monkeypatch.setattr("httpx.AsyncClient.get", http_get)
    monkeypatch.setattr(BrowserOpenNode, "execute", forbidden_browser)
    execution = context()
    execution.variables["source"] = {
        "fetch_mode": "PLAYWRIGHT",
        "settings": {"profile": {"requires_javascript": True}},
    }

    listing, url = await CrawlLinksNode()._load_listing(
        execution,
        {},
        {
            "listing_url": "https://example.test/list",
            "listing_fetch_mode": "HTTP",
            "save_artifacts": False,
        },
    )

    assert listing == "<main></main>"
    assert url == "https://example.test/list"


@pytest.mark.asyncio
async def test_mapping_preserves_internal_record_provenance():
    artifact = {"sha256": "a" * 64, "url": "https://example.test/details/one"}

    result = await MappingNode().execute(
        context(),
        {
            "records": [
                {
                    "external_id": "one",
                    "title": "Mapped",
                    "__provenance": {"raw_artifact": artifact},
                }
            ]
        },
        {
            "fields": [
                {"target": "external_id", "source_path": "external_id"},
                {"target": "title", "source_path": "title"},
            ]
        },
    )

    assert result["records"] == [
        {
            "external_id": "one",
            "title": "Mapped",
            "__provenance": {"raw_artifact": artifact},
        }
    ]


def test_llm_record_deduplication_uses_payload_or_configured_keys():
    records = [
        {"incident_id": "one", "state": "open"},
        {"incident_id": "two", "state": "open"},
        {"incident_id": "one", "state": "open"},
    ]

    assert dedupe_extracted_records(records) == records[:2]
    assert dedupe_extracted_records(records, ["state"]) == [records[0]]
