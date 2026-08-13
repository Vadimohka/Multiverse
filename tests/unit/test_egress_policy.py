import httpx
import pytest
from workflow_engine.egress import EgressPolicy, EgressPolicyError, request_with_egress_policy
from workflow_engine.transport import FetchPolicy


def resolver(host: str, _port: int) -> list[str]:
    return {"public.example": ["93.184.216.34"], "private.example": ["127.0.0.1"]}[host]


@pytest.mark.parametrize(
    "url",
    [
        "http://private.example/",
        "file:///etc/passwd",
        "http://user:password@public.example/",
        "http://public.example:8080/",
    ],
)
def test_egress_policy_rejects_non_public_or_invalid_urls(url: str):
    with pytest.raises(EgressPolicyError):
        EgressPolicy().validate_url(url, resolver=resolver)


@pytest.mark.asyncio
async def test_every_redirect_hop_is_revalidated_and_recorded():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "public.example":
            return httpx.Response(302, headers={"location": "https://final.example/items"}, request=request)
        return httpx.Response(200, text="ok", request=request)

    def public_resolver(host: str, _port: int) -> list[str]:
        return {"public.example": ["93.184.216.34"], "final.example": ["1.1.1.1"]}[host]

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        response = await request_with_egress_policy(
            client,
            "GET",
            "https://public.example/start",
            FetchPolicy(retries=0),
            resolver=public_resolver,
        )
    assert response.status_code == 200
    assert [entry["requested_url"] for entry in response.extensions["redirect_chain"]] == [
        "https://public.example/start",
        "https://final.example/items",
    ]


@pytest.mark.asyncio
async def test_private_redirect_target_is_blocked_before_it_is_requested():
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://private.example/internal"}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        with pytest.raises(EgressPolicyError, match="not public"):
            await request_with_egress_policy(
                client,
                "GET",
                "https://public.example/start",
                FetchPolicy(retries=0),
                resolver=resolver,
            )
    assert calls == ["https://public.example/start"]
