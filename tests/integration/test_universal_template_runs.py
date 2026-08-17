"""End-to-end: new universal templates persist records from fixture sources.

Each test instantiates a system template onto a synthetic demo source and runs
it synchronously through the public API — the same path the workflow editor's
«Запустить» button uses.  No site-specific configuration exists anywhere; the
fixtures are generic public-markup shapes.
"""

from pathlib import Path

import httpx
import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "universal"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture
def static_site(monkeypatch):
    """Serve the universal fixtures for any example.test URL, by path."""

    pages = {
        f"/{name}": fixture_bytes(name)
        for name in ("rate_matrix.html", "product_cards.html", "cards_with_noise.html", "spa_rendered.html")
    }

    async def request(_client, method, url, _policy, **_kwargs):
        path = httpx.URL(url).path
        content = pages.get(path)
        if content is None and path.startswith("/article/"):
            content = pages["/cards_with_noise.html"]
        response = httpx.Response(
            200,
            request=httpx.Request(method, url),
            content=content or b"<html><body>not found</body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )
        response.extensions["fetch_attempts"] = []
        return response

    monkeypatch.setattr("workflow_engine.nodes.request_with_policy", request)
    # The v2 traverse facade imports the transport helper from its own module
    # at call time, so detail fan-out must be intercepted there too.
    monkeypatch.setattr("workflow_engine.transport.request_with_policy", request)


def _setup(client, auth, *, entry_url, template_id, slug):
    project = client.get("/api/v1/projects", headers=auth).json()[0]
    source = client.post("/api/v1/sources", headers=auth, json={
        "project_id": project["id"], "name": f"demo {slug}", "entry_url": entry_url,
        "base_url": "https://example.test/", "fetch_mode": "HTTP",
    }).json()
    dataset = client.post("/api/v1/datasets", headers=auth, json={
        "project_id": project["id"], "name": f"demo {slug}", "slug": f"demo-{slug}-{project['id'][:8]}",
        "review_policy": {"new": False, "changed": False, "confidence_below": 0.0},
    }).json()
    workflow = client.post(f"/api/v1/workflow-templates/{template_id}/instantiate", headers=auth, json={
        "project_id": project["id"], "source_id": source["id"], "dataset_id": dataset["id"],
        "name": f"demo {slug} workflow",
    }).json()
    return workflow


def _run(client, auth, workflow) -> dict:
    response = client.post(f"/api/v1/workflows/{workflow['id']}/run", headers=auth, json={
        "source_id": workflow["graph_json"]["settings"]["source_id"],
        "inputs": {},
        "synchronous": True,
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_rate_matrix_template_persists_distinct_rows(client, auth, static_site):
    workflow = _setup(
        client, auth,
        entry_url="https://example.test/rate_matrix.html",
        template_id="system-universal-rate-matrix",
        slug="rates",
    )
    run = _run(client, auth, workflow)
    persistence = run["output_json"]["persistence"]
    assert persistence["created"] == 5, persistence
    records = run["output_json"]["result"]["records"]
    assert {row["row_index"] for row in records} == {0, 1, 2, 3, 4}
    assert all(row["source_id"] == workflow["graph_json"]["settings"]["source_id"] for row in records)
    assert all(row["fetched_at"] for row in records)
    assert run["status"] in {"SUCCESS", "WAITING_FOR_REVIEW"}

    # A re-run must dedupe onto the same structural identities, not duplicate.
    second = _run(client, auth, workflow)
    assert second["output_json"]["persistence"]["created"] == 0
    assert second["output_json"]["persistence"]["unchanged"] == 5


def test_product_cards_template_auto_clusters_and_persists(client, auth, static_site):
    workflow = _setup(
        client, auth,
        entry_url="https://example.test/product_cards.html",
        template_id="system-universal-product-cards",
        slug="cards",
    )
    run = _run(client, auth, workflow)
    persistence = run["output_json"]["persistence"]
    assert persistence["created"] == 4, persistence
    records = run["output_json"]["result"]["records"]
    assert all(row["url"].startswith("/business/deposits/") for row in records)
    assert all(row["source_id"] and row["fetched_at"] for row in records)


def test_news_window_template_collects_full_detail_pages(client, auth, static_site):
    workflow = _setup(
        client, auth,
        entry_url="https://example.test/cards_with_noise.html",
        template_id="system-universal-news-window",
        slug="news",
    )
    # Site-specific selectors live only in the workflow copy: pin the detail
    # links and the title field exactly as the UI editor would.
    graph = workflow["graph_json"]
    traverse = next(node for node in graph["nodes"] if node["id"] == "traverse")
    traverse["config"]["detail"]["selector"] = "article.article-card a"
    traverse["config"]["detail"]["fields"] = [{"name": "title", "selector": "h1"}]
    updated = client.patch(f"/api/v1/workflows/{workflow['id']}", headers=auth, json={"graph_json": graph})
    assert updated.status_code == 200, updated.text

    run = _run(client, auth, workflow)
    persistence = run["output_json"]["persistence"]
    assert persistence["created"] == 3, persistence
    records = run["output_json"]["result"]["records"]
    assert all(row["url"].startswith("https://example.test/article/") for row in records)
    assert all(row["title"] and row["fetched_at"] and row["source_id"] for row in records)
