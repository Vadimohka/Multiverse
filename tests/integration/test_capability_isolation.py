"""Regression coverage for Delivery 0C scoped capability resolution/redaction."""

from uuid import uuid4


def create_user_and_auth(client, admin_auth, role: str = "DEVELOPER") -> tuple[dict[str, str], dict]:
    email = f"capability-{role.lower()}-{uuid4().hex[:12]}@example.test"
    created = client.post(
        "/api/v1/users",
        headers=admin_auth,
        json={"email": email, "password": "StrongPass123!", "roles": [role]},
    )
    assert created.status_code == 201, created.text
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "StrongPass123!"})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}, created.json()


def create_project(client, auth, suffix: str) -> dict:
    response = client.post(
        "/api/v1/projects",
        headers=auth,
        json={"name": f"Capabilities {suffix}", "slug": f"capabilities-{suffix}", "description": ""},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_capabilities_are_project_scoped_and_graph_secrets_cannot_exfiltrate(client, auth):
    owner_auth, _ = create_user_and_auth(client, auth)
    first = create_project(client, owner_auth, uuid4().hex[:10])
    second = create_project(client, owner_auth, uuid4().hex[:10])

    secret = client.post(
        "/api/v1/secrets",
        headers=auth,
        json={"project_id": first["id"], "name": "PRIVATE_TOKEN", "value": "top-secret-token"},
    )
    assert secret.status_code == 201, secret.text
    assert client.get(f"/api/v1/secrets?project_id={second['id']}", headers=auth).json() == []

    profile = client.post(
        "/api/v1/browser-profiles",
        headers=auth,
        json={"project_id": first["id"], "name": "private browser", "storage_state": '{"cookies":[{"value":"top-secret-token"}]}'},
    )
    assert profile.status_code == 201, profile.text
    foreign_source = client.post(
        "/api/v1/sources",
        headers=owner_auth,
        json={
            "project_id": second["id"],
            "name": "invalid profile binding",
            "entry_url": "https://example.com",
            "settings": {"browser_profile_id": profile.json()["id"]},
        },
    )
    assert foreign_source.status_code == 422, foreign_source.text

    unsafe_graph = {
        "version": 1,
        "settings": {},
        "nodes": [{"id": "output", "type": "output", "config": {"name": "{{secret.PRIVATE_TOKEN}}"}}],
        "edges": [],
    }
    workflow = client.post(
        "/api/v1/workflows",
        headers=auth,
        json={"project_id": first["id"], "name": "secret exfiltration", "graph_json": unsafe_graph},
    )
    assert workflow.status_code == 422, workflow.text


def test_node_test_and_persisted_diagnostics_redact_resolved_secrets(client, auth):
    project = create_project(client, auth, uuid4().hex[:10])
    source = client.post(
        "/api/v1/sources",
        headers=auth,
        json={"project_id": project["id"], "name": "secret source", "entry_url": "https://example.com"},
    ).json()
    secret_value = "top-secret-token"
    assert client.post(
        "/api/v1/secrets",
        headers=auth,
        json={"project_id": project["id"], "name": "AUTH_TOKEN", "value": secret_value},
    ).status_code == 201
    graph = {
        "version": 1,
        "settings": {},
        "nodes": [
            {"id": "trigger", "type": "manual_trigger", "config": {}},
            {
                "id": "fetch",
                "type": "http_request",
                "config": {
                    "url": "https://example.com",
                    "headers": {"Authorization": "Bearer {{secret.AUTH_TOKEN}}"},
                },
            },
        ],
        "edges": [{"source": "trigger", "target": "fetch"}],
    }
    # Success or network failure, a node-test response must never disclose a
    # resolved credential in output, logs or diagnostics.
    response = client.post(
        "/api/v1/workflows/node-test",
        headers=auth,
        json={"node_type": "http_request", "config": graph["nodes"][1]["config"], "graph": graph, "target_node_id": "fetch", "source_id": source["id"]},
    )
    assert response.status_code in {200, 500}
    assert secret_value not in response.text
