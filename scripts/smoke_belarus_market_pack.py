"""Run anonymous-public smoke checks; this command never changes preset status."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps" / "api"), str(ROOT / "packages")]

from app.services.belarus_market_smoke import run_smoke  # noqa: E402


def main() -> None:
    print(json.dumps(run_smoke(sys.argv[1:]), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
