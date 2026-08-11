from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

DEFAULT_RETRY_STATUSES = (408, 425, 429, 500, 502, 503, 504)


@dataclass(frozen=True)
class FetchAttempt:
    attempt: int
    status_code: int | None
    error: str | None
    delay_seconds: float = 0.0


@dataclass(frozen=True)
class FetchPolicy:
    timeout: float = 30.0
    retries: int = 2
    backoff_seconds: float = 0.5
    retry_statuses: tuple[int, ...] = DEFAULT_RETRY_STATUSES
    respect_retry_after: bool = True

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> FetchPolicy:
        timeout = float(
            config.get("request_timeout", config.get("timeout", config.get("listing_timeout", 30)))
        )
        retries = int(config.get("request_retries", config.get("retries", 2)))
        backoff = float(config.get("retry_backoff_seconds", 0.5))
        raw_statuses = config.get("retry_statuses", DEFAULT_RETRY_STATUSES)
        statuses = tuple(int(value) for value in raw_statuses)
        if not 0 < timeout <= 300:
            raise ValueError("request_timeout must be between 0 and 300 seconds")
        if not 0 <= retries <= 10:
            raise ValueError("request_retries must be between 0 and 10")
        if not 0 <= backoff <= 60:
            raise ValueError("retry_backoff_seconds must be between 0 and 60")
        if any(status < 100 or status > 599 for status in statuses):
            raise ValueError("retry_statuses must contain HTTP status codes")
        return cls(
            timeout=timeout,
            retries=retries,
            backoff_seconds=backoff,
            retry_statuses=statuses,
            respect_retry_after=bool(config.get("respect_retry_after", True)),
        )


def retry_after_seconds(
    value: str | None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            return max(0.0, (retry_at.astimezone(UTC) - now().astimezone(UTC)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


async def request_with_policy(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    policy: FetchPolicy,
    *,
    delay: Callable[[float], Awaitable[None]] = asyncio.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    **kwargs: Any,
) -> httpx.Response:
    attempts: list[FetchAttempt] = []
    backoff_attempt = 0
    request_options = {key: value for key, value in kwargs.items() if value is not None}
    for attempt_number in range(1, policy.retries + 2):
        try:
            method_handler = getattr(client, method.lower(), None)
            if method_handler is None:
                response = await client.request(method, url, timeout=policy.timeout, **request_options)
            else:
                parameters = inspect.signature(method_handler).parameters.values()
                supports_timeout = any(
                    parameter.name == "timeout" or parameter.kind == inspect.Parameter.VAR_KEYWORD
                    for parameter in parameters
                )
                request_kwargs = {**request_options}
                if supports_timeout:
                    request_kwargs["timeout"] = policy.timeout
                response = await method_handler(url, **request_kwargs)
        except httpx.HTTPError as exc:
            if attempt_number > policy.retries:
                attempts.append(FetchAttempt(attempt_number, None, str(exc)))
                exc.fetch_attempts = [asdict(item) for item in attempts]
                raise
            wait = policy.backoff_seconds * (2**backoff_attempt)
            backoff_attempt += 1
            attempts.append(FetchAttempt(attempt_number, None, str(exc), wait))
            await delay(wait)
            continue

        status_code = int(getattr(response, "status_code", 200))
        retryable = status_code in policy.retry_statuses
        if not retryable or attempt_number > policy.retries:
            attempts.append(FetchAttempt(attempt_number, status_code, None))
            if not hasattr(response, "extensions"):
                response.extensions = {}
            response.extensions["fetch_attempts"] = [asdict(item) for item in attempts]
            return response

        wait = None
        if policy.respect_retry_after:
            wait = retry_after_seconds(response.headers.get("Retry-After"), now)
        if wait is None:
            wait = policy.backoff_seconds * (2**backoff_attempt)
            backoff_attempt += 1
        attempts.append(FetchAttempt(attempt_number, status_code, None, wait))
        await response.aclose()
        await delay(wait)

    raise RuntimeError("unreachable fetch policy state")


def fetch_attempts(response: httpx.Response) -> list[dict[str, Any]]:
    attempts = response.extensions.get("fetch_attempts", [])
    return list(attempts) if isinstance(attempts, list) else []
