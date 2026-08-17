"""Report-only anonymous-public smoke checks for Belarus Market sources."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.services.belarus_market_pack import DATASETS, PassportSource, passport_sources
from app.services.source_profiler import profile_url


def summarize_profile(source_key: str, profile: dict[str, Any]) -> dict[str, Any]:
    """Classify a profiler result without mutating any preset status."""

    if profile.get("captcha_detected") or int(profile.get("login_forms") or 0) > 0:
        return {"source_key": source_key, "transport": str(profile.get("recommended_fetch_mode") or "HTTP"), "status": "BLOCKED", "reason": "LOGIN_OR_CAPTCHA", "verified": False}
    if int(profile.get("http_status") or 0) not in range(200, 300):
        return {"source_key": source_key, "transport": str(profile.get("recommended_fetch_mode") or "HTTP"), "status": "FAIL", "reason": "HTTP_UNAVAILABLE", "verified": False}
    meaningful = bool(profile.get("json_detected") or profile.get("document_type") or int(profile.get("static_text_length") or 0) >= 300 or int(profile.get("rendered_text_length") or 0) >= 300)
    return {"source_key": source_key, "transport": str(profile.get("recommended_fetch_mode") or "HTTP"), "status": "PASS" if meaningful else "PARTIAL", "reason": "PUBLIC_REPRESENTATION" if meaningful else "INSUFFICIENT_PUBLIC_CONTENT", "verified": False}


def _fixture_summary(source: PassportSource) -> str:
    return ", ".join(Path(reference).name for reference in source.fixture_refs) or "none"


def _readiness_row(source: PassportSource, *, transport: str, result: str, reason: str) -> dict[str, Any]:
    """Return report data only; a smoke result is never a status transition."""

    return {
        "source_key": source.key,
        "dataset": DATASETS[source.dataset_group][0],
        "fixture": _fixture_summary(source),
        "transport": transport,
        "result": result,
        "reason": reason,
        "status_before": source.status,
        "status_after": source.status,
    }


def _selected_sources(keys: set[str] | None) -> list[PassportSource]:
    sources = passport_sources()
    if not keys:
        return sources
    known_keys = {source.key for source in sources}
    unknown_keys = sorted(keys - known_keys)
    if unknown_keys:
        label = "key" if len(unknown_keys) == 1 else "keys"
        raise ValueError(f"Unknown source {label}: {', '.join(unknown_keys)}")
    return [source for source in sources if source.key in keys]


async def smoke_sources(
    keys: set[str] | None = None, timeout: float = 20, *, live: bool = False
) -> list[dict[str, Any]]:
    """Return selected readiness rows, reaching public URLs only with ``live=True``.

    The profiler has no credentials or authenticated session. This function
    has no persistence dependencies, so it cannot update the verification
    registry or enable a schedule.
    """

    selected = _selected_sources(keys)
    if not live:
        return [
            _readiness_row(
                source,
                transport="HTTP",
                result="SKIPPED_REQUIRES_LIVE",
                reason="operator live smoke required",
            )
            for source in selected
        ]

    results: list[dict[str, Any]] = []
    for source in selected:
        try:
            profile = await profile_url(source.url, timeout=timeout)
            summary = summarize_profile(source.key, profile)
            result = _readiness_row(
                source,
                transport=summary["transport"],
                result=summary["status"],
                reason=summary["reason"],
            )
        except Exception as exc:
            result = _readiness_row(
                source,
                transport="HTTP",
                result="FAIL",
                reason=f"PROFILE_ERROR:{str(exc)[:160]}",
            )
        results.append(result)
    return results


def _parse_args(args: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="required acknowledgement before any anonymous-public network request",
    )
    parser.add_argument("--source-key", action="append", default=[], help="source key; may be repeated")
    parser.add_argument("--timeout", type=float, default=20)
    return parser.parse_args(args)


def run_smoke(args: Sequence[str] | None = None) -> list[dict[str, Any]]:
    """Parse report options and return readiness rows without changing source state."""

    options = _parse_args([] if args is None else args)
    keys = {key.lower() for key in options.source_key} or None
    return asyncio.run(smoke_sources(keys, options.timeout, live=options.live))
