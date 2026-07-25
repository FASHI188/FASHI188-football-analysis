#!/usr/bin/env python3
"""V6.19.0 research-only architecture-order isolation.

Tests the proposed ordering on the same strict daily-PIT formal priors:
  1) independent de-vigged 1X2 market marginal is fixed first;
  2) formal direct P(T=0..6,7+) is fixed independently;
  3) the score matrix is a KL/IPF-style reconciliation layer that must obey BOTH.

Comparator is the older hard 1X2 + O/U2.5 projection, which can move the full total-goal
marginal. Historical market prices lack original quote timestamps, so this is retrospective
research only and has zero formal weight.
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
V = ROOT / "validation"
E = ROOT / "engine"
for p in (V, E):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import validate_joint_market_ipf_crossseason_v6164 as base
import validate_joint_market_ipf_v6163 as old_joint
import validate_market_ou_kl_projection_v6162 as ou
from football_v460_engine import load_config, predict_from_history
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import derive_score_marginals, read_processed_matches

OUT = ROOT / "manifests" / "v6_architecture_order_v6190_status.json"
SEASONS = ("2022/23", "2023/24", "2024/25", "2025/26")
COMPS = base.COMPS
DIRECTIONS = ("home", "draw", "away")
TOL = 1e-10
MAX_ITER = 1000


def result_index(h: int, a: int) -> int:
    return 0 if h > a else 1 if h == a else 2


def total_bucket(h: int, a: int) -> int:
    return min(7, h + a)


def one_vec(matrix: list[dict[str, Any]]) -> list[float]:
    m = derive_score_marginals(matrix)["1x2"]
    return [float(m[k]) for k in DIRECTIONS]


def total_vec(matrix: list[dict[str, Any]]) -> list[float]:
    out = [0.0] * 8
    for c in matrix:
        out[total_bucket(int(c["home_goals"]), int(c["away_goals"]))] += float(c["probability"])
    return out


def copy_matrix(matrix: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "home_goals": int(c["home_goals"]),
            "away_goals": int(c["away_goals"]),
            "probability": float(c["probability"]),
        }
        for c in matrix
    ]


def _scale_groups(matrix: list[dict[str, Any]], group_fn, targets: list[float]) -> bool:
    sums = [0.0] * len(targets)
    for c in matrix:
        sums[group_fn(int(c["home_goals"]), int(c["away_goals"]))] += float(c["probability"])
    factors = []
    for current, target in zip(sums, targets):
        if current <= 0.0:
            if target > TOL:
                return False
            factors.append(1.0)
        else:
            factors.append(target / current)
    for c in matrix:
        g = group_fn(int(c["home_goals"]), int(c["away_goals"]))
        c["probability"] = float(c["probability"]) * factors[g]
    return True


def reconcile(prior: list[dict[str, Any]], target_1x2: list[float], target_total: list[float]):
    matrix = copy_matrix(prior)
    audit = {"converged": False, "iterations": 0, "max_residual": None}
    for it in range(1, MAX_ITER + 1):
        if not _scale_groups(matrix, result_index, target_1x2):
            audit.update({"reason": "ZERO_RESULT_SUPPORT", "iterations": it})
            return None, audit
        if not _scale_groups(matrix, total_bucket, target_total):
            audit.update({"reason": "ZERO_TOTAL_SUPPORT", "iterations": it})
            return None, audit
        p1 = one_vec(matrix)
        pt = total_vec(matrix)
        residual = max(
            max(abs(a - b) for a, b in zip(p1, target_1x2)),
            max(abs(a - b) for a, b in zip(pt, target_total)),
        )
        if residual <= TOL:
            z = sum(float(c["probability"]) for c in matrix)
            for c in matrix:
                c["probability"] = float(c["probability"]) / z
            audit.update({"converged": True, "iterations": it, "max_residual": residual})
            return matrix, audit
    audit.update({"reason": "MAX_ITER", "iterations": MAX_ITER, "max_residual": residual})
    return None, audit


def score_topk(matrix: list[dict[str, Any]], k: int, h: int, a: int) -> int:
    ranked = sorted(matrix, key=lambda c: float(c["probability"]), reverse=True)[:k]
    return int(any(int(c["home_goals"]) == h and int(c["away_goals"]) == a for c in ranked))


def brier3(p: list[float], actual: int) -> float:
    return sum((p[i] - (1.0 if i == actual else 0.0)) ** 2 for i in range(3))


def logloss3(p: list[float], actual: int) -> float:
    return -math.log(max(1e-15, p[actual]))


def rps8(p: list[float], actual: int) -> float:
    y = [1.0 if i == actual else 0.0 for i in range(8)]
    cp = cy = out = 0.0
    for i in range(7):
        cp += p[i]
        cy += y[i]
        out += (cp - cy) ** 2
    return out / 7.0


def fixed_selective(rows: list[dict[str, Any]]) -> dict[str, Any]:
    rules = {
        "all": lambda r: True,
        "p_ge_0.55": lambda r: r["market_maxp"] >= 0.55,
        "p_ge_0.58": lambda r: r["market_maxp"] >= 0.58,
        "p_ge_0.60": lambda r: r["market_maxp"] >= 0.60,
        "margin_ge_0.10": lambda r: r["market_margin"] >= 0.10,
        "margin_ge_0.15": lambda r: r["market_margin"] >= 0.15,
        "p55_margin10": lambda r: r["market_maxp"] >= 0.55 and r["market_margin"] >= 0.10,
    }
    out = {}
    for name, rule in rules.items():
        sub = [r for r in rows if rule(r)]
        hits = sum(int(r["new_1x2_top1"]) for r in sub)
        out[name] = {
            "count": len(sub),
            "coverage": len(sub) / len(rows) if rows else 0.0,
            "hits": hits,
            "accuracy": hits / len(sub) if sub else None,
        }
    return out


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"count": 0}
    n = len(rows)
    mean = lambda key: sum(float(r[key]) for r in rows) / n
    return {
        "count": n,
        "formal_1x2_top1": mean("formal_1x2_top1"),
        "old_order_1x2_top1": mean("old_1x2_top1"),
        "new_order_1x2_top1": mean("new_1x2_top1"),
        "formal_1x2_brier": mean("formal_1x2_brier"),
        "old_order_1x2_brier": mean("old_1x2_brier"),
        "new_order_1x2_brier": mean("new_1x2_brier"),
        "formal_1x2_logloss": mean("formal_1x2_logloss"),
        "old_order_1x2_logloss": mean("old_1x2_logloss"),
        "new_order_1x2_logloss": mean("new_1x2_logloss"),
        "formal_total_top1": mean("formal_total_top1"),
        "old_order_total_top1": mean("old_total_top1"),
        "new_order_total_top1": mean("new_total_top1"),
        "formal_total_rps": mean("formal_total_rps"),
        "old_order_total_rps": mean("old_total_rps"),
        "new_order_total_rps": mean("new_total_rps"),
        "formal_score_top1": mean("formal_score_top1"),
        "old_order_score_top1": mean("old_score_top1"),
        "new_order_score_top1": mean("new_score_top1"),
        "formal_score_top3": mean("formal_score_top3"),
        "old_order_score_top3": mean("old_score_top3"),
        "new_order_score_top3": mean("new_score_top3"),
        "new_vs_formal_1x2_uplift_pp": (mean("new_1x2_top1") - mean("formal_1x2_top1")) * 100.0,
        "new_vs_old_total_top1_pp": (mean("new_total_top1") - mean("old_total_top1")) * 100.0,
        "new_vs_formal_total_top1_pp": (mean("new_total_top1") - mean("formal_total_top1")) * 100.0,
        "new_vs_old_score_top1_pp": (mean("new_score_top1") - mean("old_score_top1")) * 100.0,
        "new_vs_formal_score_top1_pp": (mean("new_score_top1") - mean("formal_score_top1")) * 100.0,
        "fixed_selective_market_primary": fixed_selective(rows),
    }


def evaluate_comp_season(cid: str, season: str, config: dict[str, Any]):
    lookup = base.market_lookup(cid, season)
    params = ou.params_by_season(cid).get(season)
    if not params:
        return [], {"reason": "NO_FORMAL_PARAMS", "market_rows": len(lookup)}
    matches = [m for m in read_processed_matches(cid) if str(m.season) == season]
    bydate = defaultdict(list)
    for m in matches:
        bydate[m.date].append(m)
    hist = []
    home_count = Counter()
    away_count = Counter()
    temp = ou.calibrator(cid, season)
    rows = []
    warmc = int(config["validation"]["warmup_competition_matches"])
    warmt = int(config["validation"]["warmup_team_matches"])
    attempted = old_conv = new_conv = 0
    max_old_residual = max_new_residual = 0.0
    max_total_invariant = max_one_invariant = 0.0

    # Strict daily PIT: all matches on a date are predicted from the same prior-date history.
    for dt in sorted(bydate):
        day = sorted(bydate[dt], key=lambda x: (x.home_team, x.away_team))
        for m in day:
            mk = lookup.get((m.date.isoformat(), m.home_team, m.away_team))
            if len(hist) < warmc or home_count[m.home_team] < warmt or away_count[m.away_team] < warmt or not mk:
                continue
            try:
                pred = predict_from_history(
                    hist, cid, season, m.home_team, m.away_team, m.date,
                    selected_parameters=params, use_team_effects=True,
                )
            except Exception:
                continue
            prior = temperature_scale_matrix(pred["probabilities"]["score_matrix"], temp)
            target_one = [float(x) for x in mk["one_x_two"]]
            target_total = total_vec(prior)
            attempted += 1

            old_matrix, old_audit = old_joint.ipf(prior, target_one, float(mk["p_over25"]))
            if old_matrix is None or not old_audit.get("converged"):
                continue
            old_conv += 1
            max_old_residual = max(max_old_residual, float(old_audit.get("max_residual") or 0.0))

            new_matrix, new_audit = reconcile(prior, target_one, target_total)
            if new_matrix is None or not new_audit.get("converged"):
                continue
            new_conv += 1
            max_new_residual = max(max_new_residual, float(new_audit.get("max_residual") or 0.0))

            formal_one, old_one, new_one = one_vec(prior), one_vec(old_matrix), one_vec(new_matrix)
            formal_total, old_total, new_total = total_vec(prior), total_vec(old_matrix), total_vec(new_matrix)
            max_total_invariant = max(max_total_invariant, max(abs(a - b) for a, b in zip(formal_total, new_total)))
            max_one_invariant = max(max_one_invariant, max(abs(a - b) for a, b in zip(target_one, new_one)))
            actual_result = result_index(m.home_goals, m.away_goals)
            actual_total = min(7, m.home_goals + m.away_goals)
            market_rank = sorted(target_one, reverse=True)
            rows.append({
                "date": m.date.isoformat(),
                "competition_id": cid,
                "season": season,
                "formal_1x2_top1": int(max(range(3), key=lambda i: formal_one[i]) == actual_result),
                "old_1x2_top1": int(max(range(3), key=lambda i: old_one[i]) == actual_result),
                "new_1x2_top1": int(max(range(3), key=lambda i: new_one[i]) == actual_result),
                "formal_1x2_brier": brier3(formal_one, actual_result),
                "old_1x2_brier": brier3(old_one, actual_result),
                "new_1x2_brier": brier3(new_one, actual_result),
                "formal_1x2_logloss": logloss3(formal_one, actual_result),
                "old_1x2_logloss": logloss3(old_one, actual_result),
                "new_1x2_logloss": logloss3(new_one, actual_result),
                "formal_total_top1": int(max(range(8), key=lambda i: formal_total[i]) == actual_total),
                "old_total_top1": int(max(range(8), key=lambda i: old_total[i]) == actual_total),
                "new_total_top1": int(max(range(8), key=lambda i: new_total[i]) == actual_total),
                "formal_total_rps": rps8(formal_total, actual_total),
                "old_total_rps": rps8(old_total, actual_total),
                "new_total_rps": rps8(new_total, actual_total),
                "formal_score_top1": score_topk(prior, 1, m.home_goals, m.away_goals),
                "old_score_top1": score_topk(old_matrix, 1, m.home_goals, m.away_goals),
                "new_score_top1": score_topk(new_matrix, 1, m.home_goals, m.away_goals),
                "formal_score_top3": score_topk(prior, 3, m.home_goals, m.away_goals),
                "old_score_top3": score_topk(old_matrix, 3, m.home_goals, m.away_goals),
                "new_score_top3": score_topk(new_matrix, 3, m.home_goals, m.away_goals),
                "market_maxp": market_rank[0],
                "market_margin": market_rank[0] - market_rank[1],
            })
        # Only after every prediction for this date is frozen may the date enter history.
        for m in day:
            hist.append(m)
            home_count[m.home_team] += 1
            away_count[m.away_team] += 1

    return rows, {
        "market_rows": len(lookup),
        "season_matches": len(matches),
        "attempted": attempted,
        "old_converged": old_conv,
        "new_converged": new_conv,
        "max_old_residual": max_old_residual,
        "max_new_residual": max_new_residual,
        "max_total_invariant_residual": max_total_invariant,
        "max_1x2_invariant_residual": max_one_invariant,
        "same_date_history_frozen": True,
    }


def main() -> int:
    config = load_config()
    by_season = {}
    meta = {}
    all_rows = []
    for season in SEASONS:
        season_rows = []
        meta[season] = {}
        for cid in COMPS:
            rows, info = evaluate_comp_season(cid, season, config)
            season_rows.extend(rows)
            meta[season][cid] = info
        by_season[season] = summarize(season_rows)
        all_rows.extend(season_rows)

    aggregate = summarize(all_rows)
    max_total_resid = max(
        (float(info.get("max_total_invariant_residual") or 0.0) for sm in meta.values() for info in sm.values()),
        default=0.0,
    )
    max_one_resid = max(
        (float(info.get("max_1x2_invariant_residual") or 0.0) for sm in meta.values() for info in sm.values()),
        default=0.0,
    )
    attempted = sum(int(info.get("attempted") or 0) for sm in meta.values() for info in sm.values())
    new_conv = sum(int(info.get("new_converged") or 0) for sm in meta.values() for info in sm.values())
    status = "PASS" if attempted > 0 and new_conv == attempted and max_total_resid <= TOL * 2 and max_one_resid <= TOL * 2 else "WARN"

    payload = {
        "schema_version": "V6.19.0-architecture-order-isolation-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": status,
        "formal_current_version": "V5.0.1",
        "classification": "RETROSPECTIVE_MARKET_RESEARCH_NO_ORIGINAL_QUOTE_TIMESTAMP",
        "design": {
            "strict_daily_pit": True,
            "seasons": list(SEASONS),
            "competitions": list(COMPS),
            "formal_prior": "temperature-calibrated V5 formal score matrix using season-specific prior-only parameters",
            "old_order": "formal score matrix -> hard de-vigged 1X2 + O/U2.5 IPF; total marginal may move",
            "new_order": "independent de-vigged 1X2 first + independent formal direct P(T) second -> score matrix reconciliation last",
            "new_hard_constraints": ["1X2 market marginal", "formal direct P(T=0..6,7+) marginal"],
            "objective": "iterative proportional KL/I-projection over existing score support",
            "tolerance": TOL,
            "max_iterations": MAX_ITER,
            "selector_tuning": False,
            "fixed_selective_bands_only": True,
        },
        "audit": {
            "attempted": attempted,
            "new_converged": new_conv,
            "new_convergence_rate": new_conv / attempted if attempted else None,
            "max_total_invariant_residual": max_total_resid,
            "max_1x2_invariant_residual": max_one_resid,
        },
        "season_results": by_season,
        "aggregate": aggregate,
        "meta": meta,
        "decision_rule": {
            "architecture_supported_if": [
                "new 1X2 proper scores/top1 improve versus formal on broad sample",
                "new total marginal equals formal P(T) within tolerance",
                "projection converges without probability leakage",
            ],
            "exact_score_not_required_to_improve_for_architecture_acceptance": True,
        },
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "current_rule_change": False,
            "automatic_promotion": False,
            "historical_market_quotes_lack_original_timestamp": True,
            "no_same_day_result_leakage": True,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "audit": payload["audit"], "season_results": by_season, "aggregate": aggregate}, ensure_ascii=False, indent=2))
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
