# Multiverse minimal Compose

This deployment mode is for servers with about 1 vCPU, 1 GiB RAM, and 20 GiB SSD. Add 2–3 GiB of swap before building or running containers; document conversion and browser automation can otherwise exhaust memory.

The default stack starts PostgreSQL, Redis, API, one single-process Celery worker, and frontend Nginx. It does not start MinIO, Celery Beat, Chromium, Docling, or dedicated per-queue workers. Artifacts are stored in a shared local Docker volume instead of S3.

The worker consumes `default`, `http`, `documents`, `llm`, `exports`, and `maintenance`. It intentionally does not consume `browser`; those jobs wait until the optional browser profile is running. PDF processing uses the basic `pypdf` fallback, while DOCX and XLSX remain supported by `python-docx` and `openpyxl`.

## Configure `.env`

Create the normal environment file and replace its generated development secrets before public use:

```bash
make env
```

Set the public URLs in `.env` for the deployment, for example:

```dotenv
PUBLIC_APP_URL=https://example.com
CORS_ORIGINS=https://example.com
```

`docker-compose.min.yml` sets `ARTIFACT_STORAGE_BACKEND=local` for API and workers, so no minimal-specific change to the standard `.env` is required. [env.min.example](env.min.example) is a reference for these production overrides. Keep `.env` private; never put passwords, keys, or other secrets in Compose files or documentation.

## Add swap

On a 1 GiB VPS, provision 2–3 GiB of swap according to your Linux distribution's documented procedure before continuing.

## Build sequentially

Do not run `docker compose up --build` on a 1 GiB server. Build one image at a time:

```bash
make min-build
```

The API builds `multiverse-backend:min`; the worker reuses that image and is not built again.

## Start sequentially

```bash
make min-up
make min-ps
curl -fsS http://127.0.0.1:8080/api/v1/health
docker stats --no-stream
```

The frontend is published only on `127.0.0.1:8080`. PostgreSQL, Redis, and the API have no host ports.

## Optional profiles

Start periodic scheduling only when it is needed:

```bash
make min-scheduler
```

Browser jobs need Chromium and can use swap on a small server. Start the separate single-process browser worker only when required:

```bash
make min-browser
```

## Stop and clean up

Stop the stack without deleting its data:

```bash
make min-down
```

Never run `docker compose -f docker-compose.min.yml down -v`: it deletes the PostgreSQL and artifact volumes.

After a successful build, safely reclaim only unused build cache and dangling images:

```bash
docker builder prune -f
docker image prune -f
docker system df
```
