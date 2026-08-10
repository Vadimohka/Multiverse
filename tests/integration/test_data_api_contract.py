from __future__ import annotations

import asyncio
from types import SimpleNamespace
from urllib.parse import quote
from uuid import uuid4

from app.database import SessionLocal
from app.models import Run, Workflow
from app.routers.runs import run_events
from sqlalchemy import select


def create_observed_dataset(client, auth, *, source_published_at: str) -> tuple[dict, dict, dict, dict]:
    project = client.get("/api/v1/projects", headers=auth).json()[0]
    suffix = uuid4().hex[:10]
    dataset = client.post(
        "/api/v1/datasets",
        headers=auth,
        json={
            "project_id": project["id"],
            "name": f"Observed records {suffix}",
            "slug": f"observed-records-{suffix}",
            "natural_key_fields": ["external_id"],
            "review_policy": {"new": False, "changed": False, "confidence_below": 0},
        },
    ).json()
    record = {
        "external_id": "item-1",
        "title": "First observation",
        "source_published_at": source_published_at,
        "fetched_at": "2026-08-10T12:35:03.412987Z",
    }
    graph = {
        "version": 1,
        "settings": {},
        "nodes": [
            {"id": "trigger", "type": "manual_trigger", "config": {}},
            {"id": "constant", "type": "set_constant", "config": {"value": record}},
            {
                "id": "mapping",
                "type": "mapping",
                "config": {
                    "input_path": "records",
                    "fields": [{"target": key, "source_path": key} for key in record],
                },
            },
            {
                "id": "save",
                "type": "output",
                "config": {
                    "input_path": "records",
                    "dataset_id": dataset["id"],
                    "natural_key_fields": ["external_id"],
                },
            },
        ],
        "edges": [
            {"source": "trigger", "target": "constant"},
            {"source": "constant", "target": "mapping"},
            {"source": "mapping", "target": "save"},
        ],
    }
    workflow_response = client.post(
        "/api/v1/workflows",
        headers=auth,
        json={"project_id": project["id"], "name": f"Observed workflow {suffix}", "graph_json": graph},
    )
    assert workflow_response.status_code == 201, workflow_response.text
    workflow = workflow_response.json()
    first_run = client.post(
        f"/api/v1/workflows/{workflow['id']}/run",
        headers=auth,
        json={"synchronous": True},
    ).json()
    second_run = client.post(
        f"/api/v1/workflows/{workflow['id']}/run",
        headers=auth,
        json={"synchronous": True},
    ).json()
    return dataset, workflow, first_run, second_run


