import asyncio
import base64
from collections import Counter
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from app.routers.workflows import determine_run_status, node_warning
from workflow_engine import WorkflowEngine, compile_executable_plan, standard_v2_graph, validate_dag
from workflow_engine.catalog import NODE_CATALOG
from workflow_engine.nodes import NODE_REGISTRY, FollowLinksNode, response_payload
from workflow_engine.strategies import (
    DEFAULT_STRATEGIES,
    BrowserTraverseStrategy,
    BrowserXhrAcquireStrategy,
    DelegatedExtractStrategy,
    Strategy,
    StrategyRegistry,
    TraverseFacadeStrategy,
)
from workflow_engine.types import ExecutionContext


def context(clock=None):
    return ExecutionContext(run_id="contract", project_id="project", workflow_version_id="1", effective_run_clock=clock)


def test_readme_node_catalog_matches_public_registry():
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
    category_counts = Counter(item["category"] for item in NODE_CATALOG)
    assert len(NODE_REGISTRY) == len(NODE_CATALOG)
    assert f"**{len(NODE_REGISTRY)} executable node types**" in readme
    for category, count in category_counts.items():
        assert f"| {category} ({count}) |" in readme


def test_http_document_has_diagnostics_not_zero_output():
    async def run():
        request = httpx.Request("GET", "https://example.test/data")
        response = httpx.Response(200, headers={"content-type": "application/json"}, json={"items": [{"id": 1}]}, request=request)
        return await response_payload(context(), response)
    output = asyncio.run(run())
    output["_contract"] = {"output_type": "DOCUMENT", "item_count": 1}
    assert output["document_diagnostics"]["body_size"] > 0
    assert node_warning("http_request", output, {}) is None


def test_json_array_mapping_preserves_484_business_records():
    graph = {"nodes": [
        {"id": "start", "type": "manual_trigger", "config": {}},
        {"id": "json", "type": "json_path", "config": {"input_path": "body", "path": "$.items[*]"}},
        {"id": "map", "type": "mapping", "config": {"input_path": "records", "fields": [{"target": "id", "source_path": "id", "required": True}]}},
        {"id": "save", "type": "output", "config": {"input_path": "records"}},
    ], "edges": [{"source": "start", "target": "json"}, {"source": "json", "target": "map"}, {"source": "map", "target": "save"}]}
    result = asyncio.run(WorkflowEngine().execute(graph, context(), {"body": {"items": [{"id": n} for n in range(484)]}}))
    assert result["result"]["count"] == 484
    assert result["result"]["business_records"] is True


def test_mapping_unwraps_transport_envelope():
    graph = {"nodes": [{"id": "map", "type": "mapping", "config": {"input_path": "tabs.0.rows", "fields": [{"target": "title", "source_path": "name"}]}}], "edges": []}
    result = asyncio.run(WorkflowEngine().execute(graph, context(), {"tabs": [{"rows": [{"name": "One"}]}]}))
    assert result["result"]["records"] == [{"title": "One"}]


def test_wrong_required_extraction_is_empty_unexpected():
    graph = {"nodes": [{"id": "x", "type": "extract_repeating_list", "config": {}}, {"id": "out", "type": "output", "config": {"on_empty": "warning"}}]}
    result = {"result": {"records": []}, "node_outputs": {"x": {"count": 0}}}
    assert determine_run_status(graph, result, {"review_tasks": 0}) == "SUCCESS_EMPTY_UNEXPECTED"


@pytest.mark.asyncio
async def test_follow_links_merges_parent_and_child(monkeypatch):
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def get(self, url):
            return httpx.Response(200, text="<h1>Detail</h1>", request=httpx.Request("GET", url))
    monkeypatch.setattr("workflow_engine.nodes.httpx.AsyncClient", lambda **_: Client())
    result = await FollowLinksNode().execute(context(), {"records": [{"id": "p1", "url": "https://example.test/p1"}]}, {"input_collection": "records", "url_field": "url", "detail_fields": [{"target": "title", "selector": "h1"}], "merge_mode": "MERGE_PARENT_CHILD", "egress_resolver": lambda *_: ["93.184.216.34"]})
    assert result["records"] == [{"id": "p1", "url": "https://example.test/p1", "status_code": 200, "body": "<h1>Detail</h1>", "title": "Detail"}]


