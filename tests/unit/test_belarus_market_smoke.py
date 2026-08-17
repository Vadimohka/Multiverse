import subprocess
import sys
from pathlib import Path

import httpx
from app.services import belarus_market_smoke
from app.services.belarus_market_smoke import run_smoke, summarize_profile


def test_smoke_summary_reports_a_meaningful_public_representation_without_verifying_it():
    result = summarize_profile("ul-20", {
        "http_status": 200,
        "recommended_fetch_mode": "HTTP",
        "static_text_length": 2400,
        "captcha_detected": False,
        "login_forms": 0,
    })

    assert result == {
        "source_key": "ul-20", "transport": "HTTP", "status": "PASS",
        "reason": "PUBLIC_REPRESENTATION", "verified": False,
    }


def test_smoke_summary_never_treats_login_or_captcha_as_a_pass():
    result = summarize_profile("blocked", {"http_status": 200, "captcha_detected": True, "login_forms": 1})

    assert result["status"] == "BLOCKED"
    assert result["reason"] == "LOGIN_OR_CAPTCHA"
    assert result["verified"] is False


def test_smoke_requires_live_flag_before_network(monkeypatch):
    """Removing the live gate must make this test fail before a request occurs."""

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("network profile must not run without --live")

    monkeypatch.setattr(httpx.AsyncClient, "get", fail_if_called)
    monkeypatch.setattr(belarus_market_smoke, "profile_url", fail_if_called)

    results = run_smoke([])

    assert results
    assert all(item["result"] == "SKIPPED_REQUIRES_LIVE" for item in results)
    assert all(item["status_after"] == item["status_before"] for item in results)


def test_live_smoke_never_promotes_source(monkeypatch):
    """Changing a report row into a promotion must fail this status assertion."""

    async def public_profile(_url, timeout=20):
        assert timeout == 20
        return {
            "http_status": 200,
            "recommended_fetch_mode": "HTTP",
            "static_text_length": 2400,
            "captcha_detected": False,
            "login_forms": 0,
        }

    monkeypatch.setattr(belarus_market_smoke, "profile_url", public_profile)

    result = run_smoke(["--live", "--source-key", "news-08"])

    assert result == [{
        "source_key": "news-08",
        "dataset": "market-news",
        "fixture": "news-08-list.html, news-08-detail.html, news-08-page-1.html, news-08-page-2.html",
        "transport": "HTTP",
        "result": "PASS",
        "reason": "PUBLIC_REPRESENTATION",
        "status_before": "DRAFT",
        "status_after": "DRAFT",
    }]


def test_smoke_cli_reports_skipped_rows_without_live_flag():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([sys.executable, str(root / "scripts" / "smoke_belarus_market_pack.py")], cwd=root, capture_output=True, text=True)

    assert result.returncode == 0
    assert '"result": "SKIPPED_REQUIRES_LIVE"' in result.stdout
