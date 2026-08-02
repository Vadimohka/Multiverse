FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/apps/api:/app/apps/worker:/app/packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY apps/api apps/api
COPY apps/worker apps/worker
COPY packages packages
CMD ["celery", "-A", "worker:celery_app", "worker", "--loglevel=INFO", "-Q", "default,http,llm,exports,documents,maintenance"]
