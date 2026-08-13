import os

from workflow_engine import egress

os.environ['DATABASE_URL']='sqlite:///./test_parser_studio.db'
os.environ['DEFAULT_ADMIN_EMAIL']='admin@parser.local'
os.environ['DEFAULT_ADMIN_PASSWORD']='test-only-admin-password'
os.environ['S3_ENDPOINT']='http://127.0.0.1:9'
from pathlib import Path

import pytest
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def safe_example_test_resolver(monkeypatch):
    """Keep hermetic HTTP fixtures behind the real egress validator.

    Production DNS is never bypassed: tests use a documentation-only hostname
    and a public resolver result so request/redirect policy remains exercised.
    """
    original = egress.default_resolver

    def resolver(host: str, port: int) -> list[str]:
        if host.lower() in {"example.test", "outside.test"}:
            return ["93.184.216.34"]
        return original(host, port)

    monkeypatch.setattr(egress, "default_resolver", resolver)
    monkeypatch.setattr("workflow_engine.nodes.default_resolver", resolver)
    yield


@pytest.fixture(scope='session',autouse=True)
def cleanup_db():
    Path('test_parser_studio.db').unlink(missing_ok=True)
    yield
    Path('test_parser_studio.db').unlink(missing_ok=True)

@pytest.fixture()
def client():
    with TestClient(app) as c: yield c

@pytest.fixture()
def auth(client):
    response=client.post('/api/v1/auth/login',json={'email':os.environ['DEFAULT_ADMIN_EMAIL'],'password':os.environ['DEFAULT_ADMIN_PASSWORD']})
    assert response.status_code==200
    return {'Authorization':'Bearer '+response.json()['access_token']}
