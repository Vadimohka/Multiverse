#!/usr/bin/env python3
"""Create 10k versioned records and verify bounded cursor pagination."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps" / "api"), str(ROOT / "packages")]


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="multiverse-data-api-") as directory:
        os.environ["DATABASE_URL"] = f"sqlite:///{Path(directory) / 'load.db'}"
        os.environ.setdefault("DEFAULT_ADMIN_EMAIL", "admin@parser.local")
        os.environ.setdefault("DEFAULT_ADMIN_PASSWORD", "load-test-password")
        os.environ.setdefault("S3_ENDPOINT", "http://127.0.0.1:9")

        from fastapi.testclient import TestClient
        from sqlalchemy import event, insert, select

        from app.database import SessionLocal, engine
        from app.main import app
        from app.models import (
            Dataset,
            DatasetRun,
            Record,
            RecordObservation,
            RecordVersion,
            Run,
            Workflow,
        )

        started = time.perf_counter()
        with TestClient(app) as client:
            login = client.post(
                "/api/v1/auth/login",
                json={
                    "email": os.environ["DEFAULT_ADMIN_EMAIL"],
                    "password": os.environ["DEFAULT_ADMIN_PASSWORD"],
                },
            )
            login.raise_for_status()
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            project = client.get("/api/v1/projects", headers=headers).json()[0]
            dataset_response = client.post(
                "/api/v1/datasets",
                headers=headers,
                json={
                    "project_id": project["id"],
                    "name": "Data API load fixture",
                    "slug": "data-api-load-fixture",
                    "natural_key_fields": ["external_id"],
                },
            )
            dataset_response.raise_for_status()
            dataset = dataset_response.json()

            with SessionLocal() as db:
                workflow = db.scalar(select(Workflow).where(Workflow.project_id == project["id"]))
                if workflow is None:
                    raise RuntimeError("Seed workflow unavailable")
                run_id = str(uuid4())
                now = datetime.now(UTC)
                db.execute(insert(Run), [{
                    "id": run_id,
                    "workflow_id": workflow.id,
                    "workflow_version": workflow.version,
                    "status": "SUCCESS",
                    "started_at": now,
                    "finished_at": now,
                    "created_at": now,
                    "input_json": {},
                    "output_json": {},
                    "error_json": {},
                }])
                db.execute(insert(DatasetRun), [{
                    "id": str(uuid4()),
                    "run_id": run_id,
                    "dataset_id": dataset["id"],
                    "observed_count": 10_000,
                    "created_at": now,
                }])
                records = []
                versions = []
                observations = []
                for index in range(10_000):
                    record_id = str(uuid4())
                    version_id = str(uuid4())
                    observed_at = now + timedelta(microseconds=index)
                    payload = {"external_id": f"item-{index:05d}", "title": f"Item {index}"}
                    digest = f"{index:064d}"
                    records.append({
                        "id": record_id,
                        "dataset_id": dataset["id"],
                        "natural_key": payload["external_id"],
                        "current_version": 1,
                        "data_json": payload,
                        "data_hash": digest,
                        "status": "ACTIVE",
                        "review_status": "APPROVED",
                        "confidence": 1.0,
                        "created_at": now,
                        "updated_at": now,
                    })
                    versions.append({
                        "id": version_id,
                        "record_id": record_id,
                        "run_id": run_id,
                        "version_number": 1,
                        "data_json": payload,
                        "data_hash": digest,
                        "review_status": "APPROVED",
                        "confidence": 1.0,
                        "created_at": now,
                    })
                    observations.append({
                        "id": str(uuid4()),
                        "dataset_id": dataset["id"],
                        "record_id": record_id,
                        "record_version_id": version_id,
                        "run_id": run_id,
                        "natural_key": payload["external_id"],
                        "fetched_at": observed_at,
                        "observed_at": observed_at,
                        "created_at": observed_at,
                    })
                db.execute(insert(Record), records)
                db.execute(insert(RecordVersion), versions)
                db.execute(insert(RecordObservation), observations)
                db.commit()

            page_counts: list[int] = []
            current_selects = 0

            def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
                nonlocal current_selects
                if statement.lstrip().upper().startswith("SELECT"):
                    current_selects += 1

            event.listen(engine, "before_cursor_execute", count_selects)
            try:
                seen: set[str] = set()
                cursor = None
                while True:
                    current_selects = 0
                    url = f"/api/v1/datasets/{dataset['id']}/records?view=current&sort=asc&limit=500"
                    if cursor:
                        url += f"&cursor={cursor}"
                    response = client.get(url, headers=headers)
                    response.raise_for_status()
                    payload = response.json()
                    page_counts.append(current_selects)
                    ids = [item["record_id"] for item in payload["items"]]
                    if seen.intersection(ids):
                        raise AssertionError("cursor pagination repeated records")
                    seen.update(ids)
                    cursor = payload["pagination"]["next_cursor"]
                    if not cursor:
                        break
            finally:
                event.remove(engine, "before_cursor_execute", count_selects)

            if len(seen) != 10_000:
                raise AssertionError(f"expected 10000 records, got {len(seen)}")
            if max(page_counts) > 6:
                raise AssertionError(f"unbounded SQL count: {max(page_counts)}")
            print(json.dumps({
                "records": len(seen),
                "pages": len(page_counts),
                "max_selects_per_page": max(page_counts),
                "elapsed_seconds": round(time.perf_counter() - started, 3),
            }))


if __name__ == "__main__":
    main()
