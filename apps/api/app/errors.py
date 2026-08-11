from dataclasses import dataclass, field
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


@dataclass
class AppError(Exception):
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    retryable: bool = False
    node_id: str | None = None
    run_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
            "retryable": self.retryable,
            "node_id": self.node_id,
            "run_id": self.run_id,
        }


def api_error_code(status_code: int, detail: Any) -> str:
    if status_code == 400 and "cursor" in str(detail).lower():
        return "INVALID_CURSOR"
    return {
        400: "BAD_REQUEST",
        401: "AUTHENTICATION_REQUIRED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        422: "VALIDATION_ERROR",
        429: "RATE_LIMITED",
    }.get(status_code, "REQUEST_FAILED" if status_code < 500 else "INTERNAL_ERROR")


def error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    detail: Any,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) or request.headers.get(
        "X-Request-ID", ""
    )
    message = detail if isinstance(detail, str) else "Request validation failed"
    return JSONResponse(
        status_code=status_code,
        headers=headers,
        content={
            "detail": detail,
            "error": {
                "code": code,
                "message": message,
                "details": detail if isinstance(detail, (dict, list)) else {},
                "request_id": request_id,
            },
        },
    )


async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return error_response(
        request,
        status_code=exc.status_code,
        code=api_error_code(exc.status_code, exc.detail),
        detail=exc.detail,
        headers=exc.headers,
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return error_response(
        request,
        status_code=422,
        code="VALIDATION_ERROR",
        detail=exc.errors(),
    )
