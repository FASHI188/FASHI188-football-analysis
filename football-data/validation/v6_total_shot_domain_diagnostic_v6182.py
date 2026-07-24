#!/usr/bin/env python3
"""V6.18.2 frozen per-domain diagnostic for the V6.18.1 total-goals signal.

This is deliberately NOT a new model-selection round.
Everything is frozen from V6.18.1 before reading domain-level 2025/26 results:
- residual family: multinomial logistic regression
- regularization C=0.01
- calibration arm: log formal P(T) only
- shot arm: identical model plus the full V6.18.1 lagged shot/SOT/corner feature set
- training data: 2022/23 through 2024/25
- test: 2025/26

Purpose: determine whether the aggregate shot-vs-calibration increment is broad or
concentrated by competition. Results are diagnostic only and cannot promote or tune
anything. New prospective 2026/27 evidence is still required.
"""
from __future__ import annotations

import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path

import v6_total_shot_residual_v6181 as base
import v6_total_shot_residual_v6181a as fix

OUT = base.ROOT / "manifests" / "v6_total_shot_domain_diagnostic_v6182_status.json"
TRAIN_SEASONS = {"2022/23", "2023/24", "2024/25"}
TEST_SEASON = "2025/26"
FROZEN_C = 0.01
BOOTSTRAPS = 1000
SEED = 20260724


def probs_for(model, row, mode, shot_names, comps):
    return base.model_probs(model, row, mode, shot_names, comps)


def observation(row, p):
    y = int(row["actual"])
    top1 = int(max(range(len(p)), key=lambda i: p[i]) == y)
    return {
        "top1": top1,
        "rps": base.rps(p, y),
        "logloss": -math.log(max(base.EPS, p[y])),
    }


def summarize(obs):
    n = len(obs)
    if not n:
        return {"count": 0, "top1_rate": None, "rps": None, "logloss": None}
    return {
        "count": n,
        "top1_rate": sum(x["top1"] for x in obs) / n,
        "rps": sum(x["rps"] for x in obs) / n,
        "logloss": sum(x["logloss"] for x in obs) / n,
    }


def paired_delta(left, right):
    # left - right; for RPS/LogLoss negative is better for left.
    n = len(left)
    return {
        "top1_rate": sum(left[i]["top1"] - right[i]["top1"] for i in range(n)) / n,
        "rps": sum(left[i]["rps"] - right[i]["rps"] for i in range(n)) / n,
        "logloss": sum(left[i]["logloss"] - right[i]["logloss"] for i in range(n)) / n,
    }


def bootstrap_delta(left, right, seed):
    n = len(left)
    if n < 20:
        return None
    rng = random.Random(seed)
    vals = {"top1_rate": [], "rps": [], "logloss": []}
    for _ in range(BOOTSTRAPS):
        idxs = [rng.randrange(n) for _ in range(n)]
        vals["top1_rate"].append(sum(left[i]["top1"] - right[i]["top1"] for i in idxs) / n)
        vals["rps"].append(sum(left[i]["rps"] - right[i]["rps"] for i in idxs) / n)
        vals["logloss"].append(sum(left[i]["logloss"] - right[i]["logloss"] for i in idxs) / n)
    out = {}
    lo = int(0.025 * BOOTSTRAPS)
    hi = min(BOOTSTRAPS - 1, int(0.975 * BOOTSTRAPS))
    for k, arr in vals.items():
        arr.sort()
        out[k] = {"lower95": arr[lo], "upper95": arr[hi]}
    return out


