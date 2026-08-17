"""Regression: the Runs list must stay light even with huge run payloads.

Two runs each carrying ~4 MB ``output_json`` (raw bodies live there in
production) previously made ``GET /api/v1/runs`` exceed 100 MB and freeze the
Runs page.  The list now serves denormalised counters and the error code
only; the full payload stays on the single-run detail endpoint.
"""

import json

from app.database import SessionLocal
from app.models import Run


def _seed_runs(workflow: dict) -> tuple[str, str]:
    with SessionLocal() as db:
        big_body = "x" * (2 * 1024 * 1024)
        output = {"result": {"records": []}, "persistence": {"created": 3, "updated": 2, "unchanged": 1}}
        run_ids = []
        for index in range(2):
            run = Run(
                workflow_id=workflow["id"],
                workflow_version=workflow["version"],
                status="SUCCESS",
                output_json={**output, "node_outputs": {"acquire": {"body": f"{big_body}-{index}"}}},
                error_json={},
                records_created=3,
                records_updated=2,
                records_unchanged=1,
            )
            db.add(run)
            db.flush()
            run_ids.append(run.id)
        db.commit()
        return tuple(run_ids)


def test_runs_list_omits_full_payloads_and_stays_small(client, auth):
    project = client.get("/api/v1/projects", headers=auth).json()[0]
    workflow = client.post("/api/v1/workflows", headers=auth, json={
        "project_id": project["id"],
        "name": "Light runs list regression",
        "graph_json": {"version": 1, "settings": {}, "nodes": [], "edges": []},
    }).json()
    first_id, second_id = _seed_runs(workflow)

    response = client.get("/api/v1/runs", headers=auth)
    assert response.status_code == 200, response.text
    listed = {item["id"]: item for item in response.json()}
    assert first_id in listed and second_id in listed
    row = listed[first_id]
    assert set(row) == {
        "id", "workflow_id", "workflow_version", "source_id", "status",
        "created_at", "started_at", "finished_at", "error_code",
        "records_created", "records_updated", "records_unchanged",
    }
    assert row["records_created"] == 3
    assert row["records_updated"] == 2
    assert row["records_unchanged"] == 1
    # Two multi-megabyte runs must not leak into the list response.
    assert len(response.content) < 100_000

    detail = client.get(f"/api/v1/runs/{first_id}", headers=auth).json()
    assert len(detail["run"]["output_json"]["node_outputs"]["acquire"]["body"]) > 2 * 1024 * 1024
    assert json.dumps(detail)  # full payload stays JSON-serialisable


def test_runs_list_exposes_denormalised_error_code(client, auth):
    project = client.get("/api/v1/projects", headers=auth).json()[0]
    workflow = client.post("/api/v1/workflows", headers=auth, json={
        "project_id": project["id"],
        "name": "Light runs list error code regression",
        "graph_json": {"version": 1, "settings": {}, "nodes": [], "edges": []},
    }).json()
    with SessionLocal() as db:
        run = Run(
            workflow_id=workflow["id"],
            workflow_version=workflow["version"],
            status="FAILED",
            output_json={},
            error_json={"code": "DUPLICATE_NATURAL_KEY", "message": "Duplicate natural keys in one run"},
        )
        db.add(run)
        db.commit()
        run_id = run.id

    listed = {item["id"]: item for item in client.get("/api/v1/runs", headers=auth).json()}
    assert listed[run_id]["error_code"] == "DUPLICATE_NATURAL_KEY"
