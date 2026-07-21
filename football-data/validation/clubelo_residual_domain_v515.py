#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

import clubelo_residual_oof_v515 as core


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--competition", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        report = core.validate_domain(args.competition)
    except Exception as exc:
        report = {
            "schema_version": "V5.1.5-clubelo-residual-oof-domain-execution-r1",
            "competition_id": args.competition,
            "status": "EXECUTION_FAILURE_KEEP_FORMAL_WEIGHT_0",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback_tail": traceback.format_exc().splitlines()[-20:],
            "formal_weight": 0,
            "probability_change": False,
            "automatic_promotion": False
        }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "competition_id": args.competition,
        "status": report.get("status"),
        "forward_prediction_count": report.get("forward_prediction_count"),
        "error": report.get("error")
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