def evaluate_group(rows, cal_model, shot_model, shot_names, comps, seed):
    formal_obs = []
    cal_obs = []
    shot_obs = []
    for r in rows:
        formal_obs.append(observation(r, r["formal"]))
        cal_obs.append(observation(r, probs_for(cal_model, r, "calibration", shot_names, comps)))
        shot_obs.append(observation(r, probs_for(shot_model, r, "shot", shot_names, comps)))
    return {
        "formal": summarize(formal_obs),
        "calibration": summarize(cal_obs),
        "shot": summarize(shot_obs),
        "shot_minus_calibration": paired_delta(shot_obs, cal_obs),
        "shot_minus_formal": paired_delta(shot_obs, formal_obs),
        "shot_minus_calibration_bootstrap95": bootstrap_delta(shot_obs, cal_obs, seed),
        "shot_minus_formal_bootstrap95": bootstrap_delta(shot_obs, formal_obs, seed + 1),
    }


def main():
    raw, stat_counts = base.raw_stat_matches()
    lookup, names = fix.lagged_shot_lookup_fixed(raw)
    rows, formal_meta = base.formal_rows(lookup)
    shot_names, comps = base.feature_names(rows)
    train = [r for r in rows if r["season"] in TRAIN_SEASONS]
    test = [r for r in rows if r["season"] == TEST_SEASON]
    if len(train) < 5000 or len(test) < 1500:
        raise RuntimeError(f"insufficient frozen diagnostic rows train={len(train)} test={len(test)}")

    cal_model = base.fit_model(train, "calibration", FROZEN_C, shot_names, comps)
    shot_model = base.fit_model(train, "shot", FROZEN_C, shot_names, comps)

    by_comp = {}
    for i, cid in enumerate(sorted({r["competition_id"] for r in test})):
        sub = [r for r in test if r["competition_id"] == cid]
        by_comp[cid] = evaluate_group(sub, cal_model, shot_model, shot_names, comps, SEED + i * 10)

    aggregate = evaluate_group(test, cal_model, shot_model, shot_names, comps, SEED + 999)
    broad = {
        "domains": len(by_comp),
        "shot_top1_better_than_calibration": sum(1 for v in by_comp.values() if v["shot_minus_calibration"]["top1_rate"] > 0),
        "shot_rps_better_than_calibration": sum(1 for v in by_comp.values() if v["shot_minus_calibration"]["rps"] < 0),
        "shot_logloss_better_than_calibration": sum(1 for v in by_comp.values() if v["shot_minus_calibration"]["logloss"] < 0),
        "all_three_better_than_calibration": sum(1 for v in by_comp.values() if v["shot_minus_calibration"]["top1_rate"] > 0 and v["shot_minus_calibration"]["rps"] < 0 and v["shot_minus_calibration"]["logloss"] < 0),
        "bootstrap95_top1_positive_vs_calibration": sum(1 for v in by_comp.values() if v["shot_minus_calibration_bootstrap95"] and v["shot_minus_calibration_bootstrap95"]["top1_rate"]["lower95"] > 0),
    }

    payload = {
        "schema_version": "V6.18.2-frozen-domain-diagnostic-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "POST_HOC_DOMAIN_DIAGNOSTIC_OF_FROZEN_2025_26_OOS_NOT_SELECTION",
        "frozen_design": {
            "C": FROZEN_C,
            "train_seasons": sorted(TRAIN_SEASONS),
            "test_season": TEST_SEASON,
            "feature_set": shot_names,
            "parameter_selection_from_domain_results": False,
            "feature_selection_from_domain_results": False,
            "promotion_allowed": False,
        },
        "train_rows": len(train),
        "test_rows": len(test),
        "aggregate": aggregate,
        "breadth": broad,
        "by_competition": by_comp,
        "source_meta": {"stat_counts": stat_counts, "formal_join_meta": formal_meta},
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "current_rule_change": False,
            "runtime_probability_change": False,
            "no_domain_may_be_promoted_from_this_post_hoc_diagnostic": True,
            "next_valid_step": "freeze candidate before 2026/27 and collect prospective per-domain evidence",
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"aggregate": aggregate, "breadth": broad, "by_competition": {k: {"shot_minus_calibration": v["shot_minus_calibration"], "shot_minus_formal": v["shot_minus_formal"]} for k, v in by_comp.items()}}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