def test_historical_formula_clock():
    graph = {"nodes": [{"id": "f", "type": "formula", "config": {"input_path": "records", "target": "y", "expression": 'yesterday("Europe/Minsk")'}}], "edges": []}
    result = asyncio.run(WorkflowEngine().execute(graph, context(datetime.fromisoformat("2026-07-31T08:00:00+03:00")), {"records": [{}]}))
    assert result["result"]["records"][0]["y"] == "2026-07-30"


def test_incompatible_ports_are_rejected():
    graph = {"nodes": [{"id": "fetch", "type": "http_request"}, {"id": "save", "type": "output"}], "edges": [{"source": "fetch", "target": "save"}]}
    assert "Несовместимые порты" in validate_dag(graph)[0]


def test_v2_uses_exactly_the_seven_public_roles_and_legacy_adapter():
    graph = standard_v2_graph()

    assert [node["type"] for node in graph["nodes"]] == [
        "manual_trigger", "http_request", "crawl_links", "mapping", "transform", "validate", "output",
    ]
    assert validate_dag(graph) == []


def test_v2_graph_round_trip_keeps_adaptive_envelope_and_attempt_diagnostics():
    graph = {
        "contractVersion": 2,
        "nodes": [{
            "id": "start",
            "type": "manual_trigger",
            "config": {"mode": "MANUAL", "strategies": {"allow": ["start-input"], "prefer": ["start-input"]}},
        }],
        "edges": [],
    }
    result = asyncio.run(WorkflowEngine().execute(graph, context(), {"records": [{"id": "one"}]}))

    assert result["result"]["_contract"]["version"] == 2
    assert result["result"]["_adaptive_attempts"][0]["strategy_id"] == "start-input"
    assert result["result"]["_adaptive_attempts"][0]["selected"] is True
    assert result["result"]["context"]["kind"] == "RunContext@2"


def test_v2_phase_adapters_emit_typed_envelopes_without_hiding_legacy_records():
    graph = {
        "contractVersion": 2,
        "nodes": [
            {"id": "extract", "type": "mapping", "config": {"input_path": "records", "fields": []}},
            {"id": "process", "type": "transform", "config": {"input_path": "records", "operations": []}},
            {"id": "assure", "type": "validate", "config": {"input_path": "records", "fail_on_error": False}},
            {"id": "out", "type": "output", "config": {"input_path": "records"}},
        ],
        "edges": [
            {"source": "extract", "target": "process"},
            {"source": "process", "target": "assure"},
            {"source": "assure", "target": "out"},
        ],
    }
    result = asyncio.run(WorkflowEngine().execute(graph, context(), {"records": [{"title": "One"}]}))

    outputs = result["node_outputs"]
    assert outputs["extract"]["record_set"]["kind"] == "RecordSet@2"
    assert outputs["process"]["record_set"]["records"] == [{"title": "One"}]
    assert outputs["assure"]["assessment"]["kind"] == "RunAssessment@2"
    assert result["result"]["receipt"]["kind"] == "OutputReceipt@2"


def test_v2_plan_is_deterministic_and_excludes_runtime_secret_values():
    graph = {
        "contractVersion": 2,
        "nodes": [{
            "id": "acquire",
            "type": "http_request",
            "config": {
                "url": "{{source.url}}",
                "headers": {"Authorization": "{{secret.API_TOKEN}}"},
                "strategies": {"allow": ["browser-render"], "prefer": ["browser-render"]},
            },
        }],
        "edges": [],
    }
    first = compile_executable_plan(graph, project_id="p", workflow_id="w", workflow_version=2, source_id="s")
    second = compile_executable_plan(graph, project_id="p", workflow_id="w", workflow_version=2, source_id="s")

    assert first.digest == second.digest
    assert first.required_capabilities == ("browser",)
    assert "API_TOKEN" not in str(first.as_dict())


def test_v2_rejects_false_postcondition_without_silent_success():
    graph = {
        "contractVersion": 2,
        "nodes": [{
            "id": "start",
            "type": "manual_trigger",
            "config": {
                "strategies": {"allow": ["start-input"]},
                "successCriteria": [{"path": "missing", "equals": "first"}],
            },
        }],
        "edges": [],
    }

    with pytest.raises(Exception, match="strategy"):
        asyncio.run(WorkflowEngine().execute(graph, context(), {}))


