import asyncio
from datetime import UTC, datetime

from app.routers.workflow_templates import SYSTEM_TEMPLATES
from app.seed_templates import bcse_home_market_news_graph, bcse_market_news_category_graph, bcse_news_graph
from workflow_engine import WorkflowEngine, validate_dag
from workflow_engine.nodes import CrawlLinksNode, extract_article_record
from workflow_engine.types import ExecutionContext


def test_bcse_news_template_maps_records_before_validation_and_output():
    graph = bcse_news_graph("source-1", "dataset-1")
    nodes = {node["id"]: node for node in graph["nodes"]}

    assert validate_dag(graph) == []
    assert nodes["mapping"]["type"] == "mapping"
    assert {field["target"] for field in nodes["mapping"]["config"]["fields"]} >= {
        "news_id", "title", "source_published_at", "fetched_at", "published_at", "url", "body_text", "language", "source_name",
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
        "record_id": "n050820261",
        "title": "Новость БВФБ",
        "source_published_at": "2026-08-05T14:27:03Z",
        "fetched_at": "2026-08-05T14:27:04Z",
        "url": "https://www.bcse.by/press-center/news/n050820261/2026-08-05T17:27:03",
        "body_text": "Полный текст новости",
        "body_html": "<p>Полный текст новости</p>",
        "attachments_json": "[]",
        "tags": None,
        "category": None,
        "language": "ru",
        "source_name": "БВФБ",
    }
    context = ExecutionContext(run_id="run-1", project_id="project-1", workflow_version_id="version-1")

    result = asyncio.run(WorkflowEngine().execute(subgraph, context, {"records": [record]}))

    assert result["result"]["records"] == [{
        "news_id": "n050820261",
        "title": "Новость БВФБ",
        "source_published_at": "2026-08-05T14:27:03Z",
        "fetched_at": "2026-08-05T14:27:04Z",
        "published_at": "2026-08-05T14:27:03Z",
        "url": "https://www.bcse.by/press-center/news/n050820261/2026-08-05T17:27:03",
        "body_text": "Полный текст новости",
        "body_html": "<p>Полный текст новости</p>",
        "attachments_json": "[]",
        "tags": None,
        "category": None,
        "language": "ru",
        "source_name": "БВФБ",
    }]
    assert result["result"]["business_records"] is True
    assert result["result"]["preflight"]["validation_errors"] == []


