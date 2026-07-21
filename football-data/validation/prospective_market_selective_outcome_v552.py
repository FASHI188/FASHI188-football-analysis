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
from prospective_market_selective_shadow_v526 import evaluate as evaluate_shadow
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
    probs = {k: v / z for k, v in probs.items()}
    if abs(sum(probs.values()) - 1.0) > 1e-12:
        raise ValueError("formal 1X2 normalization failed")
    return probs


def _actual(value: str) -> str:
    token = str(value).strip().lower()
    aliases = {"h": "home", "d": "draw", "a": "away", "home": "home", "draw": "draw", "away": "away"}
    if token not in aliases:
        raise ValueError(f"unsupported actual outcome: {value}")
    return aliases[token]


def _brier(prob: dict[str, float], actual: str) -> float:
    return sum((prob[k] - (1.0 if k == actual else 0.0)) ** 2 for k in ("home", "draw", "away"))


def _rps(prob: dict[str, float], actual: str) -> float:
    # Ordered away-draw-home, same orientation used by formal football audits.
    order = ["away", "draw", "home"]
    actual_index = order.index(actual)
    cp = 0.0
    co = 0.0
    score = 0.0
    for idx in range(2):
        cp += prob[order[idx]]
        co += 1.0 if actual_index == idx else 0.0
        score += (cp - co) ** 2
    return score / 2.0


def score(snapshot: dict[str, Any], formal_one_x_two: Any, actual_outcome: str) -> dict[str, Any]:
    validation = validate_snapshot(snapshot)
    formal = _formal_probs(formal_one_x_two)
    actual = _actual(actual_outcome)
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    config_sha = _sha(config)
    shadow = evaluate_shadow(snapshot)
    cid = str(snapshot.get("competition_id") or "")
    formal_pick = max(("home", "draw", "away"), key=lambda k: formal[k])
    market_prob = shadow.get("de_vigged_1x2")
    selected = shadow.get("shadow_status") == "SHADOW_MARKET_HIGH_CONFIDENCE_DIRECTION"

    result = {
        "schema_version": "V5.5.2-prospective-market-selective-outcome-r1",
        "evaluated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "competition_id": cid,
        "season": str(snapshot.get("season") or ""),
        "home_team": str(snapshot.get("home_team") or ""),
        "away_team": str(snapshot.get("away_team") or ""),
        "kickoff_utc": snapshot.get("kickoff_utc"),
        "freeze_utc": snapshot.get("freeze_utc"),
        "match_key": f"{cid}|{snapshot.get('season')}|{snapshot.get('kickoff_utc')}|{snapshot.get('home_team')}|{snapshot.get('away_team')}",
        "snapshot_sha256": snapshot.get("raw_snapshot_sha256"),
        "config_sha256": config_sha,
        "snapshot_contract_passed": bool(validation.get("passed")),
        "snapshot_errors": validation.get("errors") or [],
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
        result["status"] = "INVALID_SNAPSHOT_FAIL_CLOSED"
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
    parser.add_argument("snapshot")
    parser.add_argument("formal_one_x_two")
    parser.add_argument("actual_outcome")
    parser.add_argument("--out")
    args = parser.parse_args()
    payload = score(
        json.loads(Path(args.snapshot).read_text(encoding="utf-8")),
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
