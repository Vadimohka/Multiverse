def test_schedule_can_be_enabled_and_retimed_without_recreating_it(client, auth):
    project = client.get("/api/v1/projects", headers=auth).json()[0]
    workflow = client.post(
        "/api/v1/workflows", headers=auth,
        json={"project_id": project["id"], "name": "Scheduled workflow", "graph_json": {"nodes": [], "edges": [], "settings": {}}},
    )
    assert workflow.status_code == 201, workflow.text
    created = client.post(
        "/api/v1/schedules", headers=auth,
        json={"workflow_id": workflow.json()["id"], "name": "Weekly market check", "cron": "0 8 * * 1", "timezone": "Europe/Minsk", "enabled": False},
    )
    assert created.status_code == 201, created.text

    updated = client.patch(
        f"/api/v1/schedules/{created.json()['id']}", headers=auth,
        json={"cron": "30 9 * * 1-5", "enabled": True},
    )

    assert updated.status_code == 200, updated.text
    assert updated.json()["cron"] == "30 9 * * 1-5"
    assert updated.json()["enabled"] is True
