from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.database import SessionLocal, engine
from app.models import (
    DatasetRun,
    Record,
    RecordObservation,
    RecordVersion,
    Run,
    Workflow,
)
from sqlalchemy import event, select


def test_current_page_query_count_is_constant(client, auth):
    project = client.get("/api/v1/projects", headers=auth).json()[0]
    suffix = uuid4().hex[:10]
    dataset = client.post(
        "/api/v1/datasets",
        headers=auth,
        json={
            "project_id": project["id"],
            "name": f"SQL page {suffix}",
            "slug": f"sql-page-{suffix}",
            "natural_key_fields": ["external_id"],
        },
    ).json()
    with SessionLocal() as db:
        workflow = db.scalar(select(Workflow).where(Workflow.project_id == project["id"]))
        assert workflow is not None
        run = Run(
            workflow_id=workflow.id,
            workflow_version=workflow.version,
            status="SUCCESS",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        db.add(run)
        db.flush()
        db.add(DatasetRun(run_id=run.id, dataset_id=dataset["id"], observed_count=40))
        base_time = datetime(2026, 8, 10, tzinfo=UTC)
        for index in range(40):
            payload = {"external_id": f"item-{index:03d}", "title": f"Item {index}"}
            record = Record(
                dataset_id=dataset["id"],
                natural_key=payload["external_id"],
                current_version=1,
                data_json=payload,
                data_hash=f"{index:064d}",
                review_status="APPROVED",
            )
            db.add(record)
            db.flush()
            version = RecordVersion(
                record_id=record.id,
                run_id=run.id,
                version_number=1,
                data_json=payload,
                data_hash=record.data_hash,
                review_status="APPROVED",
            )
            db.add(version)
            db.flush()
            observed_at = base_time + timedelta(seconds=index)
            db.add(
                RecordObservation(
                    dataset_id=dataset["id"],
                    record_id=record.id,
                    record_version_id=version.id,
                    run_id=run.id,
                    natural_key=record.natural_key,
                    observed_at=observed_at,
                    fetched_at=observed_at,
                )
            )
        db.commit()

    statements: list[str] = []

    def count_selects(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        response = client.get(
            f"/api/v1/datasets/{dataset['id']}/records?view=current&limit=25",
            headers=auth,
        )
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)

    assert response.status_code == 200, response.text
    assert len(response.json()["items"]) == 25
    assert response.json()["total"] == 40
    assert len(statements) <= 6, "Data API query count must not grow with dataset size"
    assert any("LIMIT" in statement.upper() for statement in statements), (
        "Data API must limit the joined record query in SQL"
    )
