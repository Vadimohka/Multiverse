from datetime import UTC, datetime

import httpx
import pytest
from workflow_engine.nodes import CrawlLinksNode, DownloadFileNode, FollowLinksNode, HTTPRequestNode
from workflow_engine.transport import FetchPolicy, request_with_policy, retry_after_seconds
from workflow_engine.types import ExecutionContext


@pytest.mark.asyncio
async def test_retry_after_and_backoff_are_applied_by_one_policy():
    responses = iter(
        [
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(503),
            httpx.Response(200, json={"ok": True}),
        ]
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        response = next(responses)
        response.request = request
        return response

    delays: list[float] = []

    async def delay(seconds: float) -> None:
        delays.append(seconds)

    policy = FetchPolicy.from_config(
        {
            "request_retries": 2,
            "retry_backoff_seconds": 1,
            "retry_statuses": [429, 503],
            "respect_retry_after": True,
            "request_timeout": 10,
        }
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await request_with_policy(
            client,
            "GET",
            "https://example.test/items",
            policy,
            delay=delay,
            now=lambda: datetime(2026, 8, 11, tzinfo=UTC),
        )

    assert response.status_code == 200
    assert delays == [2.0, 1.0]
    assert response.extensions["fetch_attempts"] == [
        {"attempt": 1, "status_code": 429, "error": None, "delay_seconds": 2.0},
        {"attempt": 2, "status_code": 503, "error": None, "delay_seconds": 1.0},
        {"attempt": 3, "status_code": 200, "error": None, "delay_seconds": 0.0},
    ]


def test_fetch_policy_rejects_unbounded_values():
    with pytest.raises(ValueError, match="request_timeout"):
        FetchPolicy.from_config({"request_timeout": 0})
    with pytest.raises(ValueError, match="request_retries"):
        FetchPolicy.from_config({"request_retries": 100})


def test_retry_after_http_date_is_supported():
    seconds = retry_after_seconds(
        "Tue, 11 Aug 2026 09:00:05 GMT",
        now=lambda: datetime(2026, 8, 11, 9, 0, tzinfo=UTC),
    )
    assert seconds == 5.0


@pytest.mark.asyncio
async def test_fetch_nodes_expose_shared_policy_diagnostics(monkeypatch):
    calls: list[tuple[str, str, FetchPolicy]] = []

    async def fake_request(_client, method, url, policy, **_kwargs):
        calls.append((method, url, policy))
        request = httpx.Request(method, url)
        body = b"<main><h1>Generic detail</h1></main>"
        response = httpx.Response(
            200,
            request=request,
            content=body,
            headers={
                "content-type": "text/html",
                "content-disposition": 'attachment; filename="item.html"',
            },
        )
        response.extensions["fetch_attempts"] = [
            {"attempt": 1, "status_code": 200, "error": None, "delay_seconds": 0.0}
        ]
        return response

    monkeypatch.setattr("workflow_engine.nodes.request_with_policy", fake_request)
    context = ExecutionContext(run_id="transport", project_id="project", workflow_version_id="1")
    shared = {"request_retries": 3, "retry_backoff_seconds": 0.25, "request_timeout": 12}

    http_result = await HTTPRequestNode().execute(
        context,
        {},
        {**shared, "url": "https://example.test/http"},
    )
    download_result = await DownloadFileNode().execute(
        context,
        {},
        {**shared, "url": "https://example.test/file"},
    )
    follow_result = await FollowLinksNode().execute(
        context,
        {"records": [{"url": "https://example.test/follow"}]},
        {
            **shared,
            "input_collection": "records",
            "detail_fields": [{"target": "title", "selector": "h1"}],
        },
    )
    crawl_result = await CrawlLinksNode().execute(
        context,
        {"records": [{"url": "https://example.test/crawl"}]},
        {
            **shared,
            "input_path": "records",
            "detail_fields": [{"name": "title", "selector": "h1"}],
            "save_artifacts": False,
            "delay_ms": 0,
        },
    )

    assert len(calls) == 4
    assert all(policy.retries == 3 and policy.backoff_seconds == 0.25 for _, _, policy in calls)
    assert http_result["fetch_attempts"][0]["status_code"] == 200
    assert download_result["fetch_attempts"][0]["status_code"] == 200
    assert follow_result["progress"][0]["fetch_attempts"][0]["status_code"] == 200
    assert crawl_result["detail_diagnostics"][0]["fetch_attempts"][0]["status_code"] == 200