def test_v2_adaptive_fallback_records_failed_postcondition_and_selected_strategy():
    class Candidate(Strategy):
        def __init__(self, strategy_id: str, payload: str):
            super().__init__(strategy_id, "Start")
            self.payload = payload

        async def execute(self, executor, node, execution_context, inputs, config):
            del executor, node, execution_context, inputs, config
            return {"value": self.payload}

    strategies = StrategyRegistry()
    strategies.register(Candidate("first", "incomplete"))
    strategies.register(Candidate("second", "complete"))
    graph = {
        "contractVersion": 2,
        "nodes": [{
            "id": "start",
            "type": "manual_trigger",
            "config": {
                "strategies": {"allow": ["first", "second"]},
                "successCriteria": [{"path": "value", "equals": "complete"}],
            },
        }],
        "edges": [],
    }

    result = asyncio.run(WorkflowEngine(strategies=strategies).execute(graph, context(), {}))

    attempts = result["result"]["_adaptive_attempts"]
    assert [(item["strategy_id"], item["selected"]) for item in attempts] == [("first", False), ("second", True)]
    assert attempts[0]["fallback_reason"] == "POSTCONDITION_FAILED"


def test_v2_manual_strategy_error_never_silently_falls_back():
    class Fails(Strategy):
        async def execute(self, executor, node, execution_context, inputs, config):
            raise ValueError("transport unavailable")

    class Succeeds(Strategy):
        async def execute(self, executor, node, execution_context, inputs, config):
            return {"value": "complete"}

    strategies = StrategyRegistry()
    strategies.register(Fails("first", "Start"))
    strategies.register(Succeeds("second", "Start"))
    graph = {
        "contractVersion": 2,
        "nodes": [{
            "id": "start",
            "type": "manual_trigger",
            "config": {
                "mode": "MANUAL",
                "selectedStrategy": "first",
                "strategies": {"allow": ["first", "second"]},
            },
        }],
        "edges": [],
    }

    with pytest.raises(Exception, match="strategy"):
        asyncio.run(WorkflowEngine(strategies=strategies).execute(graph, context(), {}))


def test_schema_first_extract_uses_dom_fallback_and_preserves_field_evidence():
    graph = {
        "contractVersion": 2,
        "nodes": [{
            "id": "extract",
            "type": "mapping",
            "config": {
                "collectionPath": "records",
                "fieldCandidates": {
                    "title": [{"id": "jsonld", "kind": "json_ld", "path": "headline", "required": True}, {"id": "dom", "kind": "dom", "selector": "h1", "required": True}],
                },
            },
        }],
        "edges": [],
    }
    result = asyncio.run(WorkflowEngine().execute(graph, context(), {"records": [{"body": "<article><h1>Fallback title</h1></article>"}]}))

    record = result["result"]["records"][0]
    assert record["title"] == "Fallback title"
    assert record["__provenance"]["field_evidence"]["title"]["candidate"] == "dom"


def test_assure_blocks_output_when_required_scope_is_incomplete():
    graph = {
        "contractVersion": 2,
        "nodes": [
            {"id": "assure", "type": "validate", "config": {"input_path": "records", "fail_on_error": False, "expectedScope": {"requireComplete": True}}},
            {"id": "output", "type": "output", "config": {"input_path": "records"}},
        ],
        "edges": [{"source": "assure", "target": "output"}],
    }
    result = asyncio.run(WorkflowEngine().execute(graph, context(), {"records": [{"id": "one"}], "traversal": {"reconciliation": {"discovered": 2, "succeeded": 1, "failed": 1}}}))

    assessment = result["node_outputs"]["assure"]["assessment"]
    assert assessment["status"] == "PARTIAL"
    assert assessment["commit_allowed"] is False
    assert result["result"]["preflight"]["validation_errors"][0]["code"] == "ASSESSMENT_BLOCKED"


def test_v2_registry_exposes_named_api_and_feed_strategies_without_public_nodes():
    assert {
        "acquire-api", "http-api", "acquire-feed", "acquire-browser-xhr",
        "traverse-browser", "extract-dom", "extract-json", "extract-table", "extract-document",
    } <= DEFAULT_STRATEGIES.known_ids()
    assert "acquire-api" not in {item["type"] for item in NODE_CATALOG}