def test_system_universal_list_detail_template_uses_public_v2_facades():
    template = next(item for item in SYSTEM_TEMPLATES if item["id"] == "system-universal-html-list-detail")
    graph = template["graph_json"]
    nodes = {node["id"]: node for node in graph["nodes"]}

    assert validate_dag(graph) == []
    assert graph["contractVersion"] == 2
    assert nodes["acquire"]["config"]["url"] == "{{source.url}}"
    assert nodes["traverse"]["config"]["strategies"]["allow"] == ["traverse-links"]
    assert nodes["traverse"]["config"]["pagination"] == {"enabled": False, "mode": "next", "maxPages": 25}
    assert nodes["traverse"]["config"]["detail"] == {
        "enabled": True, "selector": "", "itemsPath": "", "urlPath": "url", "maxItems": 100, "fields": [],
    }
    assert nodes["extract"]["config"]["strategies"]["allow"] == ["extract-mapping"]
    assert nodes["extract"]["config"]["fields"] == []
    assert {(edge["source"], edge["target"]) for edge in graph["edges"]} == {
        ("start", "acquire"), ("acquire", "traverse"), ("traverse", "extract"),
        ("extract", "process"), ("process", "assure"), ("assure", "output"),
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


def test_bcse_preset_keeps_listing_pagination_and_detail_api_in_configuration():
    graph = bcse_news_graph("source-1", "dataset-1", incremental=True)
    crawl = next(node for node in graph["nodes"] if node["id"] == "crawl")["config"]

    assert crawl["listing_url"] == "https://www.bcse.by/press-center/releases"
    assert crawl["listing_fetch_mode"] == "PLAYWRIGHT"
    assert crawl["link_selector"] == "#pc-0c a.text-pc[href*='/press-center/']"
    assert crawl["pagination_enabled"] is True
    assert crawl["pagination_max_pages"] >= 25
    assert crawl["pagination_next_selector"] == "#pc-0 li.paginationjs-next:not(.disabled) a"
    assert crawl["detail_fetch_mode"] == "HTTP"
    assert crawl["detail_request"] == {
        "url": "https://www.bcse.by/solo/calendar",
        "method": "GET",
        "query_params": {
            "sType": "6",
            "sDay": "{{publication_time}}",
            "link": "{{record_id}}",
        },
        "html_path": "solo.html",
        "not_found_path": "solo.notFound",
    }
    effective = CrawlLinksNode()._effective_config(
        ExecutionContext(run_id="run-1", project_id="project-1", workflow_version_id="version-1"),
        {},
        crawl,
    )
    assert effective["detail_request"]["query_params"]["sDay"] == "{{publication_time}}"
    assert effective["detail_request"]["query_params"]["link"] == "{{record_id}}"
    assert crawl["base_url"] == "https://www.bcse.by/"
    publication = next(field for field in crawl["detail_fields"] if field["name"] == "source_published_at")
    assert publication == {
        "name": "source_published_at",
        "source": "response",
        "source_path": "day",
        "timezone": "Europe/Minsk",
    }

    record = extract_article_record(
        """<p>Итоги торговой сессии<a href='/files/result.pdf'>Протокол</a></p>""",
        "https://www.bcse.by/press-center/releases/pr100820261/2026-08-10T17:27:03?utm_source=test",
        {
            "record_id": "pr100820261",
            "detail_response": {
                "day": "2026-08-10T17:27:03",
                "solo": {
                    "title": "Новости торгов",
                    "html": "<p>Итоги торговой сессии<a href='/files/result.pdf'>Протокол</a></p>",
                    "tags": ["итоги торгов", "фондовый рынок"],
                    "categoryName": "Фондовый рынок",
                },
            },
        },
        crawl,
        None,
    )
    assert record["source_published_at"] == "2026-08-10T14:27:03Z"
    assert record["url"] == "https://www.bcse.by/press-center/releases/pr100820261/2026-08-10T17:27:03"
    assert record["title"] == "Новости торгов"
    assert record["body_text"] == "Итоги торговой сессии Протокол"
    assert record["body_html"].startswith("<p>Итоги торговой сессии")
    assert record["tags"] == "итоги торгов|фондовый рынок"
    assert record["category"] == "Фондовый рынок"
    assert record["attachments_json"] == '[{"title": "Протокол", "url": "https://www.bcse.by/files/result.pdf"}]'


def test_bcse_news_category_graph_isolated_from_releases_with_distinct_provenance():
    graph = bcse_market_news_category_graph("source-2", "dataset-1", incremental=True)
    crawl = next(node for node in graph["nodes"] if node["id"] == "crawl")["config"]
    fields = {field["target"]: field for field in next(node for node in graph["nodes"] if node["id"] == "mapping")["config"]["fields"]}

    assert crawl["listing_url"] == "https://www.bcse.by/press-center/releases"
    assert crawl["listing_fetch_mode"] == "PLAYWRIGHT"
    assert crawl["link_selector"] == "#pc-nws-1c a.text-pc[href*='/press-center/news/']"
    assert crawl["pagination_next_selector"] == "#pc-nws-1 li.paginationjs-next:not(.disabled) a"
    assert "/press-center/news/" in crawl["url_pattern"]
    assert "/press-center/releases/" not in crawl["url_pattern"]
    assert fields["source_id"]["constant"] == "bcse-news"
    assert fields["source_section"]["constant"] == "news"
    assert fields["selection_rule_id"]["constant"] == "bcse-news-category-v1"
    assert fields["selection_evidence"]["constant"]["url_prefix"] == "/press-center/news/"


def test_bcse_home_market_news_graph_targets_only_currency_and_repo_widgets():
    graph = bcse_home_market_news_graph("source-3", "dataset-2", incremental=True)
    assert validate_dag(graph) == []
    nodes = {node["id"]: node for node in graph["nodes"]}
    browser = nodes["browser"]["config"]
    extract = nodes["extract"]["config"]
    assert browser["url"] == "https://www.bcse.by/"
    assert extract["container_selector"] == "#repo-body .inf-wrap, [data-browser-supplement='currency-results'] table tbody tr"
    fields = {field["target"]: field for field in nodes["mapping"]["config"]["fields"]}
    assert fields["source_id"]["constant"] == "bcse-currency-repo-news"
    assert fields["selection_rule_id"]["constant"] == "bcse-currency-and-byn-repo-v1"
    assert nodes["output"]["config"]["name"] == "market_news"


def test_bcse_home_repo_widget_extracts_typed_row():
    from workflow_engine.nodes import ExtractRepeatingListNode

    html = """
    <div id='currency'><div class='inf-instrument'><a class='text-asfalt'>USD/BYN_TOD</a><div class='inf-date'>13:00</div><div class='w-60p'><span class='text-asfalt'>3.0131</span></div><div class='w-50p'><span class='text-right'>+0.75%</span><span>+0.0225</span></div></div></div>
    <div id='repo-body'><div class='inf-wrap'><span class='inf-name'>1-3 дней</span><span class='inf-repo-percent'>4%</span><span class='inf-repo-date'>12:16</span></div></div>
    """
    result = asyncio.run(ExtractRepeatingListNode().execute(
        ExecutionContext(run_id="run", project_id="p", workflow_version_id="v"),
        {"html": html},
        {"input_path": "html", "container_selector": "#repo-body .inf-wrap", "fields": [
            {"name": "label", "selector": ".inf-name"},
            {"name": "value_raw", "selector": ".inf-repo-percent"},
            {"name": "observed_source", "selector": ".inf-repo-date"},
            {"name": "change_percent_raw", "selector": ".w-50p > .text-right:first-child"},
            {"name": "change_absolute_raw", "selector": ".w-50p span"},
        ]},
    ))
    assert result["count"] == 1
    assert result["records"][0]["label"] == "1-3 дней"
    assert result["records"][0]["value_raw"] == "4%"


def test_crawler_date_query_parameter_names_are_fully_configurable(monkeypatch):
    captured: dict = {}

    class Response:
        headers = {"content-type": "text/html"}
        content = b"<main></main>"
        text = "<main></main>"
        url = "https://example.test/list"

        def raise_for_status(self):
            return None

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, _url, params=None):
            captured.update(params or {})
            return Response()

    monkeypatch.setattr("workflow_engine.nodes.httpx.AsyncClient", lambda **_: Client())
    context = ExecutionContext(
        run_id="run-1",
        project_id="project-1",
        workflow_version_id="version-1",
        effective_run_clock=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
    )

    asyncio.run(CrawlLinksNode()._load_listing(context, {}, {
        "listing_url": "https://example.test/list",
        "save_artifacts": False,
        "date_range_query": {
            "from_param": "startDate",
            "to_param": "endDate",
            "lookback_days": 2,
            "format": "YYYY/MM/DD",
            "timezone": "UTC",
        },
    }))

    assert captured == {"startDate": "2026/08/08", "endDate": "2026/08/10"}
