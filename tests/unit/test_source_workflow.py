import asyncio
from types import SimpleNamespace

from app.routers.workflows import build_source_template
from app.services.source_profiler import build_extractor_suggestion, detect_repeating_candidates
from bs4 import BeautifulSoup
from workflow_engine.nodes import ParseTableNode
from workflow_engine.types import ExecutionContext


def test_profiler_detects_belinvest_service_cards_and_detail_links():
    html = """
    <main>
      <div class="services-item js-service-item">
        <a class="item-description-link" href="/business/deposit/one">Надёжный</a>
        <span id="deposit_name_1">Надёжный</span>
      </div>
      <div class="services-item js-service-item">
        <a class="item-description-link" href="/business/deposit/two">Доходный</a>
        <span id="deposit_name_2">Доходный</span>
      </div>
    </main>
    """

    candidates = detect_repeating_candidates(BeautifulSoup(html, "lxml"))
    suggestion = build_extractor_suggestion(candidates)

    assert suggestion["container_selector"] == ".services-item.js-service-item"
    assert {field["name"] for field in suggestion["fields"]} >= {"url", "title"}
    assert next(field for field in suggestion["fields"] if field["name"] == "url")["attribute"] == "href"
    assert suggestion["follow_links"] is True


def test_source_template_uses_profile_detail_table_and_natural_key():
    profile = {
        "extractor": {
            "container_selector": ".services-item.js-service-item",
            "fields": [
                {"name": "url", "selector": ".item-description-link", "attribute": "href"},
                {"name": "title", "selector": ".item-description-link"},
            ],
            "follow_links": True,
        }
    }
    source = SimpleNamespace(id="source-1", fetch_mode="HTTP", entry_url="https://example.test", settings={"profile": profile})

    graph = build_source_template(source, "blank")
    nodes = {node["id"]: node for node in graph["nodes"]}

    extract = nodes["extract"]["config"]
    assert extract["container_selector"] == ".services-item.js-service-item"
    assert {field["name"] for field in extract["fields"]} == {"url", "product_name"}
    assert nodes["follow"]["config"]["detail_table"] == {
        "selector": "table",
        "header_row": 0,
        "normalize_fields": True,
    }
    assert nodes["output"]["config"]["natural_key_fields"] == ["url"]
    assert nodes["output"]["config"]["on_empty"] != "allow"


def test_parse_table_can_expose_stable_detail_field_aliases():
    html = """
    <table><tr><th>Валюта</th><th>Процентная ставка</th><th>Срок</th></tr>
    <tr><td>BYN</td><td>12,5%</td><td>3 месяца</td></tr></table>
    """

    result = asyncio.run(ParseTableNode().execute(
        ExecutionContext(run_id="1", project_id="1", workflow_version_id="1"),
        {"html": html},
        {"selector": "table", "normalize_fields": True},
    ))

    assert result["records"] == [{
        "Валюта": "BYN",
        "Процентная ставка": "12,5%",
        "Срок": "3 месяца",
        "currency": "BYN",
        "rate": "12,5%",
        "term": "3 месяца",
    }]
