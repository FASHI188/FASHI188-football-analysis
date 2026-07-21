#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bayesian_dynamic_state_oof_v501_same_day_safe import validate_domain_same_day_safe
from platform_core import atomic_write_json

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "manifests" / "bayesian_dynamic_state_oof_v501"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--competition", required=True)
    args = parser.parse_args()
    report = validate_domain_same_day_safe(args.competition)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(OUT_DIR / f"{args.competition}.json", report)
    print(json.dumps({"competition_id": args.competition, "status": report["status"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
