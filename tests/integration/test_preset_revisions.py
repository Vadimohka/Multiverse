from uuid import uuid4


def create_project(client, auth, suffix: str) -> dict:
    response = client.post(
        "/api/v1/projects",
        headers=auth,
        json={"name": f"Preset {suffix}", "slug": f"preset-{suffix}"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_blueprint_preset_revision_compiles_and_instantiates_a_v2_workflow(client, auth):
    project = create_project(client, auth, uuid4().hex[:10])
    blueprint = client.post(
        "/api/v1/presets/blueprints",
        headers=auth,
        json={"project_id": project["id"], "slug": "universal", "name": "Universal"},
    )
    assert blueprint.status_code == 201, blueprint.text
    preset = client.post(
        "/api/v1/presets/source",
        headers=auth,
        json={
            "project_id": project["id"],
            "blueprint_revision_id": blueprint.json()["id"],
            "slug": "news",
            "name": "News",
            "config_json": {
                "apiVersion": "multiverse.io/v2",
                "kind": "SourcePreset",
                "nodes": {"acquire": {"entry": "https://example.test/news"}},
            },
        },
    )
    assert preset.status_code == 201, preset.text

    preview = client.get(f"/api/v1/presets/source/{preset.json()['id']}/compile", headers=auth)
    assert preview.status_code == 200, preview.text
    assert [node["type"] for node in preview.json()["graph"]["nodes"]] == [
        "manual_trigger", "http_request", "crawl_links", "mapping", "transform", "validate", "output",
    ]

    workflow = client.post(
        f"/api/v1/presets/source/{preset.json()['id']}/instantiate",
        headers=auth,
        json={"name": "Compiled news"},
    )
    assert workflow.status_code == 201, workflow.text
    graph = workflow.json()["graph_json"]
    assert graph["contractVersion"] == 2
    assert graph["settings"]["presetRefs"]["sourcePresetRevisionId"] == preset.json()["id"]


def test_verified_preset_requires_a_fixture_reference(client, auth):
    project = create_project(client, auth, uuid4().hex[:10])
    blueprint = client.post(
        "/api/v1/presets/blueprints",
        headers=auth,
        json={"project_id": project["id"], "slug": "universal", "name": "Universal"},
    ).json()
    response = client.post(
        "/api/v1/presets/source",
        headers=auth,
        json={
            "project_id": project["id"],
            "blueprint_revision_id": blueprint["id"],
            "slug": "verified",
            "name": "Verified",
            "status": "VERIFIED",
            "config_json": {"nodes": {}},
        },
    )
    assert response.status_code == 422
    assert "fixture" in response.text.lower()
