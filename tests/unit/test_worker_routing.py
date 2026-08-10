from app.seed_templates import bcse_news_graph
from worker import queue_for_graph


def test_configured_browser_detail_crawl_uses_browser_worker():
    graph = bcse_news_graph("source-1", "dataset-1", incremental=True)

    assert queue_for_graph(graph) == "browser"
