from datetime import UTC, datetime, timedelta

from app.models import ApiToken, ApiUsageBucket
from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def enforce_api_token_rate_limit(
    db: Session,
    token: ApiToken,
    now: datetime | None = None,
) -> None:
    """Consume one fixed-window request without allowing count overshoot."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    bucket_start = current.replace(second=0, microsecond=0)
    limit = max(token.rate_limit_per_minute, 1)
    updated = db.scalar(
        update(ApiUsageBucket)
        .where(
            ApiUsageBucket.token_id == token.id,
            ApiUsageBucket.bucket_start == bucket_start,
            ApiUsageBucket.request_count < limit,
        )
        .values(request_count=ApiUsageBucket.request_count + 1)
        .returning(ApiUsageBucket.id)
    )
    if updated is not None:
        return

    try:
        with db.begin_nested():
            bucket = ApiUsageBucket(
                token_id=token.id,
                bucket_start=bucket_start,
                request_count=1,
            )
            db.add(bucket)
            db.flush()
        return
    except IntegrityError:
        # Another request created the same minute bucket. Retry the guarded increment.
        updated = db.scalar(
            update(ApiUsageBucket)
            .where(
                ApiUsageBucket.token_id == token.id,
                ApiUsageBucket.bucket_start == bucket_start,
                ApiUsageBucket.request_count < limit,
            )
            .values(request_count=ApiUsageBucket.request_count + 1)
            .returning(ApiUsageBucket.id)
        )
        if updated is not None:
            return

    retry_after = max(1, int((bucket_start + timedelta(minutes=1) - current).total_seconds()))
    raise HTTPException(
        status_code=429,
        detail="API token rate limit exceeded",
        headers={"Retry-After": str(retry_after)},
    )
