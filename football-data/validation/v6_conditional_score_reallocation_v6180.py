#!/usr/bin/env python3
"""V6.18.0 conditional exact-score reallocation challenge.

Research-only. Goal: improve exact-score structure without changing the formal
0-7+ total-goals marginal or 1X2 marginal.

For every formal prior score matrix, cells are partitioned by
(total bucket, 1X2 result). Historical score frequencies are learned strictly
from matches that occurred before the target match. Within each partition only,
we replace the prior conditional allocation with a Dirichlet posterior:

    q_i = (count_i + kappa * p_i) / (N + kappa)

The partition mass itself is unchanged, therefore total-goals (0..6,7+) and
1X2 marginals are invariant by construction. No market price is used here.

Design:
- Development: 2022/23, 2023/24, 2024/25.
- Frozen test: 2025/26.
- Kappa candidates are pre-registered below and selected independently per
  competition on development score log loss, then score Top-1 as tie-break.
- Formal engine parameters/calibrator remain season-specific and unchanged.
- No formal weight/runtime/CURRENT change.
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

import validate_market_ou_kl_projection_v6162 as ou
import validate_joint_market_ipf_v6163 as joint
from football_v460_engine import load_config, predict_from_history
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import derive_score_marginals, read_processed_matches

OUT = ROOT / "manifests" / "v6_conditional_score_reallocation_v6180_status.json"
DEV_SEASONS = ("2022/23", "2023/24", "2024/25")
TEST_SEASON = "2025/26"
KAPPAS = (5.0, 20.0, 80.0, 320.0)
TOTAL_CAP = 7
EPS = 1e-15
MIN_DEV_ROWS = 80


def rows(matrix: list[dict[str, Any]]):
    for c in matrix:
        yield int(c["home_goals"]), int(c["away_goals"]), float(c["probability"])


def renorm(matrix: list[dict[str, Any]]):
    s = sum(float(c["probability"]) for c in matrix)
    if not math.isfinite(s) or s <= 0:
        raise ValueError("invalid score-matrix mass")
    return [
        {
            "home_goals": int(c["home_goals"]),
            "away_goals": int(c["away_goals"]),
            "probability": float(c["probability"]) / s,
        }
        for c in matrix
    ]


def result_key(h: int, a: int) -> str:
    return "H" if h > a else "D" if h == a else "A"


def group_key(h: int, a: int) -> tuple[int, str]:
    return min(TOTAL_CAP, h + a), result_key(h, a)


def actual_cell_key(h: int, a: int) -> tuple[tuple[int, str], int, int]:
    return group_key(h, a), h, a


def adjusted_matrix(prior, hist_counts: Counter, kappa: float):
    prior = renorm(prior)
    grouped: dict[tuple[int, str], list[tuple[int, int, float]]] = defaultdict(list)
    for h, a, p in rows(prior):
        grouped[group_key(h, a)].append((h, a, p))

    out = []
    changed_groups = 0
    for g, cells in grouped.items():
        mass = sum(p for _, _, p in cells)
        if mass <= 0:
            continue
        n = sum(hist_counts[(g, h, a)] for h, a, _ in cells)
        if n <= 0:
            out.extend(
                {"home_goals": h, "away_goals": a, "probability": p}
                for h, a, p in cells
            )
            continue
        changed_groups += 1
        denom = float(n) + float(kappa)
        for h, a, p in cells:
            p_cond = p / mass
            c = float(hist_counts[(g, h, a)])
            q_cond = (c + float(kappa) * p_cond) / denom
            out.append(
                {"home_goals": h, "away_goals": a, "probability": mass * q_cond}
            )
    return renorm(out), changed_groups


def score_metrics(matrix, hg: int, ag: int):
    ranked = sorted(((p, h, a) for h, a, p in rows(matrix)), reverse=True)
    top1 = int(bool(ranked) and (ranked[0][1], ranked[0][2]) == (hg, ag))
    top3 = int((hg, ag) in {(h, a) for _, h, a in ranked[:3]})
    p_actual = sum(p for h, a, p in rows(matrix) if h == hg and a == ag)
    logloss = -math.log(max(EPS, p_actual))
    return top1, top3, logloss, p_actual


def marginal_residual(a, b):
    ma = derive_score_marginals(a)
    mb = derive_score_marginals(b)
    r1 = max(
        abs(float(ma["1x2"][k]) - float(mb["1x2"][k]))
        for k in ("home", "draw", "away")
    )
    keys = set(ma["total_goals"]) | set(mb["total_goals"])
    rt = max(
        abs(float(ma["total_goals"].get(k, 0.0)) - float(mb["total_goals"].get(k, 0.0)))
        for k in keys
    ) if keys else 0.0
    return r1, rt


def fresh_acc():
    return {
        "n": 0,
        "top1": 0,
        "top3": 0,
        "logloss_sum": 0.0,
        "actual_prob_sum": 0.0,
        "changed_rows": 0,
        "changed_groups": 0,
        "max_1x2_residual": 0.0,
        "max_total_residual": 0.0,
    }


def add_metric(acc, prior, adj, hg, ag, changed_groups):
    t1, t3, ll, pa = score_metrics(adj, hg, ag)
    r1, rt = marginal_residual(prior, adj)
    acc["n"] += 1
    acc["top1"] += t1
    acc["top3"] += t3
    acc["logloss_sum"] += ll
    acc["actual_prob_sum"] += pa
    acc["changed_rows"] += int(changed_groups > 0)
    acc["changed_groups"] += int(changed_groups)
    acc["max_1x2_residual"] = max(acc["max_1x2_residual"], r1)
    acc["max_total_residual"] = max(acc["max_total_residual"], rt)


def finish(acc):
    n = int(acc["n"])
    if n <= 0:
        return {**acc, "top1_rate": None, "top3_rate": None, "mean_logloss": None, "mean_actual_probability": None}
    return {
        **acc,
        "top1_rate": acc["top1"] / n,
        "top3_rate": acc["top3"] / n,
        "mean_logloss": acc["logloss_sum"] / n,
        "mean_actual_probability": acc["actual_prob_sum"] / n,
    }


def formal_rows(cid: str, season: str, config):
    params = ou.params_by_season(cid).get(season)
    if not params:
        return [], {"reason": "NO_FORMAL_PARAMS"}
    temp = ou.calibrator(cid, season)
    matches = [m for m in read_processed_matches(cid) if str(m.season) == season]
    bydate = defaultdict(list)
    for m in matches:
        bydate[m.date].append(m)
    hist = []
    hc = Counter()
    ac = Counter()
    out = []
    failures = 0
    warmc = int(config["validation"]["warmup_competition_matches"])
    warmt = int(config["validation"]["warmup_team_matches"])
    for dt in sorted(bydate):
        for m in sorted(bydate[dt], key=lambda x: (x.home_team, x.away_team)):
            if len(hist) >= warmc and hc[m.home_team] >= warmt and ac[m.away_team] >= warmt:
                try:
                    pred = predict_from_history(
                        hist,
                        cid,
                        season,
                        m.home_team,
                        m.away_team,
                        m.date,
                        selected_parameters=params,
                        use_team_effects=True,
                    )
                    prior = temperature_scale_matrix(pred["probabilities"]["score_matrix"], temp)
                    out.append((m, prior))
                except Exception:
                    failures += 1
            hist.append(m)
            hc[m.home_team] += 1
            ac[m.away_team] += 1
    return out, {"matches": len(matches), "prediction_rows": len(out), "prediction_failures": failures}


def seed_counts_from_matches(counts: Counter, matches):
    for m in matches:
        counts[actual_cell_key(int(m.home_goals), int(m.away_goals))] += 1


def evaluate_comp(cid: str, config):
    counts = Counter()
    dev_by_k = {k: fresh_acc() for k in KAPPAS}
    baseline_dev = fresh_acc()
    meta = {"development": {}, "test": {}}

    # Development is processed chronologically; only prior actual scores are in counts.
    for season in DEV_SEASONS:
        fr, mta = formal_rows(cid, season, config)
        meta["development"][season] = mta
        for match, prior in fr:
            hg, ag = int(match.home_goals), int(match.away_goals)
            # Baseline is included for direct comparability.
            add_metric(baseline_dev, prior, prior, hg, ag, 0)
            for k in KAPPAS:
                adj, cg = adjusted_matrix(prior, counts, k)
                add_metric(dev_by_k[k], prior, adj, hg, ag, cg)
            counts[actual_cell_key(hg, ag)] += 1

    dev_finished = {str(k): finish(v) for k, v in dev_by_k.items()}
    bdev = finish(baseline_dev)
    eligible = [
        k for k in KAPPAS
        if dev_by_k[k]["n"] >= MIN_DEV_ROWS
        and dev_by_k[k]["max_1x2_residual"] <= 1e-10
        and dev_by_k[k]["max_total_residual"] <= 1e-10
    ]
    if not eligible:
        return {
            "status": "INSUFFICIENT_DEVELOPMENT",
            "development_baseline": bdev,
            "development_candidates": dev_finished,
            "selected_kappa": None,
            "test": None,
            "meta": meta,
        }

    # Proper score first; hit-rate is only a tie-breaker.
    selected = min(
        eligible,
        key=lambda k: (
            dev_finished[str(k)]["mean_logloss"],
            -dev_finished[str(k)]["top1_rate"],
            -dev_finished[str(k)]["top3_rate"],
            k,
        ),
    )

    test_rows, test_meta = formal_rows(cid, TEST_SEASON, config)
    meta["test"][TEST_SEASON] = test_meta
    baseline_test = fresh_acc()
    challenge_test = fresh_acc()
    for match, prior in test_rows:
        hg, ag = int(match.home_goals), int(match.away_goals)
        add_metric(baseline_test, prior, prior, hg, ag, 0)
        adj, cg = adjusted_matrix(prior, counts, selected)
        add_metric(challenge_test, prior, adj, hg, ag, cg)
        counts[actual_cell_key(hg, ag)] += 1

    bt = finish(baseline_test)
    ct = finish(challenge_test)
    test = {
        "baseline": bt,
        "challenge": ct,
        "delta": {
            "top1_rate": None if bt["top1_rate"] is None else ct["top1_rate"] - bt["top1_rate"],
            "top3_rate": None if bt["top3_rate"] is None else ct["top3_rate"] - bt["top3_rate"],
            "mean_logloss": None if bt["mean_logloss"] is None else ct["mean_logloss"] - bt["mean_logloss"],
            "mean_actual_probability": None if bt["mean_actual_probability"] is None else ct["mean_actual_probability"] - bt["mean_actual_probability"],
        },
    }
    return {
        "status": "PASS",
        "development_baseline": bdev,
        "development_candidates": dev_finished,
        "selected_kappa": selected,
        "test": test,
        "meta": meta,
    }


def aggregate(results):
    b = fresh_acc()
    c = fresh_acc()
    comps = 0
    for cid, r in results.items():
        if r.get("status") != "PASS" or not r.get("test"):
            continue
        comps += 1
        for src, dst in ((r["test"]["baseline"], b), (r["test"]["challenge"], c)):
            for k in ("n", "top1", "top3", "changed_rows", "changed_groups"):
                dst[k] += src[k]
            dst["logloss_sum"] += src["logloss_sum"]
            dst["actual_prob_sum"] += src["actual_prob_sum"]
            dst["max_1x2_residual"] = max(dst["max_1x2_residual"], src["max_1x2_residual"])
            dst["max_total_residual"] = max(dst["max_total_residual"], src["max_total_residual"])
    bf, cf = finish(b), finish(c)
    return {
        "competitions_tested": comps,
        "baseline": bf,
        "challenge": cf,
        "delta": {
            "top1_rate": None if bf["top1_rate"] is None else cf["top1_rate"] - bf["top1_rate"],
            "top3_rate": None if bf["top3_rate"] is None else cf["top3_rate"] - bf["top3_rate"],
            "mean_logloss": None if bf["mean_logloss"] is None else cf["mean_logloss"] - bf["mean_logloss"],
            "mean_actual_probability": None if bf["mean_actual_probability"] is None else cf["mean_actual_probability"] - bf["mean_actual_probability"],
        },
    }


def main():
    cfg = load_config()
    results = {}
    for cid in joint.COMPS:
        results[cid] = evaluate_comp(cid, cfg)
    agg = aggregate(results)
    status = "PASS" if agg["competitions_tested"] > 0 else "NO_ELIGIBLE_TEST_DOMAINS"
    payload = {
        "schema_version": "V6.18.0-conditional-score-reallocation-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": status,
        "formal_current_version": "V5.0.1",
        "classification": "STRICT_PRIOR_SCORE_STRUCTURE_RESEARCH",
        "design": {
            "development_seasons": list(DEV_SEASONS),
            "frozen_test_season": TEST_SEASON,
            "kappa_candidates": list(KAPPAS),
            "min_development_rows": MIN_DEV_ROWS,
            "selection_objective": "lowest development exact-score log loss; Top-1 then Top-3 tie-break",
            "partition": "(0-7+ total bucket, 1X2 result)",
            "invariants": ["1X2 marginal", "0-7+ total-goals marginal"],
            "market_used": False,
            "formal_parameters_changed": False,
        },
        "aggregate_test": agg,
        "competition_results": results,
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "no_test_parameter_selection": True,
            "asian_handicap_not_preserved_by_partition_and_requires_separate_audit_before_any_promotion": True,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "aggregate_test": agg, "selected": {k: v.get("selected_kappa") for k, v in results.items()}}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