def test_public_document_template_preserves_file_payload_through_traverse_and_outputs_records():
    storage = type("Storage", (), {
        "get_bytes": staticmethod(lambda *_args: asyncio.sleep(0, result=b"Code,Rate\nUSD,3.1\n")),
        "put_bytes": staticmethod(lambda *_args, **_kwargs: asyncio.sleep(0, result={"storage_key": "artifact", "sha256": "hash"})),
    })()
    graph = standard_v2_graph(settings={"source_id": "source"})
    nodes = {node["id"]: node for node in graph["nodes"]}
    nodes["acquire"]["config"] = {
        "contractVersion": 2, "strategies": {"allow": ["acquire-file"]},
        "url": "{{source.url}}",
    }
    nodes["traverse"]["config"] = {"contractVersion": 2, "strategies": {"allow": ["traverse-links"]}}
    nodes["extract"]["config"] = {
        "contractVersion": 2, "strategies": {"allow": ["extract-document"]},
        "document": {"inputPath": "content_base64", "filenamePath": "filename"},
    }
    nodes["process"]["config"] = {"contractVersion": 2, "strategies": {"allow": ["process-operations"]}, "operations": []}
    nodes["assure"]["config"] = {"contractVersion": 2, "strategies": {"allow": ["assure-validation"]}, "fail_on_error": False, "expectedScope": {"allowEmpty": False}}
    nodes["output"]["config"] = {"contractVersion": 2, "strategies": {"allow": ["output-dataset"]}, "on_empty": "warning"}
    execution_context = context()
    execution_context.variables = {"source": {"url": "document://rates.csv", "settings": {
        "document_storage_key": "sources/source/rates.csv", "document_storage_backend": "LOCAL", "document_bucket": "raw", "document_filename": "rates.csv",
    }}}
    execution_context.artifact_storage = storage

    result = asyncio.run(WorkflowEngine().execute(graph, execution_context, {"source": execution_context.variables["source"]}))

    assert result["result"]["records"] == [{"Code": "USD", "Rate": "3.1"}]
    assert result["result"]["business_records"] is True


def test_v2_catalog_exposes_strategy_choices_per_public_phase_without_new_nodes():
    items = {item["type"]: item for item in NODE_CATALOG}
    assert {item["id"] for item in items["crawl_links"]["strategy_options"]} == {
        "traverse-links", "traverse-browser",
    }
    assert {item["id"] for item in items["mapping"]["strategy_options"]} >= {
        "extract-dom", "extract-json", "extract-table", "extract-document",
    }


@pytest.mark.asyncio
async def test_extract_adapters_delegate_to_generic_dom_json_and_table_nodes():
    async def executor(node, execution_context, inputs, config):
        return await node.execute(execution_context, inputs, config)

    html = """<div class='card'><a href='/one'>One</a><b class='rate'>10</b></div>
    <table id='rates'><tr><th>Code</th><th>Rate</th></tr><tr><td>USD</td><td>3.1</td></tr></table>"""
    dom = await DelegatedExtractStrategy("extract-dom", "extract_repeating_list", "dom").execute(
        executor, None, context(), {"body": html},
        {"dom": {"itemSelector": ".card", "fields": [{"name": "rate", "selector": ".rate"}]}},
    )
    json_output = await DelegatedExtractStrategy("extract-json", "json_path", "json").execute(
        executor, None, context(), {"body": {"items": [{"code": "USD"}]}},
        {"json": {"path": "$.items[*]"}},
    )
    table = await DelegatedExtractStrategy("extract-table", "parse_table", "table").execute(
        executor, None, context(), {"body": html}, {"table": {"selector": "#rates"}},
    )

    assert dom["records"] == [{"rate": "10", "evidence": {"rate": {"css_selector": ".card .rate", "text": "10"}}}]
    assert dom["business_records"] is True
    assert json_output["records"] == [{"code": "USD"}]
    assert json_output["business_records"] is True
    assert table["records"] == [{"Code": "USD", "Rate": "3.1"}]
    assert table["business_records"] is True


