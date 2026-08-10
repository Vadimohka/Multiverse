import asyncio

from app.seed_templates import bcse_news_graph
from app.routers.workflow_templates import SYSTEM_TEMPLATES
from workflow_engine import WorkflowEngine, validate_dag
from workflow_engine.nodes import CrawlLinksNode
from workflow_engine.types import ExecutionContext


def test_bcse_news_template_maps_records_before_validation_and_output():
    graph = bcse_news_graph("source-1", "dataset-1")
    nodes = {node["id"]: node for node in graph["nodes"]}

    assert validate_dag(graph) == []
    assert nodes["mapping"]["type"] == "mapping"
    assert {field["target"] for field in nodes["mapping"]["config"]["fields"]} >= {
        "news_id", "title", "published_at", "url", "body_text", "language", "source_name", "observed_at",
    }
    assert {(edge["source"], edge["target"]) for edge in graph["edges"]} == {
        ("trigger", "crawl"), ("crawl", "mapping"), ("mapping", "validate"), ("validate", "output"),
    }


def test_bcse_news_template_output_is_persistable_business_records():
    graph = bcse_news_graph("source-1", "dataset-1")
    keep = {"mapping", "validate", "output"}
    subgraph = {
        "version": 1,
        "settings": {},
        "nodes": [node for node in graph["nodes"] if node["id"] in keep],
        "edges": [edge for edge in graph["edges"] if edge["source"] in keep and edge["target"] in keep],
    }
    record = {
        "news_id": "n050820261",
        "title": "Новость БВФБ",
        "published_at": "2026-08-05T17:27:03",
        "url": "https://www.bcse.by/press-center/news/n050820261/2026-08-05T17:27:03",
        "body_text": "Полный текст новости",
        "body_html": "<p>Полный текст новости</p>",
        "tags": "ценные бумаги",
        "attachments_json": "[]",
        "language": "ru",
        "source_name": "БВФБ",
        "observed_at": "2026-08-10T11:17:17+00:00",
    }
    context = ExecutionContext(run_id="run-1", project_id="project-1", workflow_version_id="version-1")

    result = asyncio.run(WorkflowEngine().execute(subgraph, context, {"records": [record]}))

    assert result["result"]["records"] == [record]
    assert result["result"]["business_records"] is True
    assert result["result"]["preflight"]["validation_errors"] == []


def test_system_list_detail_template_is_source_independent_and_persistable():
    template = next(item for item in SYSTEM_TEMPLATES if item["id"] == "system-list-detail-crawl")
    graph = template["graph_json"]
    nodes = {node["id"]: node for node in graph["nodes"]}

    assert validate_dag(graph) == []
    assert nodes["crawl"]["config"]["listing_url"] == ""
    assert nodes["crawl"]["config"]["items_path"] == ""
    assert nodes["crawl"]["config"]["url_pattern"] == ""
    assert nodes["crawl"]["config"]["same_origin_only"] is True
    assert nodes["crawl"]["config"]["pagination_enabled"] is True
    assert nodes["crawl"]["config"]["pagination_max_pages"] >= 1
    assert {field["target"] for field in nodes["mapping"]["config"]["fields"]} >= {
        "record_id", "title", "published_at", "url", "body_text", "source_name",
    }
    assert {(edge["source"], edge["target"]) for edge in graph["edges"]} == {
        ("trigger", "crawl"), ("crawl", "mapping"), ("mapping", "output"),
    }


def test_generic_html_link_discovery_skips_navigation():
    html = """
    <main>
      <nav><a href="/">Home</a><a href="/section">Section</a></nav>
      <a href="#all">All</a>
      <article><a href="/articles/one">First card</a></article>
      <article><a href="/articles/two">Second card</a></article>
    </main>
    """

    items = CrawlLinksNode()._listing_items(html, {"link_selector": ""})

    assert items == [
        {"url": "/articles/one", "title": "First card"},
        {"url": "/articles/two", "title": "Second card"},
    ]
