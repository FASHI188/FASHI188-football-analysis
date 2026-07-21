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

from platform_core import canonical_json_bytes, sha256_bytes, score_matrix_rows
from prospective_market_matrix_shadow_v531 import evaluate as evaluate_shadow
from prospective_market_snapshot_v523 import validate as validate_snapshot

REGISTRY = ROOT / "config" / "market_matrix_projection_final_registry_v531.json"
VALIDATION_CFG = ROOT / "config" / "prospective_market_matrix_validation_v548.json"
EPS = 1e-15


def _sha(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def _metrics(matrix: list[dict[str, Any]], hg: int, ag: int) -> dict[str, float]:
    one = {"home": 0.0, "draw": 0.0, "away": 0.0}
    totals = [0.0] * 8
    cells = []
    p_observed = 0.0
    p_over25 = 0.0
    for h, a, p in score_matrix_rows(matrix):
        group = "home" if h > a else "draw" if h == a else "away"
        one[group] += p
        totals[min(7, h + a)] += p
        cells.append((p, h, a))
        if h == hg and a == ag:
            p_observed += p
        if h + a >= 3:
            p_over25 += p
    actual = "home" if hg > ag else "draw" if hg == ag else "away"
    pick = max(("home", "draw", "away"), key=lambda k: one[k])
    brier = sum((one[k] - (1.0 if k == actual else 0.0)) ** 2 for k in one)
    order = ["away", "draw", "home"]
    cumulative_p = 0.0
    cumulative_o = 0.0
    rps = 0.0
    actual_idx = order.index(actual)
    for idx in range(2):
        cumulative_p += one[order[idx]]
        cumulative_o += 1.0 if actual_idx == idx else 0.0
        rps += (cumulative_p - cumulative_o) ** 2
    rps /= 2.0
    cells.sort(reverse=True)
    observed_score = (hg, ag)
    score_top1 = 1.0 if cells and (cells[0][1], cells[0][2]) == observed_score else 0.0
    score_top3 = 1.0 if any((h, a) == observed_score for _p, h, a in cells[:3]) else 0.0
    observed_total = min(7, hg + ag)
    total_rank = sorted(range(8), key=lambda i: (-totals[i], i))
    running = 0.0
    total_rps = 0.0
    for idx, prob in enumerate(totals[:-1]):
        running += prob
        observed_cdf = 1.0 if observed_total <= idx else 0.0
        total_rps += (running - observed_cdf) ** 2
    total_rps /= 7.0
    actual_over = 1.0 if hg + ag >= 3 else 0.0
    p_over = min(1.0 - EPS, max(EPS, p_over25))
    return {
        "one_x_two_accuracy": 1.0 if pick == actual else 0.0,
        "one_x_two_brier": brier,
        "one_x_two_rps": rps,
        "joint_log": -math.log(max(EPS, p_observed)),
        "score_top1": score_top1,
        "score_top3": score_top3,
        "total_top1": 1.0 if total_rank[0] == observed_total else 0.0,
        "total_top2": 1.0 if observed_total in total_rank[:2] else 0.0,
        "total_rps": total_rps,
        "ou_brier": (p_over25 - actual_over) ** 2,
    }


def score(snapshot: dict[str, Any], formal_matrix: list[dict[str, Any]], home_goals: int, away_goals: int) -> dict[str, Any]:
    snap_validation = validate_snapshot(snapshot)
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    cfg = json.loads(VALIDATION_CFG.read_text(encoding="utf-8"))
    registry_sha = _sha(registry)
    cfg_sha = _sha(cfg)
    formal_sha = _sha(formal_matrix)
    shadow = evaluate_shadow(snapshot, formal_matrix)
    result = {
        "schema_version": "V5.4.8-prospective-market-matrix-outcome-r1",
        "evaluated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "competition_id": str(snapshot.get("competition_id") or ""),
        "season": str(snapshot.get("season") or ""),
        "home_team": str(snapshot.get("home_team") or ""),
        "away_team": str(snapshot.get("away_team") or ""),
        "kickoff_utc": snapshot.get("kickoff_utc"),
        "freeze_utc": snapshot.get("freeze_utc"),
        "match_key": f"{snapshot.get('competition_id')}|{snapshot.get('season')}|{snapshot.get('kickoff_utc')}|{snapshot.get('home_team')}|{snapshot.get('away_team')}",
        "snapshot_sha256": snapshot.get("raw_snapshot_sha256"),
        "formal_matrix_sha256": formal_sha,
        "registry_sha256": registry_sha,
        "validation_config_sha256": cfg_sha,
        "snapshot_contract_passed": bool(snap_validation.get("passed")),
        "snapshot_errors": snap_validation.get("errors") or [],
        "shadow_status": shadow.get("shadow_status"),
        "formal_weight_change": False,
        "probability_change": False,
        "formal_promotion": False,
        "actual_score": {"home_goals": int(home_goals), "away_goals": int(away_goals)},
    }
    if not snap_validation.get("passed"):
        result["status"] = "INVALID_SNAPSHOT_FAIL_CLOSED"
        return result
    if shadow.get("shadow_status") != "SHADOW_MARKET_MATRIX_READY":
        result["status"] = "NO_ELIGIBLE_SHADOW_MATRIX"
        return result
    candidate = shadow.get("candidate_matrix")
    audit = shadow.get("audit") or {}
    if not isinstance(candidate, list) or not candidate:
        result["status"] = "SHADOW_MATRIX_MISSING_FAIL_CLOSED"
        return result
    result.update({
        "status": "SCORED_SHADOW_ROW",
        "profile": shadow.get("frozen_profile"),
        "candidate_matrix_sha256": _sha(candidate),
        "projection_audit": audit,
        "formal_metrics": _metrics(formal_matrix, int(home_goals), int(away_goals)),
        "candidate_metrics": _metrics(candidate, int(home_goals), int(away_goals)),
    })
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot")
    parser.add_argument("formal_matrix")
    parser.add_argument("home_goals", type=int)
    parser.add_argument("away_goals", type=int)
    parser.add_argument("--out")
    args = parser.parse_args()
    payload = score(
        json.loads(Path(args.snapshot).read_text(encoding="utf-8")),
        json.loads(Path(args.formal_matrix).read_text(encoding="utf-8")),
        args.home_goals,
        args.away_goals,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if payload.get("status") == "SCORED_SHADOW_ROW" else 2


if __name__ == "__main__":
    raise SystemExit(main())
