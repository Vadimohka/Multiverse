from uuid import uuid4


def assert_error(response, *, status: int, code: str, request_id: str) -> None:
    assert response.status_code == status, response.text
    assert response.headers["X-Request-ID"] == request_id
    payload = response.json()
    assert payload["error"]["code"] == code
    assert payload["error"]["message"]
    assert payload["error"]["request_id"] == request_id
    assert "details" in payload["error"]
    assert payload["detail"]


def test_machine_api_errors_have_stable_envelope(client, auth):
    project = client.get("/api/v1/projects", headers=auth).json()[0]
    suffix = uuid4().hex[:10]
    dataset = client.post(
        "/api/v1/datasets",
        headers=auth,
        json={
            "project_id": project["id"],
            "name": f"Error contract {suffix}",
            "slug": f"error-contract-{suffix}",
            "natural_key_fields": ["external_id"],
        },
    ).json()
    cases = [
        (
            f"/api/v1/datasets/{uuid4()}/records",
            404,
            "NOT_FOUND",
        ),
        (
            "/api/v1/datasets/missing/records?view=unknown",
            422,
            "VALIDATION_ERROR",
        ),
        (
            f"/api/v1/datasets/{dataset['id']}/records?cursor=not-a-cursor",
            400,
            "INVALID_CURSOR",
        ),
    ]
    for index, (url, status, code) in enumerate(cases):
        request_id = f"contract-{index}"
        response = client.get(url, headers={**auth, "X-Request-ID": request_id})
        assert_error(response, status=status, code=code, request_id=request_id)


def test_scoped_token_rate_limit_is_enforced_per_minute(client, auth):
    project = client.get("/api/v1/projects", headers=auth).json()[0]
    suffix = uuid4().hex[:10]
    dataset = client.post(
        "/api/v1/datasets",
        headers=auth,
        json={
            "project_id": project["id"],
            "name": f"Rate limited {suffix}",
            "slug": f"rate-limited-{suffix}",
            "natural_key_fields": ["external_id"],
        },
    ).json()
    created = client.post(
        "/api/v1/api-tokens",
        headers=auth,
        json={
            "name": "Limited agent",
            "scopes": ["datasets:read"],
            "dataset_ids": [dataset["id"]],
            "rate_limit_per_minute": 2,
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["rate_limit_per_minute"] == 2
    token_auth = {"Authorization": f"Bearer {created.json()['token']}"}
    url = f"/api/v1/datasets/{dataset['id']}/records"

    assert client.get(url, headers=token_auth).status_code == 200
    assert client.get(url, headers=token_auth).status_code == 200
    limited = client.get(url, headers={**token_auth, "X-Request-ID": "limited-request"})

    assert_error(limited, status=429, code="RATE_LIMITED", request_id="limited-request")
    assert int(limited.headers["Retry-After"]) >= 1


def test_revoked_and_expired_machine_tokens_are_rejected(client, auth):
    project = client.get("/api/v1/projects", headers=auth).json()[0]
    suffix = uuid4().hex[:10]
    dataset = client.post(
        "/api/v1/datasets",
        headers=auth,
        json={
            "project_id": project["id"],
            "name": f"Token lifecycle {suffix}",
            "slug": f"token-lifecycle-{suffix}",
            "natural_key_fields": ["external_id"],
        },
    ).json()

    def create_token(name: str, expires_at: str | None = None) -> dict:
        response = client.post(
            "/api/v1/api-tokens",
            headers=auth,
            json={
                "name": name,
                "dataset_ids": [dataset["id"]],
                "expires_at": expires_at,
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    revoked = create_token("Revoked")
    assert client.delete(f"/api/v1/api-tokens/{revoked['id']}", headers=auth).status_code == 204
    revoked_response = client.get(
        f"/api/v1/datasets/{dataset['id']}/records",
        headers={"Authorization": f"Bearer {revoked['token']}", "X-Request-ID": "revoked"},
    )
    assert_error(
        revoked_response,
        status=401,
        code="AUTHENTICATION_REQUIRED",
        request_id="revoked",
    )

    expired = create_token("Expired", "2026-08-10T00:00:00Z")
    expired_response = client.get(
        f"/api/v1/datasets/{dataset['id']}/records",
        headers={"Authorization": f"Bearer {expired['token']}", "X-Request-ID": "expired"},
    )
    assert_error(
        expired_response,
        status=401,
        code="AUTHENTICATION_REQUIRED",
        request_id="expired",
    )
