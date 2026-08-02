ARG PYTHON_VERSION=3.14.6
FROM python:${PYTHON_VERSION}-slim-bookworm
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/apps/api:/app/packages
RUN python -c "import sys; assert sys.version_info[:2] == (3, 14), sys.version"
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libxml2 libxslt1.1 curl && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt && python -m pip check
# Source Profiler and the visual selector picker are synchronous UI endpoints.
# They need the same Chromium runtime as the browser worker, otherwise they
# silently degrade to a static HTML fallback in a Compose deployment.
RUN python -m playwright install --with-deps chromium
COPY alembic.ini .
COPY migrations migrations
COPY apps/api apps/api
COPY packages packages
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --app-dir apps/api --host 0.0.0.0 --port 8000"]
