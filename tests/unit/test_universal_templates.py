from copy import deepcopy

from app.routers.workflow_templates import SYSTEM_TEMPLATES, _clean_graph, _template_issues
from workflow_engine import PUBLIC_PHASES, compile_executable_plan, validate_dag
from workflow_engine.nodes import CrawlLinksNode, extract_article_record


def test_system_templates_have_no_literal_source_bindings():
    for template in SYSTEM_TEMPLATES:
        graph = template["graph_json"]
        assert _template_issues(graph) == []
        assert graph["contractVersion"] == 2
        assert [node["type"] for node in graph["nodes"]] == list(PUBLIC_PHASES)
        assert validate_dag(graph) == []


def test_system_templates_compile_to_portable_v2_execution_plans():
    for template in SYSTEM_TEMPLATES:
        plan = compile_executable_plan(
            template["graph_json"],
            project_id="project-1",
            workflow_id=template["id"],
            workflow_version=1,
            source_id="source-1",
        )

        assert plan.contract_version == 2
        assert [node.phase for node in plan.nodes] == list(PUBLIC_PHASES.values())
        assert "https://" not in str(plan.as_dict())


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


def test_clean_graph_removes_v2_selectors_endpoints_and_mapping_but_keeps_public_capabilities():
    graph = {
        "contractVersion": 2,
        "settings": {"source_id": "source-1", "dataset_id": "dataset-1", "presetRefs": {"sourcePreset": "one"}},
        "nodes": [
            {"id": "acquire", "type": "http_request", "config": {
                "contractVersion": 2,
                "strategies": {"allow": ["acquire-api", "acquire-browser-xhr"]},
                "endpoint": "https://api.example.test/v1/items",
                "xhr": {"urlContains": "/v1/items", "path": "$.data"},
                "headers": {"X-Source": "one"},
            }},
            {"id": "traverse", "type": "crawl_links", "config": {
                "contractVersion": 2,
                "strategies": {"allow": ["traverse-browser"]},
                "browserTraversal": {"listing": {"itemSelector": ".offer", "fields": [{"name": "title"}]}},
            }},
            {"id": "extract", "type": "mapping", "config": {
                "contractVersion": 2,
                "strategies": {"allow": ["extract-dom"]},
                "dom": {"itemSelector": ".offer", "fields": [{"name": "title", "selector": "h2"}]},
                "fieldCandidates": {"title": [{"kind": "dom", "selector": "h2"}]},
            }},
        ],
        "edges": [],
    }

    cleaned = _clean_graph(graph)
    configs = {node["id"]: node["config"] for node in cleaned["nodes"]}

    assert cleaned["settings"] == {}
    assert configs["acquire"]["strategies"]["allow"] == ["acquire-api", "acquire-browser-xhr"]
    assert configs["acquire"]["endpoint"] == ""
    assert configs["acquire"]["xhr"] == {"urlContains": "", "path": ""}
    assert configs["acquire"]["headers"] == {}
    assert configs["traverse"]["browserTraversal"]["listing"]["itemSelector"] == ""
    assert configs["extract"]["dom"] == {"inputPath": "body", "itemSelector": "", "fields": []}
    assert "fieldCandidates" not in configs["extract"]
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


def _template(template_id: str) -> dict:
    return next(item for item in SYSTEM_TEMPLATES if item["id"] == template_id)


def _node(graph: dict, node_id: str) -> dict:
    return next(node for node in graph["nodes"] if node["id"] == node_id)["config"]


def test_rate_matrix_template_pins_row_identity_and_context():
    template = _template("system-universal-rate-matrix")
    graph = template["graph_json"]
    output = _node(graph, "output")
    assert output["natural_key_fields"] == ["page_url", "table_id", "row_index"]
    process = _node(graph, "process")
    assert process["operations"] == [
        {"type": "add_context", "fields": ["source_id", "source_name", "fetched_at", "page_url"]},
    ]
    assert _node(graph, "assure")["expectedScope"]["minRecords"] == 1
    assert _node(graph, "extract")["table"]["normalize_fields"] is True


def test_product_cards_template_enables_auto_clustering():
    template = _template("system-universal-product-cards")
    extract = _node(template["graph_json"], "extract")
    assert extract["strategies"]["allow"] == ["extract-dom"]
    assert extract["dom"]["itemSelector"] == ""
    assert extract["dom"]["fields"] == []
    assert _node(template["graph_json"], "output")["natural_key_fields"] == ["url"]


def test_news_window_template_carries_detail_fields_and_date_boundary():
    template = _template("system-universal-news-window")
    traverse = _node(template["graph_json"], "traverse")
    assert traverse["detail"]["enabled"] is True
    assert [field["name"] for field in traverse["detail"]["fields"]] == [
        "title", "body_text", "published_at", "url",
    ]
    assert traverse["dateBoundary"]["field"] == "source_published_at"
    # An empty result inside a legitimate date window must not fail the run.
    assert _node(template["graph_json"], "assure")["expectedScope"]["allowEmpty"] is True


def test_browser_cards_detail_template_wires_shell_criterion():
    template = _template("system-universal-browser-cards-detail")
    graph = template["graph_json"]
    acquire = _node(graph, "acquire")
    assert acquire["strategies"]["allow"] == ["acquire-browser"]
    assert acquire["successCriteria"] == [
        {"path": "body_text_len", "operator": "gte", "value": 1000, "name": "rendered_text_present"},
    ]
    traversal = _node(graph, "traverse")["browserTraversal"]
    assert traversal["detail"]["enabled"] is True
    assert _node(graph, "output")["natural_key_fields"] == ["url", "state"]
    operations = _node(graph, "process")["operations"]
    assert {"type": "add_context", "fields": ["source_id", "source_name", "fetched_at", "page_url", "state"]} in operations


def test_new_templates_survive_instantiation_cleaning():
    from app.routers.workflow_templates import _clean_graph

    for template_id in (
        "system-universal-rate-matrix",
        "system-universal-product-cards",
        "system-universal-news-window",
        "system-universal-browser-cards-detail",
    ):
        cleaned = _clean_graph(_template(template_id)["graph_json"], reset_v2_source_config=False)
        assert _template_issues(cleaned) == []
        assert validate_dag(cleaned) == []


def test_shell_aware_templates_wire_rendered_text_criterion():
    for template_id, allow in (
        ("system-universal-cards-shell-aware", ["acquire-http", "acquire-browser"]),
        ("system-universal-tables-shell-aware", ["acquire-http", "acquire-browser"]),
        ("system-universal-news-shell-aware", ["acquire-http", "acquire-browser"]),
    ):
        graph = _template(template_id)["graph_json"]
        acquire = _node(graph, "acquire")
        assert acquire["strategies"]["allow"] == allow
        assert acquire["strategies"]["prefer"] == ["acquire-http"]
        assert acquire["successCriteria"] == [
            {"path": "body_text_len", "operator": "gte", "value": 1000, "name": "rendered_text_present"}
        ]
        assert _template_issues(graph) == []


def test_shell_aware_variants_keep_base_template_output_contracts():
    cards = _node(_template("system-universal-cards-shell-aware")["graph_json"], "output")
    tables = _node(_template("system-universal-tables-shell-aware")["graph_json"], "output")
    news = _node(_template("system-universal-news-shell-aware")["graph_json"], "output")
    assert cards["natural_key_fields"] == ["url"]
    assert tables["natural_key_fields"] == ["page_url", "table_id", "row_index"]
    assert news["natural_key_fields"] == ["url"]
    assert _node(_template("system-universal-news-shell-aware")["graph_json"], "traverse")["detail"]["enabled"] is True