@pytest.mark.asyncio
async def test_extract_adapters_flatten_all_traversed_listing_pages():
    async def executor(node, execution_context, inputs, config):
        return await node.execute(execution_context, inputs, config)

    result = await DelegatedExtractStrategy("extract-dom", "extract_repeating_list", "dom").execute(
        executor,
        None,
        context(),
        {"pages": [
            {"url": "https://example.test/page=1", "body": "<article class='card'><h2>One</h2></article>", "origin": "acquire"},
            {"url": "https://example.test/page=2", "body": "<article class='card'><h2>Two</h2></article>", "state": "page:2"},
        ]},
        {"dom": {"itemSelector": ".card", "fields": [{"name": "title", "selector": "h2"}]}},
    )

    assert result["type"] == "PAGE_COLLECTION"
    assert result["pages"] == [
        {"index": 1, "url": "https://example.test/page=1", "count": 1},
        {"index": 2, "url": "https://example.test/page=2", "count": 1},
    ]
    assert result["records"] == [
        {"title": "One", "url": "https://example.test/page=1", "evidence": {"title": {"css_selector": ".card h2", "text": "One"}}, "__provenance": {"page": {"url": "https://example.test/page=1", "index": 1, "state": "acquire"}}},
        {"title": "Two", "url": "https://example.test/page=2", "evidence": {"title": {"css_selector": ".card h2", "text": "Two"}}, "__provenance": {"page": {"url": "https://example.test/page=2", "index": 2, "state": "page:2"}}},
    ]


@pytest.mark.asyncio
async def test_document_extract_adapter_delegates_to_generic_document_parser():
    async def executor(node, execution_context, inputs, config):
        return await node.execute(execution_context, inputs, config)

    payload = base64.b64encode(b"Code,Rate\nUSD,3.1\n").decode()
    result = await DelegatedExtractStrategy("extract-document", "parse_document", "document").execute(
        executor,
        None,
        context(),
        {"content_base64": payload, "filename": "rates.csv"},
        {"document": {}},
    )

    assert result["type"] == "CSV"
    assert result["records"] == [{"Code": "USD", "Rate": "3.1"}]
    assert result["business_records"] is True


@pytest.mark.asyncio
async def test_document_extract_adapter_flattens_public_document_catalogue_records():
    async def executor(node, execution_context, inputs, config):
        return await node.execute(execution_context, inputs, config)

    payload = base64.b64encode(b"Code,Rate\nUSD,3.1\n").decode()
    result = await DelegatedExtractStrategy("extract-document", "parse_document", "document").execute(
        executor,
        None,
        context(),
        {"records": [{
            "url": "https://example.test/rates.csv",
            "content_base64": payload,
            "filename": "rates.csv",
            "__provenance": {"state": "detail:1"},
        }]},
        {"document": {}},
    )

    assert result["type"] == "DOCUMENT_COLLECTION"
    assert result["records"] == [{
        "Code": "USD", "Rate": "3.1", "url": "https://example.test/rates.csv",
        "__provenance": {"state": "detail:1", "document": {"filename": "rates.csv", "index": 0}},
    }]
    assert result["documents"] == [{
        "index": 0, "url": "https://example.test/rates.csv", "filename": "rates.csv", "type": "CSV", "count": 1,
    }]


@pytest.mark.asyncio
async def test_browser_xhr_acquire_selects_declaratively_captured_public_json():
    async def executor(_node, _execution_context, _inputs, _config):
        return {
            "url": "https://example.test/list",
            "body": "<main>shell</main>",
            "artifacts": [],
            "network": [
                {"url": "https://example.test/telemetry", "body": {"ignored": True}},
                {"url": "https://example.test/api/items", "content_type": "application/json", "body": {"data": [{"id": "one"}]}},
            ],
        }

    result = await BrowserXhrAcquireStrategy("acquire-browser-xhr", "Acquire").execute(
        executor,
        None,
        context(),
        {},
        {"xhr": {"urlContains": "/api/", "path": "$.data"}},
    )

    assert result["url"] == "https://example.test/api/items"
    assert result["body"] == [{"id": "one"}]


