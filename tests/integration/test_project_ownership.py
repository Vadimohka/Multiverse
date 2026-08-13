"""Regression matrix for Delivery 0A project/object authorization guards."""

from uuid import uuid4


def create_user_and_auth(client, admin_auth, role: str = "DEVELOPER") -> tuple[dict[str, str], dict]:
    email = f"project-{role.lower()}-{uuid4().hex[:12]}@example.test"
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
        json={"name": f"Owned {suffix}", "slug": f"owned-{suffix}", "description": ""},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_project_objects_are_not_enumerable_or_mutable_cross_project(client, auth):
    owner_auth, _ = create_user_and_auth(client, auth)
    outsider_auth, _ = create_user_and_auth(client, auth)
    project = create_project(client, owner_auth, uuid4().hex[:10])
    source = client.post(
        "/api/v1/sources",
        headers=owner_auth,
        json={"project_id": project["id"], "name": "Private source", "entry_url": "https://example.test"},
    ).json()
    graph = {"version": 1, "settings": {}, "nodes": [], "edges": []}
    workflow = client.post(
        "/api/v1/workflows",
        headers=owner_auth,
        json={"project_id": project["id"], "name": "Private workflow", "graph_json": graph},
    ).json()
    dataset = client.post(
        "/api/v1/datasets",
        headers=owner_auth,
        json={"project_id": project["id"], "name": "Private dataset", "slug": f"private-{uuid4().hex[:10]}"},
    ).json()

    assert project["id"] not in {row["id"] for row in client.get("/api/v1/projects", headers=outsider_auth).json()}
    assert client.get(f"/api/v1/sources?project_id={project['id']}", headers=outsider_auth).status_code == 404
    assert client.get(f"/api/v1/workflows/{workflow['id']}", headers=outsider_auth).status_code == 404
    assert client.patch(f"/api/v1/sources/{source['id']}", headers=outsider_auth, json={"name": "Stolen"}).status_code == 404
    assert client.delete(f"/api/v1/datasets/{dataset['id']}", headers=outsider_auth).status_code == 404


def test_owner_can_grant_project_access_without_cross_project_object_binding(client, auth):
    owner_auth, _ = create_user_and_auth(client, auth)
    member_auth, member = create_user_and_auth(client, auth)
    owner_project = create_project(client, owner_auth, uuid4().hex[:10])
    other_project = create_project(client, owner_auth, uuid4().hex[:10])
    grant = client.post(
        f"/api/v1/projects/{owner_project['id']}/members",
        headers=owner_auth,
        json={"user_id": member["id"], "role": "EDITOR"},
    )
    assert grant.status_code == 201, grant.text

    source = client.post(
        "/api/v1/sources",
        headers=member_auth,
        json={"project_id": owner_project["id"], "name": "Member source", "entry_url": "https://example.test"},
    )
    assert source.status_code == 201, source.text
    foreign_schema = client.post(
        "/api/v1/schemas",
        headers=owner_auth,
        json={"project_id": other_project["id"], "name": "Foreign", "schema_json": {"type": "object"}},
    ).json()
    assert foreign_schema["id"]
    invalid = client.post(
        "/api/v1/datasets",
        headers=member_auth,
        json={
            "project_id": owner_project["id"],
            "name": "Invalid binding",
            "slug": f"invalid-{uuid4().hex[:10]}",
            "schema_id": foreign_schema["id"],
        },
    )
    # The member cannot discover objects from another project; the endpoint
    # intentionally returns 404 rather than exposing the foreign schema ID.
    assert invalid.status_code == 404, invalid.text
