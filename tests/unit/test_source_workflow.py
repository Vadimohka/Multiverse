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


def test_profiler_prefers_extractable_record_container_over_repeated_link():
    html = """
    <main>
      <article class="entry-card x9k2"><a class="detail-target" href="/one">One</a><p class="summary-text">Alpha</p></article>
      <article class="entry-card x9k2"><a class="detail-target" href="/two">Two</a><p class="summary-text">Beta</p></article>
      <article class="entry-card x9k2"><a class="detail-target" href="/three">Three</a><p class="summary-text">Gamma</p></article>
    </main>
    """

    candidates = detect_repeating_candidates(BeautifulSoup(html, "lxml"))
    suggestion = build_extractor_suggestion(candidates)

    assert suggestion["container_selector"] == ".entry-card.x9k2"
    containers = BeautifulSoup(html, "lxml").select(suggestion["container_selector"])
    assert containers
    assert all(
        container.select_one(field["selector"]) is not None
        for container in containers
        for field in suggestion["fields"]
    )
    assert {field["name"] for field in suggestion["fields"]} >= {"url", "title"}
    assert next(field for field in suggestion["fields"] if field["name"] == "url")["attribute"] == "href"
    assert suggestion["follow_links"] is True


def test_source_template_creates_neutral_v2_graph_without_copying_profile_selectors():
    profile = {
        "extractor": {
            "container_selector": ".entry-card.x9k2",
            "fields": [
                {"name": "url", "selector": ".detail-target", "attribute": "href"},
                {"name": "title", "selector": ".detail-target"},
            ],
            "follow_links": True,
        }
    }
    source = SimpleNamespace(id="source-1", fetch_mode="HTTP", entry_url="https://example.test", settings={"profile": profile})

    graph = build_source_template(source, "blank")
    nodes = {node["id"]: node for node in graph["nodes"]}

    extract = nodes["extract"]["config"]
    assert graph["contractVersion"] == 2
    assert [node["type"] for node in graph["nodes"]] == [
        "manual_trigger", "http_request", "crawl_links", "mapping", "transform", "validate", "output",
    ]
    assert extract["strategies"]["allow"] == ["extract-dom"]
    assert extract["dom"]["itemSelector"] == ""
    assert extract["dom"]["fields"] == []
    assert "follow" not in nodes
    assert nodes["output"]["config"]["natural_key_fields"] == ["url"]
    assert nodes["output"]["config"]["on_empty"] != "allow"


def test_parse_table_normalization_is_structural_not_financial():
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
        "валюта": "BYN",
        "процентная_ставка": "12,5%",
        "срок": "3 месяца",
        "row_index": 0,
        "table_id": "table:0",
    }]
