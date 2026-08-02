# Runtime verification — 30 July 2026

## Verified live

- Docker Compose is running with all 10 declared services: API, frontend, PostgreSQL, Redis, MinIO, four Celery workers and Beat.
- API health endpoint returns HTTP 200.
- A real browser session was used to sign in, open the dashboard and all navigation sections. No route rendered the `Раздел не найден` placeholder or an API error.
- The workflow editor was opened for the seeded financial demo. DAG validation succeeded without creating an extra workflow version when the graph was unchanged.
- Sign-out returns to the login form and removes the application shell.

## Automated verification

- `python3 -m pytest -q`: 21 passed.
- `python3 scripts/smoke_test.py`: passed; the seven-node demo pipeline created three records, three review tasks, one artifact and a valid XLSX export.
- `cd apps/frontend && npm run build`: passed.

## Corrections made during verification

- Successful sign-in now redirects to the dashboard instead of leaving an authenticated user on `/login` and rendering the unknown-section placeholder.
- Sign-out updates React state as well as local storage.
- An unchanged workflow graph no longer creates an unnecessary new workflow version during Save, Validate, Publish or Run.
- Integration tests now use a test-only administrator password rather than a local deployment secret.
- Uploaded PDF, DOCX, XLSX, CSV and JSON files can now become persistent DOCUMENT sources with raw artifacts and a generated workflow.
- The selector picker now shows clickable, synchronised areas over a real Playwright screenshot; the API image contains Chromium so this works in Docker Compose.
- Data Explorer now requests confirmed dataset records in pages, and run details expose retry/cancel actions.

## Scope decision

This is a working single-tenant MVP, not full compliance with every item of the master specification. The known gaps remain: multi-tenant isolation, interactive selector overlay, noVNC browser authentication, standalone GraphQL builder, email trigger/output, visual join/group/fuzzy/SCD2 tools, and performance qualification for the stated large-scale targets.
