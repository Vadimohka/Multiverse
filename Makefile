.PHONY: env dev up down test lint format migrate frontend-lint frontend-build check min-build min-up min-down min-logs min-ps min-scheduler min-browser

env:
	@test -f .env || (cp .env.example .env && \
		python3 -c "from pathlib import Path; import secrets; p=Path('.env'); s=p.read_text(); s=s.replace('replace-with-a-long-random-value-before-production', secrets.token_urlsafe(48)).replace('replace-with-a-different-long-random-value-before-production', secrets.token_urlsafe(48)).replace('replace-with-a-strong-postgres-password', secrets.token_urlsafe(32)).replace('replace-with-a-strong-minio-password', secrets.token_urlsafe(32)).replace('ChangeThisDemoAdminPassword123!', secrets.token_urlsafe(24)); p.write_text(s)"; echo '.env created from .env.example')
	@test -f .env && echo '.env is ready'

dev:
	uvicorn app.main:app --app-dir apps/api --reload --port 8000

up: env
	docker compose up --build

down:
	docker compose down

test:
	pytest

lint:
	ruff check apps packages tests

format:
	ruff format apps packages tests

migrate:
	alembic upgrade head

frontend-build:
	cd apps/frontend && npm run build

frontend-lint:
	cd apps/frontend && npm run lint

check: test lint frontend-lint frontend-build

min-build: env
	COMPOSE_PARALLEL_LIMIT=1 docker compose -f docker-compose.min.yml build api
	COMPOSE_PARALLEL_LIMIT=1 docker compose -f docker-compose.min.yml build frontend

min-up: env
	docker compose -f docker-compose.min.yml up -d postgres redis
	docker compose -f docker-compose.min.yml up -d api
	docker compose -f docker-compose.min.yml up -d worker frontend

min-down:
	docker compose -f docker-compose.min.yml down

min-logs:
	docker compose -f docker-compose.min.yml logs -f --tail=150

min-ps:
	docker compose -f docker-compose.min.yml ps

min-scheduler:
	docker compose -f docker-compose.min.yml --profile scheduler up -d celery-beat

min-browser:
	COMPOSE_PARALLEL_LIMIT=1 docker compose -f docker-compose.min.yml --profile browser up -d --build worker-browser