@pytest.mark.asyncio
async def test_browser_traverse_uses_declarative_states_load_more_and_detail_without_site_code():
    calls: list[dict[str, object]] = []

    async def executor(node, _execution_context, _inputs, config):
        calls.append(config)
        url = str(config["url"])
        if url.endswith("/one"):
            return {"url": url, "body": "<h1>One detail</h1>", "artifacts": [{"kind": "html"}]}
        if url.endswith("/two"):
            return {"url": url, "body": "<h1>Two detail</h1>", "artifacts": [{"kind": "html"}]}
        state = next((action.get("selector") for action in config.get("actions", []) if action.get("type") == "click" and action.get("selector", "").startswith("[data-tab")), "")
        if state == "[data-tab='b']":
            body = "<article class='card'><a class='detail' href='/two'>Two</a><span class='rate'>2</span></article>"
        else:
            body = "<article class='card'><a class='detail' href='/one'>One</a><span class='rate'>1</span></article>"
        return {"url": url, "body": body, "artifacts": [{"kind": "html"}]}

    result = await BrowserTraverseStrategy("traverse-browser", "Traverse").execute(
        executor,
        None,
        context(),
        {"url": "https://example.test/list"},
        {
            "browserTraversal": {
                "states": [
                    {"name": "a", "actions": [{"type": "click", "selector": "[data-tab='a']"}]},
                    {"name": "b", "actions": [{"type": "click", "selector": "[data-tab='b']"}]},
                ],
                "loadMore": {"selector": ".load-more", "times": 1, "waitMs": 0},
                "scroll": {"times": 1, "waitMs": 0},
                "listing": {
                    "itemSelector": ".card", "linkSelector": "a.detail",
                    "fields": [{"name": "rate", "selector": ".rate"}],
                },
                "detail": {"enabled": True, "includeListingFields": True, "fields": [{"name": "title", "selector": "h1"}]},
            },
            "budgets": {"maxItems": 10, "maxRequests": 10},
        },
    )

    assert [record["title"] for record in result["records"]] == ["One detail", "Two detail"]
    assert [record["rate"] for record in result["records"]] == ["1", "2"]
    assert result["traversal"]["reconciliation"] == {
        "discovered": 2, "succeeded": 2, "intentionally_skipped": 0, "failed": 0, "duplicate": 0,
    }
    assert any(action.get("selector") == ".load-more" for action in calls[0]["actions"])
    assert not any(action.get("type") == "javascript" for call in calls for action in call["actions"])


@pytest.mark.asyncio
async def test_browser_traverse_allows_a_link_card_as_its_own_detail_link():
    async def executor(_node, _execution_context, _inputs, config):
        url = str(config["url"])
        if url.endswith("/one"):
            return {"url": url, "body": "<h1>One detail</h1>", "artifacts": [{"kind": "html"}]}
        return {"url": url, "body": "<a class='article-card' href='/one'>One</a>", "artifacts": [{"kind": "html"}]}

    result = await BrowserTraverseStrategy("traverse-browser", "Traverse").execute(
        executor,
        None,
        context(),
        {"url": "https://example.test/list"},
        {
            "browserTraversal": {
                "listing": {"itemSelector": "a.article-card", "linkSelector": ":scope"},
                "detail": {"enabled": True, "fields": [{"name": "title", "selector": "h1"}]},
            },
        },
    )

    assert result["records"] == [{
        "url": "https://example.test/one",
        "state": "default",
        "body": "<h1>One detail</h1>",
        "content_type": "text/html",
        "__provenance": {"state": "default", "url": "https://example.test/one", "artifacts": [{"kind": "html"}]},
        "title": "One detail",
    }]


@pytest.mark.asyncio
async def test_browser_traverse_disables_listing_pagination_for_detail_pages():
    calls: list[dict[str, object]] = []

    async def executor(_node, _execution_context, _inputs, config):
        calls.append(config)
        url = str(config["url"])
        if url.endswith("/one"):
            return {"url": url, "body": "<h1>One detail</h1>", "artifacts": []}
        return {"url": url, "body": "<a class='card' href='/one'>One</a>", "artifacts": []}

    await BrowserTraverseStrategy("traverse-browser", "Traverse").execute(
        executor,
        None,
        context(),
        {"url": "https://example.test/list"},
        {
            "browserTraversal": {
                "listing": {"itemSelector": "a.card", "linkSelector": ":scope"},
                "pagination": {"enabled": True, "maxPages": 2},
                "detail": {"enabled": True, "maxItems": 1},
            },
        },
    )

    assert calls[0]["pagination_enabled"] is True
    assert calls[1]["pagination_enabled"] is False


