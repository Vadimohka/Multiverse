import httpx
import pytest
from workflow_engine.nodes import CrawlLinksNode
from workflow_engine.types import ExecutionContext


def context() -> ExecutionContext:
    return ExecutionContext(
        run_id="crawl-run",
        project_id="project",
        workflow_version_id="version-1",
        secrets={"_CRAWL_RESUME_SECRET": "test-resume-secret"},
    )


def success_response(method: str, url: str) -> httpx.Response:
    response = httpx.Response(
        200,
        request=httpx.Request(method, url),
        text="<main><h1>Generic detail</h1></main>",
        headers={"content-type": "text/html"},
    )
    response.extensions["fetch_attempts"] = []
    return response


@pytest.mark.asyncio
async def test_frontier_deduplicates_canonical_urls_and_enforces_domain_and_max_pages(monkeypatch):
    requested: list[str] = []

    async def request(_client, method, url, _policy, **_kwargs):
        requested.append(url)
        return success_response(method, url)

    monkeypatch.setattr("workflow_engine.nodes.request_with_policy", request)
    result = await CrawlLinksNode().execute(
        context(),
        {
            "url": "https://example.test/list",
            "records": [
                {"url": "/one?utm_source=test"},
                {"url": "https://EXAMPLE.test/one"},
                {"url": "https://outside.test/skip"},
                {"url": "/two"},
                {"url": "/three"},
            ],
        },
        {
            "input_path": "records",
            "allowed_domains": [],
            "same_origin_only": True,
            "max_pages": 2,
            "detail_fields": [{"name": "title", "selector": "h1"}],
            "save_artifacts": False,
            "delay_ms": 0,
        },
    )

    assert requested == ["https://example.test/one", "https://example.test/two"]
    assert result["completed_urls"] == requested


@pytest.mark.asyncio
async def test_listing_and_detail_share_cookie_session(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/list":
            return httpx.Response(
                200,
                request=request,
                text='<main><a href="/detail">Detail</a></main>',
                headers={"content-type": "text/html", "set-cookie": "session=ready; Path=/"},
            )
        assert request.headers.get("cookie") == "session=ready"
        return httpx.Response(
            200,
            request=request,
            text="<main><h1>Session detail</h1></main>",
            headers={"content-type": "text/html"},
        )

    transport = httpx.MockTransport(handler)
    async_client = httpx.AsyncClient
    monkeypatch.setattr(
        "workflow_engine.nodes.httpx.AsyncClient",
        lambda **kwargs: async_client(transport=transport, **kwargs),
    )
    result = await CrawlLinksNode().execute(
        context(),
        {},
        {
            "listing_url": "https://example.test/list",
            "listing_fetch_mode": "HTTP",
            "link_selector": "main a[href]",
            "detail_fields": [{"name": "title", "selector": "h1"}],
            "save_artifacts": False,
            "delay_ms": 0,
        },
    )

    assert result["records"][0]["title"] == "Session detail"


@pytest.mark.asyncio
async def test_partial_failure_resume_fetches_only_failed_canonical_url(monkeypatch):
    requested: list[str] = []
    failing = True

    async def request(_client, method, url, _policy, **_kwargs):
        requested.append(url)
        if failing and url.endswith("/retry"):
            response = httpx.Response(503, request=httpx.Request(method, url))
            response.extensions["fetch_attempts"] = []
            return response
        return success_response(method, url)

    monkeypatch.setattr("workflow_engine.nodes.request_with_policy", request)
    config = {
        "input_path": "records",
        "error_policy": "CONTINUE",
        "detail_fields": [{"name": "title", "selector": "h1"}],
        "save_artifacts": False,
        "delay_ms": 0,
        "_node_id": "crawl",
    }
    first = await CrawlLinksNode().execute(
        context(),
        {
            "url": "https://example.test/list",
            "records": [{"url": "/one"}, {"url": "/retry"}, {"url": "/two"}],
        },
        config,
    )
    assert len(first["records"]) == 2
    assert [item["url"] for item in first["failures"]] == ["https://example.test/retry"]
    assert first["resume_token"]

    requested.clear()
    failing = False
    second = await CrawlLinksNode().execute(
        context(),
        {"resume_token": first["resume_token"]},
        config,
    )

    assert requested == ["https://example.test/retry"]
    assert len(second["records"]) == 1
    assert second["resume_token"] is None

    with pytest.raises(ValueError, match="Invalid crawl resume token"):
        await CrawlLinksNode().execute(
            context(),
            {"resume_token": first["resume_token"][:-1] + "x"},
            config,
        )


@pytest.mark.asyncio
async def test_require_minimum_policy_rejects_too_few_records(monkeypatch):
    async def request(_client, method, url, _policy, **_kwargs):
        return httpx.Response(503, request=httpx.Request(method, url))

    monkeypatch.setattr("workflow_engine.nodes.request_with_policy", request)
    with pytest.raises(ValueError, match="minimum"):
        await CrawlLinksNode().execute(
            context(),
            {"url": "https://example.test/list", "records": [{"url": "/one"}]},
            {
                "input_path": "records",
                "error_policy": "REQUIRE_MINIMUM",
                "minimum_successful_records": 1,
                "save_artifacts": False,
                "delay_ms": 0,
            },
        )


@pytest.mark.asyncio
async def test_recursive_frontier_honours_depth_and_cycle_detection(monkeypatch):
    requested: list[str] = []

    async def request(_client, method, url, _policy, **_kwargs):
        requested.append(url)
        links = (
            '<a class="next" href="/two">Two</a><a class="next" href="/one">Cycle</a>'
            if url.endswith("/one")
            else '<a class="next" href="/three">Too deep</a>'
        )
        response = httpx.Response(
            200,
            request=httpx.Request(method, url),
            text=f"<main><h1>Page</h1>{links}</main>",
            headers={"content-type": "text/html"},
        )
        response.extensions["fetch_attempts"] = []
        return response

    monkeypatch.setattr("workflow_engine.nodes.request_with_policy", request)
    result = await CrawlLinksNode().execute(
        context(),
        {"url": "https://example.test/list", "records": [{"url": "/one"}]},
        {
            "input_path": "records",
            "recursive_link_selector": "a.next[href]",
            "max_depth": 2,
            "max_pages": 10,
            "save_artifacts": False,
            "delay_ms": 0,
        },
    )

    assert requested == ["https://example.test/one", "https://example.test/two"]
    assert result["discovered"] == 2
