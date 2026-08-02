import asyncio
from collections import Counter
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from app.routers.workflows import determine_run_status, node_warning
from workflow_engine import WorkflowEngine, validate_dag
from workflow_engine.catalog import NODE_CATALOG
from workflow_engine.nodes import NODE_REGISTRY, FollowLinksNode, response_payload
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
    result = await FollowLinksNode().execute(context(), {"records": [{"id": "p1", "url": "https://example.test/p1"}]}, {"input_collection": "records", "url_field": "url", "detail_fields": [{"target": "title", "selector": "h1"}], "merge_mode": "MERGE_PARENT_CHILD"})
    assert result["records"] == [{"id": "p1", "url": "https://example.test/p1", "status_code": 200, "body": "<h1>Detail</h1>", "title": "Detail"}]


def test_historical_formula_clock():
    graph = {"nodes": [{"id": "f", "type": "formula", "config": {"input_path": "records", "target": "y", "expression": 'yesterday("Europe/Minsk")'}}], "edges": []}
    result = asyncio.run(WorkflowEngine().execute(graph, context(datetime.fromisoformat("2026-07-31T08:00:00+03:00")), {"records": [{}]}))
    assert result["result"]["records"][0]["y"] == "2026-07-30"


def test_incompatible_ports_are_rejected():
    graph = {"nodes": [{"id": "fetch", "type": "http_request"}, {"id": "save", "type": "output"}], "edges": [{"source": "fetch", "target": "save"}]}
    assert "Несовместимые порты" in validate_dag(graph)[0]