@pytest.mark.asyncio
async def test_browser_traverse_reuses_upstream_default_listing_body():
    calls: list[dict[str, object]] = []

    async def executor(_node, _execution_context, _inputs, config):
        calls.append(config)
        return {"url": str(config["url"]), "body": "<a class='card' href='/unused'>Unused</a>", "artifacts": []}

    result = await BrowserTraverseStrategy("traverse-browser", "Traverse").execute(
        executor,
        None,
        context(),
        {"url": "https://example.test/list", "body": "<a class='card' href='/one'>One</a>"},
        {"browserTraversal": {"listing": {"itemSelector": "a.card", "linkSelector": ":scope"}}},
    )

    assert calls == []
    assert result["records"] == [{"url": "https://example.test/one", "state": "default"}]


def test_traverse_facade_passes_through_and_emits_resumable_checkpoint():
    graph = {
        "contractVersion": 2,
        "nodes": [{
            "id": "traverse",
            "type": "crawl_links",
            "config": {"strategies": {"allow": ["traverse-links"]}},
        }],
        "edges": [],
    }
    result = asyncio.run(WorkflowEngine().execute(
        graph,
        context(),
        {"url": "https://example.test/list", "body": "<main>Listing</main>", "records": [{"id": "one"}]},
    ))

    traversal = result["result"]["traversal"]
    assert result["result"]["records"] == [{"id": "one"}]
    assert traversal["stop_reason"] == "PASS_THROUGH"
    assert traversal["checkpoint"]["completed_urls"] == ["https://example.test/list"]


@pytest.mark.asyncio
async def test_traverse_facade_honors_configured_pagination_max_below_global_budget(monkeypatch):
    calls: list[str] = []

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass

    async def fake_request(client, method, url, *_args, **_kwargs):
        del client, method, _args, _kwargs
        calls.append(url)
        return httpx.Response(200, text="<main>Next</main>", headers={"content-type": "text/html"}, request=httpx.Request("GET", url))

    monkeypatch.setattr("workflow_engine.egress.request_with_egress_policy", fake_request)
    output = await TraverseFacadeStrategy("traverse-links", "Traverse").execute(
        None,
        None,
        context(),
        {"url": "https://example.test/list?page=1", "body": "<main>First</main>", "content_type": "text/html"},
        {
            "pagination": {"enabled": True, "mode": "page", "urlTemplate": "https://example.test/list?page={{page}}", "maxPages": 2},
            "budgets": {"maxPages": 25},
            "egress_resolver": lambda *_: ["93.184.216.34"],
        },
    )

    assert calls == ["https://example.test/list?page=2"]
    assert len(output["pages"]) == 2
    assert output["traversal"]["stop_reason"] == "MAX_PAGES"


@pytest.mark.asyncio
async def test_traverse_facade_resumes_without_refetching_completed_detail(monkeypatch):
    calls: list[str] = []

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass

    async def fake_request(client, method, url, *_args, **_kwargs):
        del client, method, _args, _kwargs
        calls.append(url)
        return httpx.Response(200, text="<h1>Detail</h1>", headers={"content-type": "text/html"}, request=httpx.Request("GET", url))

    monkeypatch.setattr("workflow_engine.egress.request_with_egress_policy", fake_request)
    strategy = TraverseFacadeStrategy("traverse-links", "Traverse")
    output = await strategy.execute(
        None,
        None,
        context(),
        {
            "url": "https://example.test/list",
            "body": "<a class='detail' href='/one'>One</a><a class='detail' href='/two'>Two</a>",
            "content_type": "text/html",
            "checkpoint": {"completed_detail_urls": ["https://example.test/one"]},
        },
        {
            "detail": {"enabled": True, "selector": "a.detail", "maxItems": 10},
            "egress_resolver": lambda *_: ["93.184.216.34"],
        },
    )

    assert calls == ["https://example.test/two"]
    assert output["traversal"]["checkpoint"]["completed_detail_urls"] == [
        "https://example.test/one", "https://example.test/two",
    ]