def test_unchanged_record_is_present_in_latest_successful_run(client, auth):
    dataset, _, first_run, second_run = create_observed_dataset(
        client,
        auth,
        source_published_at="2026-08-10T12:34:56.800000Z",
    )

    response = client.get(
        f"/api/v1/datasets/{dataset['slug']}/records?view=latest_run",
        headers=auth,
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["meta"]["run_id"] == second_run["id"]
    assert payload["meta"]["run_id"] != first_run["id"]
    assert [item["data"]["external_id"] for item in payload["items"]] == ["item-1"]
    assert payload["items"][0]["timestamps"] == {
        "source_published_at": "2026-08-10T12:34:56.800000Z",
        "source_modified_at": None,
        "fetched_at": "2026-08-10T12:35:03.412987Z",
        "observed_at": payload["items"][0]["timestamps"]["observed_at"],
    }

    history = client.get(
        f"/api/v1/datasets/{dataset['id']}/records?view=history",
        headers=auth,
    ).json()
    assert len(history["items"]) == 2
    assert len({item["record_version_id"] for item in history["items"]}) == 1


def test_source_publication_filter_uses_half_open_exact_second(client, auth):
    dataset, _, _, _ = create_observed_dataset(
        client,
        auth,
        source_published_at="2026-08-10T12:34:56.800000Z",
    )

    matching = client.get(
        f"/api/v1/datasets/{dataset['id']}/records"
        "?view=current&time_basis=source_published_at&at=2026-08-10T12:34:56Z",
        headers=auth,
    )
    following_second = client.get(
        f"/api/v1/datasets/{dataset['id']}/records"
        "?view=current&time_basis=source_published_at&at=2026-08-10T12:34:57Z",
        headers=auth,
    )
    naive = client.get(
        f"/api/v1/datasets/{dataset['id']}/records"
        "?time_basis=source_published_at&from=2026-08-10T12:34:56",
        headers=auth,
    )

    assert matching.status_code == 200 and len(matching.json()["items"]) == 1
    assert following_second.status_code == 200 and following_second.json()["items"] == []
    assert naive.status_code == 422


def test_empty_success_run_event_stream_terminates(client):
    with SessionLocal() as db:
        workflow = db.scalar(select(Workflow))
        assert workflow is not None
        run = Run(
            workflow_id=workflow.id,
            workflow_version=workflow.version,
            status="SUCCESS_EMPTY_ALLOWED",
            output_json={"result": {"records": []}},
        )
        db.add(run)
        db.commit()
        run_id = run.id

    async def collect() -> list[str]:
        response = await run_events(run_id, _=SimpleNamespace())
        return [chunk async for chunk in response.body_iterator]

    chunks = asyncio.run(asyncio.wait_for(collect(), timeout=0.2))
    assert len(chunks) == 1
    assert "SUCCESS_EMPTY_ALLOWED" in chunks[0]


def test_latest_successful_run_can_be_empty(client, auth):
    dataset, workflow, _, prior_run = create_observed_dataset(
        client,
        auth,
        source_published_at="2026-08-10T12:34:56Z",
    )
    graph = workflow["graph_json"]
    next(node for node in graph["nodes"] if node["id"] == "constant")["config"]["value"] = []
    next(node for node in graph["nodes"] if node["id"] == "save")["config"]["on_empty"] = "allow"
    updated = client.patch(
        f"/api/v1/workflows/{workflow['id']}",
        headers=auth,
        json={"graph_json": graph},
    )
    assert updated.status_code == 200, updated.text
    empty_run = client.post(
        f"/api/v1/workflows/{workflow['id']}/run",
        headers=auth,
        json={"synchronous": True},
    ).json()
    assert empty_run["status"] == "SUCCESS_EMPTY_ALLOWED"

    response = client.get(
        f"/api/v1/datasets/{dataset['id']}/records?view=latest_run",
        headers=auth,
    )

    assert response.status_code == 200
    assert response.json()["meta"]["run_id"] == empty_run["id"]
    assert response.json()["meta"]["run_id"] != prior_run["id"]
    assert response.json()["items"] == []


def test_record_cursor_is_stable_and_does_not_repeat_items(client, auth):
    dataset, workflow, _, _ = create_observed_dataset(
        client,
        auth,
        source_published_at="2026-08-10T12:34:56Z",
    )
    graph = workflow["graph_json"]
    next(node for node in graph["nodes"] if node["id"] == "constant")["config"]["value"] = [
        {
            "external_id": f"item-{index}",
            "title": f"Item {index}",
            "source_published_at": f"2026-08-10T12:34:5{index}Z",
            "fetched_at": "2026-08-10T12:35:03Z",
        }
        for index in (1, 2, 3)
    ]
    assert client.patch(
        f"/api/v1/workflows/{workflow['id']}",
        headers=auth,
        json={"graph_json": graph},
    ).status_code == 200
    client.post(
        f"/api/v1/workflows/{workflow['id']}/run",
        headers=auth,
        json={"synchronous": True},
    )

    first = client.get(
        f"/api/v1/datasets/{dataset['id']}/records?view=current"
        "&time_basis=source_published_at&limit=2",
        headers=auth,
    ).json()
    cursor = first["pagination"]["next_cursor"]
    assert cursor
    second = client.get(
        f"/api/v1/datasets/{dataset['id']}/records?view=current"
        f"&time_basis=source_published_at&limit=2&cursor={quote(cursor, safe='')}",
        headers=auth,
    ).json()

    first_ids = [item["record_id"] for item in first["items"]]
    second_ids = [item["record_id"] for item in second["items"]]
    assert len(first_ids) == 2
    assert len(second_ids) == 1
    assert set(first_ids).isdisjoint(second_ids)
    assert second["pagination"]["next_cursor"] is None


def test_site_specific_values_in_executable_workflow_are_not_blocked(client, auth):
    project = client.get("/api/v1/projects", headers=auth).json()[0]
    suffix = uuid4().hex[:10]
    graph = {
        "version": 1,
        "settings": {},
        "nodes": [
            {"id": "trigger", "type": "manual_trigger", "config": {}},
            {
                "id": "constant",
                "type": "set_constant",
                "config": {"value": {"source_name": "БВФБ", "url": "https://www.bcse.by/news/one"}},
            },
            {"id": "output", "type": "output", "config": {"input_path": "records", "on_empty": "allow"}},
        ],
        "edges": [
            {"source": "trigger", "target": "constant"},
            {"source": "constant", "target": "output"},
        ],
    }
    created = client.post(
        "/api/v1/workflows",
        headers=auth,
        json={"project_id": project["id"], "name": f"Site preset {suffix}", "graph_json": graph},
    )
    assert created.status_code == 201
    workflow_id = created.json()["id"]

    visible_ids = {item["id"] for item in client.get("/api/v1/workflows", headers=auth).json()}
    run = client.post(
        f"/api/v1/workflows/{workflow_id}/run",
        headers=auth,
        json={"synchronous": True},
    )

    assert workflow_id in visible_ids
    assert run.status_code == 201, run.text
    assert run.json()["status"] == "SUCCESS"


def test_bcse_news_preset_is_bootstrapped_as_an_executable_chain(client, auth):
    projects = client.get("/api/v1/projects", headers=auth).json()
    project = next(item for item in projects if item["slug"] == "bcse-news")
    datasets = client.get(f"/api/v1/datasets?project_id={project['id']}", headers=auth).json()
    sources = client.get(f"/api/v1/sources?project_id={project['id']}", headers=auth).json()
    workflows = client.get(f"/api/v1/workflows?project_id={project['id']}", headers=auth).json()

    dataset = next(item for item in datasets if item["slug"] == "bcse-news")
    source = next(item for item in sources if item["entry_url"] == "https://www.bcse.by/press_center/calendar")
    workflow = next(item for item in workflows if item["name"] == "БВФБ: новости")
    crawl = next(node for node in workflow["graph_json"]["nodes"] if node["id"] == "crawl")["config"]

    assert workflow["graph_json"]["settings"]["source_id"] == source["id"]
    assert workflow["graph_json"]["settings"]["dataset_id"] == dataset["id"]
    assert source["fetch_mode"] == "HTTP"
    assert crawl["detail_fields"]
    assert crawl["date_range_query"]["from_param"] == "sFrom"


def test_scoped_api_token_can_only_read_its_dataset(client, auth):
    dataset, _, _, _ = create_observed_dataset(
        client,
        auth,
        source_published_at="2026-08-10T12:34:56Z",
    )
    other_dataset = next(
        item for item in client.get("/api/v1/datasets", headers=auth).json()
        if item["id"] != dataset["id"]
    )
    created = client.post(
        "/api/v1/api-tokens",
        headers=auth,
        json={
            "name": "AI news reader",
            "scopes": ["datasets:read"],
            "dataset_ids": [dataset["id"]],
        },
    )

    assert created.status_code == 201, created.text
    raw_token = created.json()["token"]
    token_auth = {"Authorization": f"Bearer {raw_token}"}
    allowed = client.get(
        f"/api/v1/datasets/{dataset['slug']}/records"
        "?time_basis=source_published_at&from=2026-08-10T12:34:56Z",
        headers=token_auth,
    )
    forbidden = client.get(
        f"/api/v1/datasets/{other_dataset['id']}/records",
        headers=token_auth,
    )
    pending = client.get(
        f"/api/v1/datasets/{dataset['id']}/records?include_pending=true",
        headers=token_auth,
    )
    workflow_access = client.get("/api/v1/workflows", headers=token_auth)

    assert allowed.status_code == 200, allowed.text
    assert [item["data"]["external_id"] for item in allowed.json()["items"]] == ["item-1"]
    assert forbidden.status_code == 403
    assert pending.status_code == 403
    assert workflow_access.status_code == 401


def test_data_api_openapi_exposes_views_time_bases_and_response_envelope(client):
    operation = client.get("/api/v1/openapi.json").json()["paths"][
        "/api/v1/datasets/{dataset_id}/records"
    ]["get"]
    parameters = {item["name"]: item for item in operation["parameters"]}

    assert parameters["view"]["schema"]["enum"] == ["current", "latest_run", "run", "history"]
    assert parameters["time_basis"]["schema"]["enum"] == [
        "source_published_at",
        "source_modified_at",
        "fetched_at",
        "observed_at",
    ]
    response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert response_schema["$ref"].endswith("/DataRecordsResponse")
