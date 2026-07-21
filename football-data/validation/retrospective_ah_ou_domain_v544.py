#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

import retrospective_ah_ou_ceiling_v528 as base

ALLOWED = {"POR_PrimeiraLiga", "SCO_Premiership"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--competition", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        if args.competition not in ALLOWED:
            raise RuntimeError(f"competition not allowed: {args.competition}")
        report = base.audit_domain(args.competition)
        payload = {
            "schema_version": "V5.4.4-retrospective-ah-ou-domain-r1",
            "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "competition_id": args.competition,
            "status": "PASS",
            "report": report,
            "formal_weight_change": False,
            "probability_change": False,
            "formal_pit_market_eligible": False
        }
    except Exception as exc:
        payload = {
            "schema_version": "V5.4.4-retrospective-ah-ou-domain-r1",
            "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "competition_id": args.competition,
            "status": "EXECUTION_FAILURE",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback_tail": traceback.format_exc().splitlines()[-30:],
            "formal_weight_change": False,
            "probability_change": False,
            "formal_pit_market_eligible": False
        }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: payload.get(k) for k in ("competition_id", "status", "error")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
