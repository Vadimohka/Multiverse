from copy import deepcopy

from app.routers.workflow_templates import SYSTEM_TEMPLATES, _clean_graph, _template_issues
from workflow_engine.nodes import CrawlLinksNode, extract_article_record


def test_system_templates_have_no_literal_source_bindings():
    for template in SYSTEM_TEMPLATES:
        issues = _template_issues(template["graph_json"])
        if template.get("site_preset"):
            assert "site-preset" in template["tags"]
            assert issues
            assert all(issue.startswith("literal URL at ") for issue in issues)
        else:
            assert issues == []


def test_clean_graph_removes_site_specific_crawl_settings():
    graph = {
        "version": 1,
        "settings": {"source_id": "source-1", "dataset_id": "dataset-1", "natural_key_fields": ["news_id"]},
        "nodes": [
            {
                "id": "crawl",
                "type": "crawl_links",
                "config": {
                    "listing_url": "https://www.bcse.by/press_center/calendar",
                    "items_path": "tabs.0.contents",
                    "url_pattern": "/press-center/news/(n[^/?#]+)",
                    "title_selector": "#title",
                    "source_name": "БВФБ",
                },
            },
            {
                "id": "validate",
                "type": "validate",
                "config": {"schema": {"title": "BCSENews", "properties": {"url": {"pattern": "/press-center/news/"}}}, "required": ["news_id"]},
            },
            {"id": "output", "type": "output", "config": {"dataset_id": "dataset-1", "name": "bcse_news", "natural_key_fields": ["news_id"]}},
        ],
        "edges": [],
    }

    cleaned = _clean_graph(deepcopy(graph))
    crawl = cleaned["nodes"][0]["config"]
    validate = cleaned["nodes"][1]["config"]
    output = cleaned["nodes"][2]["config"]

    assert cleaned["settings"] == {"natural_key_fields": ["url"]}
    assert crawl["listing_url"] == ""
    assert crawl["items_path"] == ""
    assert crawl["url_pattern"] == ""
    assert crawl["detail_fields"] == []
    assert crawl["detail_constants"] == {}
    assert "source_name" not in crawl
    assert validate["schema"] == {}
    assert validate["required"] == []
    assert validate["fail_on_error"] is False
    assert "dataset_id" not in output
    assert output["natural_key_fields"] == ["url"]
    assert output["name"] == "records"
    assert _template_issues(cleaned) == []


def test_network_collection_discovery_is_schema_independent():
    payload = [{"tabs": [{"contents": [{"url": "/one"}, {"url": "/two"}]}]}]
    assert CrawlLinksNode()._largest_url_list(payload) == [{"url": "/one"}, {"url": "/two"}]


def test_network_collections_from_multiple_tabs_are_merged_and_deduplicated():
    payload = {
        "tab_a": [{"url": "/one"}, {"url": "/two"}],
        "tab_b": [{"url": "/two"}, {"href": "/three"}],
    }
    assert CrawlLinksNode()._merge_url_lists(payload) == [
        {"url": "/one"}, {"url": "/two"}, {"url": "/three", "href": "/three"}
    ]


def test_article_contract_has_body_fallback_for_short_detail_pages():
    record = extract_article_record(
        "<html><body><h1>Notice</h1><p>Short notice text</p></body></html>",
        "https://example.test/item/1", {"record_id": "1", "item": {}}, {}, None,
    )
    assert record["title"] == "Notice"
    assert record["body_text"]
    assert record["body_html"]
