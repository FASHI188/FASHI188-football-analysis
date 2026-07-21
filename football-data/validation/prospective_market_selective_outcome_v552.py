#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
ENGINE = ROOT / "engine"
for path in (VALIDATION, ENGINE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from platform_core import canonical_json_bytes, sha256_bytes
from prospective_market_consensus_v554 import validate_consensus
from prospective_market_consensus_shadow_v555 import evaluate_selective as evaluate_consensus_selective
from prospective_market_selective_shadow_v526 import evaluate as evaluate_single_selective
from prospective_market_snapshot_v523 import validate as validate_snapshot

CONFIG = ROOT / "config" / "prospective_market_selective_challenger_v526.json"


def _sha(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def _formal_probs(value: Any) -> dict[str, float]:
    if isinstance(value, dict) and "one_x_two" in value:
        value = value["one_x_two"]
    if not isinstance(value, dict):
        raise ValueError("formal 1X2 must be an object")
    probs = {k: float(value[k]) for k in ("home", "draw", "away")}
    if any((not math.isfinite(v) or v < 0.0) for v in probs.values()):
        raise ValueError("formal 1X2 contains invalid probability")
    z = sum(probs.values())
    if z <= 0.0:
        raise ValueError("formal 1X2 probability sum is zero")
    return {k: v / z for k, v in probs.items()}


def _actual(value: str) -> str:
    token = str(value).strip().lower()
    aliases = {"h": "home", "d": "draw", "a": "away", "home": "home", "draw": "draw", "away": "away"}
    if token not in aliases:
        raise ValueError(f"unsupported actual outcome: {value}")
    return aliases[token]


def _brier(prob: dict[str, float], actual: str) -> float:
    return sum((prob[k] - (1.0 if k == actual else 0.0)) ** 2 for k in ("home", "draw", "away"))


def _rps(prob: dict[str, float], actual: str) -> float:
    order = ["away", "draw", "home"]
    actual_index = order.index(actual)
    cp = co = score = 0.0
    for idx in range(2):
        cp += prob[order[idx]]
        co += 1.0 if actual_index == idx else 0.0
        score += (cp - co) ** 2
    return score / 2.0


def score(market_input: dict[str, Any], formal_one_x_two: Any, actual_outcome: str) -> dict[str, Any]:
    formal = _formal_probs(formal_one_x_two)
    actual = _actual(actual_outcome)
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    config_sha = _sha(config)
    is_consensus = market_input.get("schema_version") == "V5.5.4-prospective-market-consensus-r1"
    if is_consensus:
        validation = validate_consensus(market_input)
        shadow = evaluate_consensus_selective(market_input)
        market_input_kind = "INDEPENDENT_PROVIDER_CONSENSUS"
        promotion_eligible = bool(validation.get("passed")) and bool(market_input.get("promotion_evidence_eligible"))
        freeze_utc = market_input.get("consensus_observed_at_utc")
        market_hash = market_input.get("consensus_sha256")
        provider_count = market_input.get("provider_count")
    else:
        validation = validate_snapshot(market_input)
        shadow = evaluate_single_selective(market_input)
        market_input_kind = "SINGLE_PROVIDER_SNAPSHOT_DIAGNOSTIC"
        promotion_eligible = False
        freeze_utc = market_input.get("freeze_utc")
        market_hash = market_input.get("raw_snapshot_sha256")
        provider_count = 1

    cid = str(market_input.get("competition_id") or "")
    formal_pick = max(("home", "draw", "away"), key=lambda k: formal[k])
    market_prob = shadow.get("de_vigged_1x2")
    selected = shadow.get("shadow_status") == "SHADOW_MARKET_HIGH_CONFIDENCE_DIRECTION"
    result = {
        "schema_version": "V5.5.5-prospective-market-selective-outcome-r2",
        "evaluated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "competition_id": cid,
        "season": str(market_input.get("season") or ""),
        "home_team": str(market_input.get("home_team") or ""),
        "away_team": str(market_input.get("away_team") or ""),
        "kickoff_utc": market_input.get("kickoff_utc"),
        "freeze_utc": freeze_utc,
        "match_key": f"{cid}|{market_input.get('season')}|{market_input.get('kickoff_utc')}|{market_input.get('home_team')}|{market_input.get('away_team')}",
        "market_input_kind": market_input_kind,
        "market_input_sha256": market_hash,
        "provider_count": provider_count,
        "promotion_evidence_eligible": promotion_eligible,
        "config_sha256": config_sha,
        "market_input_validation_passed": bool(validation.get("passed")),
        "market_input_errors": validation.get("errors") or [],
        "shadow_status": shadow.get("shadow_status"),
        "selected_by_shadow_gate": selected,
        "actual_outcome": actual,
        "formal_one_x_two": formal,
        "formal_direction": formal_pick,
        "formal_direction_correct": 1 if formal_pick == actual else 0,
        "formal_brier": _brier(formal, actual),
        "formal_rps": _rps(formal, actual),
        "formal_direction_override": False,
        "probability_mutation": False,
        "formal_weight": 0,
    }
    if not validation.get("passed"):
        result["status"] = "INVALID_MARKET_INPUT_FAIL_CLOSED"
        return result
    if not shadow.get("registered_candidate_domain"):
        result["status"] = "DOMAIN_NOT_REGISTERED_FOR_SELECTIVE_VALIDATION"
        return result
    if not isinstance(market_prob, dict):
        result["status"] = "MARKET_PROBABILITY_MISSING_FAIL_CLOSED"
        return result
    market = {k: float(market_prob[k]) for k in ("home", "draw", "away")}
    market_pick = max(("home", "draw", "away"), key=lambda k: market[k])
    result.update({
        "status": "SCORED_SELECTIVE_ROW",
        "market_one_x_two": market,
        "market_direction": market_pick,
        "market_direction_correct": 1 if market_pick == actual else 0,
        "market_brier": _brier(market, actual),
        "market_rps": _rps(market, actual),
        "market_gap": shadow.get("market_top1_top2_gap"),
        "registered_gap_threshold": shadow.get("registered_gap_threshold"),
        "timing_robust_point_gate": shadow.get("timing_robust_point_gate"),
    })
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("market_input")
    parser.add_argument("formal_one_x_two")
    parser.add_argument("actual_outcome")
    parser.add_argument("--out")
    args = parser.parse_args()
    payload = score(
        json.loads(Path(args.market_input).read_text(encoding="utf-8")),
        json.loads(Path(args.formal_one_x_two).read_text(encoding="utf-8")),
        args.actual_outcome,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if payload.get("status") == "SCORED_SELECTIVE_ROW" else 2


if __name__ == "__main__":
    raise SystemExit(main())
