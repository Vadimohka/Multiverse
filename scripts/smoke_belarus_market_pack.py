"""Run anonymous-public smoke checks; this command never changes preset status."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps" / "api"), str(ROOT / "packages")]

from app.services.belarus_market_smoke import run_smoke


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="required acknowledgement before any network request")
    parser.add_argument("--source", action="append", default=[], help="source key; may be repeated")
    parser.add_argument("--timeout", type=float, default=20)
    args = parser.parse_args()
    if not args.live:
        raise SystemExit("Refusing network requests: pass --live. This command never logs in or changes VERIFIED status.")
    print(json.dumps(run_smoke(set(args.source) or None, args.timeout), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
