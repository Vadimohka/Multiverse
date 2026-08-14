"""Public-only smoke checks for declarative Belarus Market sources."""

from __future__ import annotations

import asyncio
from typing import Any

from app.services.belarus_market_pack import PassportSource, passport_sources
from app.services.source_profiler import profile_url


def summarize_profile(source_key: str, profile: dict[str, Any]) -> dict[str, Any]:
    """Classify a profiler result without mutating any preset status."""

    if profile.get("captcha_detected") or int(profile.get("login_forms") or 0) > 0:
        return {"source_key": source_key, "transport": str(profile.get("recommended_fetch_mode") or "HTTP"), "status": "BLOCKED", "reason": "LOGIN_OR_CAPTCHA", "verified": False}
    if int(profile.get("http_status") or 0) not in range(200, 300):
        return {"source_key": source_key, "transport": str(profile.get("recommended_fetch_mode") or "HTTP"), "status": "FAIL", "reason": "HTTP_UNAVAILABLE", "verified": False}
    meaningful = bool(profile.get("json_detected") or profile.get("document_type") or int(profile.get("static_text_length") or 0) >= 300 or int(profile.get("rendered_text_length") or 0) >= 300)
    return {"source_key": source_key, "transport": str(profile.get("recommended_fetch_mode") or "HTTP"), "status": "PASS" if meaningful else "PARTIAL", "reason": "PUBLIC_REPRESENTATION" if meaningful else "INSUFFICIENT_PUBLIC_CONTENT", "verified": False}


async def smoke_sources(keys: set[str] | None = None, timeout: float = 20) -> list[dict[str, Any]]:
    """Profile selected public sources sequentially and retain no session state."""

    selected: list[PassportSource] = [item for item in passport_sources() if not keys or item.key in keys]
    results: list[dict[str, Any]] = []
    for source in selected:
        try:
            profile = await profile_url(source.url, timeout=timeout)
            result = summarize_profile(source.key, profile)
            result["url"] = source.url
        except Exception as exc:
            result = {"source_key": source.key, "url": source.url, "transport": "HTTP", "status": "FAIL", "reason": f"PROFILE_ERROR:{str(exc)[:160]}", "verified": False}
        results.append(result)
    return results


def run_smoke(keys: set[str] | None = None, timeout: float = 20) -> list[dict[str, Any]]:
    return asyncio.run(smoke_sources(keys, timeout))
