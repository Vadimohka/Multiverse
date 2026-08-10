import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.bootstrap import seed
from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.errors import AppError
from app.routers import (
    api_tokens,
    auth,
    data,
    documents,
    projects,
    prompts,
    review,
    runs,
    schemas,
    sources,
    system,
    users,
    workflow_templates,
    workflows,
)
from app.routers import settings as settings_router

settings = get_settings()
logger = logging.getLogger("parser_studio.requests")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db: seed(db)
    yield


app = FastAPI(title="Parser Studio API", version="0.1.0", openapi_url="/api/v1/openapi.json", docs_url="/api/docs", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(json.dumps({"event": "request_failed", "request_id": request_id, "method": request.method, "path": request.url.path}, ensure_ascii=False))
        raise
    response.headers["X-Request-ID"] = request_id
    logger.info(json.dumps({"event": "request", "request_id": request_id, "method": request.method, "path": request.url.path, "status": response.status_code, "duration_ms": int((time.perf_counter()-started)*1000)}, ensure_ascii=False))
    return response


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=422, content=exc.as_dict())


api_prefix = "/api/v1"
for router in [auth.router, api_tokens.router, users.router, projects.router, sources.router, workflow_templates.router, workflows.router, runs.router, schemas.router, prompts.router, review.router, data.router, documents.router, settings_router.router, system.router]:
    app.include_router(router, prefix=api_prefix)


@app.get("/")
def root() -> dict:
    return {"name": "Parser Studio", "docs": "/api/docs", "health": "/api/v1/health"}
