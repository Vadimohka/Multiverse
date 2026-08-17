import subprocess
import sys
from pathlib import Path

from app.services.belarus_market_smoke import summarize_profile


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


def test_smoke_cli_refuses_network_without_explicit_live_flag():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([sys.executable, str(root / "scripts" / "smoke_belarus_market_pack.py")], cwd=root, capture_output=True, text=True)

    assert result.returncode != 0
    assert "pass --live" in result.stderr
