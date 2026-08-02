import base64
import io

import pytest
from openpyxl import Workbook
from workflow_engine import WorkflowEngine
from workflow_engine.nodes import (
    CrawlLinksNode,
    FormulaNode,
    LLMExtractNode,
    ParseDocumentNode,
    ParseTableNode,
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


def test_crawl_links_article_extractor_uses_stable_bcse_fields():
    record = extract_article_record(
        """<html><span id='title'> Новость&nbsp;БВФБ </span>
        <div class='dynamic-publicationdate'>28 июля 2026</div>
        <div id='pc_body'><p>Первый абзац.</p><p>Второй абзац.</p><a href='/files/rules.pdf'>Правила</a></div></html>""",
        "https://www.bcse.by/press-center/news/n280720261/2026-07-28T17:28:35?utm=x",
        {"news_id": "n280720261", "item": {}},
        {},
        None,
    )
    assert record["news_id"] == "n280720261"
    assert record["title"] == "Новость БВФБ"
    assert record["published_at"] == "2026-07-28"
    assert record["url"] == "https://www.bcse.by/press-center/news/n280720261/2026-07-28T17:28:35"
    assert record["body_text"] == "Первый абзац.\n\nВторой абзац.\n\nПравила"
    assert record["attachments_json"] == '[{"title": "Правила", "url": "https://www.bcse.by/files/rules.pdf"}]'


@pytest.mark.asyncio
async def test_crawl_links_filters_and_deduplicates_json_items():
    node = CrawlLinksNode()
    items = node._listing_items({"tabs": [{"contents": [{"url": "press-center/news/n010120261"}]}]}, {"items_path": "tabs.0.contents"})
    assert items == [{"url": "press-center/news/n010120261"}]
