from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request(base: str, path: str, method: str = "GET", data: Any = None, token: str = "") -> tuple[int, Any]:
    body = json.dumps(data).encode() if data is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{base}{path}", data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            content = response.read()
            content_type = response.headers.get("content-type", "")
            return response.status, json.loads(content) if "json" in content_type else content
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{method} {path}: HTTP {exc.code}: {exc.read().decode()}") from exc


def wait_for_health(base: str, process: subprocess.Popen[bytes]) -> None:
    for _ in range(120):
        if process.poll() is not None:
            raise RuntimeError("API завершился до healthcheck")
        try:
            status, payload = request(base, "/health")
            if status == 200 and payload.get("status") == "ok":
                return
        except Exception:
            pass
        time.sleep(0.25)
    raise RuntimeError("API не прошёл healthcheck")


def main() -> None:
    port = free_port()
    api_base = f"http://127.0.0.1:{port}/api/v1"
    with tempfile.TemporaryDirectory(prefix="parser-studio-smoke-") as tmp:
        database = Path(tmp) / "smoke.db"
        # Keep the process log outside the disposable SQLite directory.  On
        # Windows a just-exited child can transiently retain this log handle,
        # which must not turn a successful smoke run into a cleanup failure.
        log_path = Path(tempfile.gettempdir()) / f"parser-studio-smoke-{port}.log"
        env = os.environ.copy()
        env.update(
            {
                "DATABASE_URL": f"sqlite:///{database}",
                "INTERNAL_API_URL": f"http://127.0.0.1:{port}",
                "DEFAULT_ADMIN_EMAIL": "admin@parser.local",
                "DEFAULT_ADMIN_PASSWORD": "Admin123!",
                # ``PYTHONPATH`` uses ``;`` on Windows and ``:`` on POSIX.
                # Keeping this portable makes the smoke test exercise the
                # current checkout on every documented development platform.
                "PYTHONPATH": os.pathsep.join(
                    [str(ROOT / "apps/api"), str(ROOT / "packages")]
                ),
            }
        )
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                wait_for_health(api_base, process)
                _, login = request(
                    api_base,
                    "/auth/login",
                    "POST",
                    {"email": "admin@parser.local", "password": "Admin123!"},
                )
                token = login["access_token"]
                _, workflows = request(api_base, "/workflows", token=token)
                # The HTTP demo intentionally points to the application's own
                # loopback endpoint.  Production egress policy must reject
                # such a private address, so the portable smoke scenario uses
                # the seeded input workflow; network safety is covered by the
                # dedicated egress regression suite.
                workflow = next(item for item in workflows if item["name"] == "Нормализация депозитов")
                _, validation = request(api_base, f"/workflows/{workflow['id']}/validate", "POST", {}, token)
                assert validation["valid"], validation
                request(api_base, f"/workflows/{workflow['id']}/publish", "POST", {}, token)
                _, run = request(
                    api_base,
                    f"/workflows/{workflow['id']}/run",
                    "POST",
                    {
                        "synchronous": True,
                        "inputs": {
                            "records": [
                                {"institution_name": "Демо Банк", "product_name": "Сберегательный плюс", "currency": "BYN", "term": "3 месяца", "rate": "12,5%"},
                                {"institution_name": "Финанс Банк", "product_name": "Надёжный год", "currency": "BYN", "term": "1 год", "rate": "СР + 1,25 п.п."},
                                {"institution_name": "Капитал Банк", "product_name": "Валютный", "currency": "USD", "term": "31–60 дней", "rate": "до 3,2%"},
                            ]
                        },
                    },
                    token,
                )
                assert run["status"] == "WAITING_FOR_REVIEW", run
                _, detail = request(api_base, f"/runs/{run['id']}", token=token)
                assert len(detail["nodes"]) == 5
                assert all(node["status"] == "SUCCESS" for node in detail["nodes"])
                persistence = detail["run"]["output_json"]["persistence"]
                assert persistence["created"] == 3
                assert persistence["review_tasks"] == 3
                _, reviews = request(api_base, "/review", token=token)
                assert len(reviews) == 3
                for item in reviews:
                    request(api_base, f"/review/{item['id']}/approve", "POST", {}, token)
                _, datasets = request(api_base, "/datasets", token=token)
                dataset = next(item for item in datasets if item["slug"] == "demo-deposits")
                _, page = request(api_base, f"/datasets/{dataset['id']}/records", token=token)
                assert len(page["items"]) == 3
                assert all(record["review_status"] == "APPROVED" for record in page["items"])
                _, xlsx = request(
                    api_base,
                    f"/exports?dataset_id={dataset['id']}&format=xlsx",
                    "POST",
                    None,
                    token,
                )
                assert xlsx[:2] == b"PK"
                _, metrics = request(api_base, "/metrics")
                assert b"runs_total" in metrics and b"review_queue_size" in metrics
                _, catalog = request(api_base, "/workflows/catalog", token=token)
                assert len(catalog) >= 20
                print(
                    json.dumps(
                        {
                            "status": "passed",
                            "workflow": workflow["name"],
                            "run_status": run["status"],
                            "nodes": len(detail["nodes"]),
                            "records": len(page["items"]),
                            "review_tasks": persistence["review_tasks"],
                            "catalog_nodes": len(catalog),
                            "xlsx_bytes": len(xlsx),
                        },
                        ensure_ascii=False,
                    )
                )
            except Exception:
                log.flush()
                print(log_path.read_text(errors="replace"), file=sys.stderr)
                raise
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        # Windows cannot remove a temporary directory while this parent-owned
        # file handle is still open.  Leaving the ``with`` block before the
        # TemporaryDirectory cleanup also keeps failure logs readable above.
        try:
            log_path.unlink()
        except PermissionError:
            # A transiently locked diagnostic log is harmless and remains
            # available for inspection; the test result itself is authoritative.
            pass


if __name__ == "__main__":
    main()
