#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

from prospective_market_snapshot_v523 import validate as validate_snapshot

CONFIG = ROOT / "config" / "prospective_market_selective_challenger_v526.json"


def _devig(one_x_two: dict) -> dict[str, float]:
    raw = {
        "home": 1.0 / float(one_x_two["home"]),
        "draw": 1.0 / float(one_x_two["draw"]),
        "away": 1.0 / float(one_x_two["away"]),
    }
    total = sum(raw.values())
    return {key: value / total for key, value in raw.items()}


def evaluate(snapshot: dict) -> dict:
    contract_result = validate_snapshot(snapshot)
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    competition_id = str(snapshot.get("competition_id") or "")
    candidate = (config.get("candidate_domains") or {}).get(competition_id)

    result = {
        "schema_version": "V5.2.6-prospective-market-selective-shadow-evaluation-r1",
        "evaluated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "competition_id": competition_id,
        "snapshot_contract_passed": bool(contract_result.get("passed")),
        "snapshot_errors": contract_result.get("errors") or [],
        "registered_candidate_domain": bool(candidate),
        "shadow_status": "NO_SHADOW_DIRECTION",
        "formal_direction_override": False,
        "probability_mutation": False,
        "formal_weight": 0,
    }

    if not contract_result.get("passed"):
        result["shadow_status"] = "SNAPSHOT_INVALID_FAIL_CLOSED"
        return result
    if not candidate:
        result["shadow_status"] = "DOMAIN_NOT_REGISTERED_FOR_MARKET_SELECTIVE_SHADOW"
        return result

    prob = _devig(snapshot["one_x_two"])
    ordered = sorted(prob.items(), key=lambda item: (item[1], item[0]), reverse=True)
    top1, top2 = ordered[0], ordered[1]
    gap = float(top1[1] - top2[1])
    threshold = float(candidate["gap_threshold"])
    result.update({
        "de_vigged_1x2": prob,
        "market_top1": top1[0],
        "market_top1_probability": top1[1],
        "market_top2": top2[0],
        "market_top2_probability": top2[1],
        "market_top1_top2_gap": gap,
        "registered_gap_threshold": threshold,
        "historical_reference_selected": candidate.get("retrospective_selected"),
        "historical_reference_accuracy": candidate.get("retrospective_accuracy"),
    })

    if gap >= threshold:
        result["shadow_status"] = "SHADOW_MARKET_HIGH_CONFIDENCE_DIRECTION"
        result["shadow_direction"] = top1[0]
    else:
        result["shadow_status"] = "SHADOW_GATE_NOT_MET"
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", help="Validated or raw V5.2.3 prospective market snapshot JSON")
    parser.add_argument("--out", help="Optional output JSON path")
    args = parser.parse_args()
    snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    result = evaluate(snapshot)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["snapshot_contract_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