@pytest.mark.asyncio
async def test_traverse_facade_extracts_configured_http_detail_fields(monkeypatch):
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass

    async def fake_request(client, method, url, *_args, **_kwargs):
        del client, method, _args, _kwargs
        return httpx.Response(
            200,
            text="<article><h1>Full article</h1><time datetime='2026-08-13T12:00:00Z'></time><div class='body'>All public detail text.</div></article>",
            headers={"content-type": "text/html"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr("workflow_engine.egress.request_with_egress_policy", fake_request)
    output = await TraverseFacadeStrategy("traverse-links", "Traverse").execute(
        None,
        None,
        context(),
        {"url": "https://example.test/list", "body": "<a class='detail' href='/one'>Listing title</a>", "content_type": "text/html"},
        {
            "detail": {
                "enabled": True,
                "selector": "a.detail",
                "maxItems": 1,
                "fields": [
                    {"name": "title", "selector": "h1"},
                    {"name": "body_text", "selector": ".body"},
                    {"name": "source_published_at", "selector": "time", "attribute": "datetime"},
                    {"name": "listing_title", "source": "listing", "source_path": "title"},
                ],
            },
            "egress_resolver": lambda *_: ["93.184.216.34"],
        },
    )

    record = output["records"][0]
    assert record["title"] == "Full article"
    assert record["body_text"] == "All public detail text."
    assert record["source_published_at"] == "2026-08-13T12:00:00Z"
    assert record["listing_title"] == "Listing title"
    assert record["url"] == "https://example.test/one"
    assert record["content_type"] == "text/html"
    assert record["__provenance"]["state"] == "detail:1"


@pytest.mark.asyncio
async def test_traverse_facade_accepts_saved_legacy_detail_fields(monkeypatch):
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass

    async def fake_request(client, method, url, *_args, **_kwargs):
        del client, method, _args, _kwargs
        return httpx.Response(200, text="<h1>Saved mapping</h1>", headers={"content-type": "text/html"}, request=httpx.Request("GET", url))

    monkeypatch.setattr("workflow_engine.egress.request_with_egress_policy", fake_request)
    output = await TraverseFacadeStrategy("traverse-links", "Traverse").execute(
        None,
        None,
        context(),
        {"url": "https://example.test/list", "body": "<a class='detail' href='/one'>One</a>", "content_type": "text/html"},
        {
            "detail": {"enabled": True, "selector": "a.detail", "maxItems": 1},
            "detail_fields": [{"name": "title", "selector": "h1"}],
            "egress_resolver": lambda *_: ["93.184.216.34"],
        },
    )

    assert output["records"][0]["title"] == "Saved mapping"


@pytest.mark.asyncio
async def test_traverse_facade_respects_detail_max_items_below_global_budget(monkeypatch):
    calls: list[str] = []

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass

    async def fake_request(client, method, url, *_args, **_kwargs):
        del client, method, _args, _kwargs
        calls.append(url)
        return httpx.Response(200, text="<h1>Detail</h1>", headers={"content-type": "text/html"}, request=httpx.Request("GET", url))

    monkeypatch.setattr("workflow_engine.egress.request_with_egress_policy", fake_request)
    output = await TraverseFacadeStrategy("traverse-links", "Traverse").execute(
        None,
        None,
        context(),
        {"url": "https://example.test/list", "body": "<a class='detail' href='/one'>One</a><a class='detail' href='/two'>Two</a>", "content_type": "text/html"},
        {
            "budgets": {"maxItems": 500},
            "detail": {"enabled": True, "selector": "a.detail", "maxItems": 1},
            "egress_resolver": lambda *_: ["93.184.216.34"],
        },
    )

    assert calls == ["https://example.test/one"]
    assert output["traversal"]["reconciliation"]["succeeded"] == 1


def test_v2_acquire_envelope_keeps_payload_available_to_traverse():
    from workflow_engine.contracts import adapt_v2_output

    output = adapt_v2_output(
        "http_request",
        {"url": "https://example.test/list", "body": "<main>listing</main>", "content_type": "text/html", "status_code": 200},
        run_context={},
    )

    assert output["source_bundle"]["body"] == "<main>listing</main>"
    assert output["body"] == "<main>listing</main>"
    assert output["content_type"] == "text/html"
