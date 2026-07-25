#!/usr/bin/env python3
"""V6.25.14 fixed-model random100 stability audit for V6.25.13.

Evaluation-only; formal_weight=0.

The V6.25.13 model is frozen at the validation-selected hyperparameters
L2=10.0 and alpha=0.5. This script does not tune anything. It rebuilds the same
strict-PIT 2025/26 holdout, fits the seven ordinal residual heads on the same
2022/23+2023/24+2024/25 pre-holdout data, then evaluates 1,000 deterministic
random samples of 100 holdout matches.

The purpose is to answer one question: is the small exact-total Top-1 gain seen
on the full untouched holdout stable to ordinary 100-match sampling variation?
"""
from __future__ import annotations

import json
import math
import random
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "engine", ROOT / "validation"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import v6_total_xg_ordinal_residual_v62513 as core  # noqa: E402
from v6_total_distribution_pit_calibration_v6244 import _score  # noqa: E402

# Execution-only compatibility with the V6.25.13 receipt typo. Statistical code unchanged.
core.true = True
core.false = False

OUT = ROOT / "manifests" / "v6_total_xg_ordinal_resample_v62514_status.json"
FIXED_L2 = 10.0
FIXED_ALPHA = 0.5
SAMPLE_N = 100
RESAMPLES = 1000
SEED_BASE = 20260725
EPS = 1e-12


def _total_log_loss(rows: list[dict[str, Any]], matrix_key: str) -> float:
    return core._total_log_loss(rows, matrix_key)


def _summary(rows: list[dict[str, Any]], matrix_key: str) -> dict[str, float]:
    metric = _score(rows, matrix_key)
    return {
        "top1_accuracy": float(metric["total_goals_0_7plus"]["top1_accuracy"]),
        "mean_rps": float(metric["total_goals_0_7plus"]["mean_rps"]),
        "log_loss": float(_total_log_loss(rows, matrix_key)),
        "score_top1_accuracy": float(metric["score"]["top1_accuracy"]),
        "score_top3_accuracy": float(metric["score"]["top3_accuracy"]),
    }


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    xs = sorted(values)
    pos = (len(xs) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return xs[lo]
    w = pos - lo
    return xs[lo] * (1.0 - w) + xs[hi] * w


def _dist(values: list[float]) -> dict[str, float]:
    return {
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "p02_5": _quantile(values, 0.025),
        "p10": _quantile(values, 0.10),
        "p90": _quantile(values, 0.90),
        "p97_5": _quantile(values, 0.975),
        "min": min(values),
        "max": max(values),
    }


def main() -> int:
    rows, attach_audit = core._strict_rows_with_xg()
    train = [r for r in rows if r["season"] in core.TRAIN_SEASONS]
    valid = [r for r in rows if r["season"] == core.VALID_SEASON]
    holdout = [r for r in rows if r["season"] == core.HOLDOUT_SEASON]
    if len(holdout) < SAMPLE_N:
        raise RuntimeError(f"holdout {len(holdout)} < random sample size {SAMPLE_N}")

    models = core._fit_models(train + valid, FIXED_L2)
    paired = core._rows_with_candidate(holdout, models, FIXED_ALPHA)
    full_baseline = _summary(paired, "baseline_matrix")
    full_candidate = _summary(paired, "candidate_matrix")

    base_top1: list[float] = []
    cand_top1: list[float] = []
    delta_top1: list[float] = []
    delta_rps: list[float] = []
    delta_log: list[float] = []
    delta_score_top1: list[float] = []
    wins = ties = losses = 0
    sample_receipts: list[dict[str, Any]] = []

    for i in range(RESAMPLES):
        seed = SEED_BASE + i
        rng = random.Random(seed)
        sample = rng.sample(paired, SAMPLE_N)
        b = _summary(sample, "baseline_matrix")
        c = _summary(sample, "candidate_matrix")
        d_top1 = c["top1_accuracy"] - b["top1_accuracy"]
        d_rps = c["mean_rps"] - b["mean_rps"]
        d_log = c["log_loss"] - b["log_loss"]
        d_score = c["score_top1_accuracy"] - b["score_top1_accuracy"]
        base_top1.append(b["top1_accuracy"])
        cand_top1.append(c["top1_accuracy"])
        delta_top1.append(d_top1)
        delta_rps.append(d_rps)
        delta_log.append(d_log)
        delta_score_top1.append(d_score)
        if d_top1 > 1e-15:
            wins += 1
        elif d_top1 < -1e-15:
            losses += 1
        else:
            ties += 1
        if i < 20:
            sample_receipts.append({
                "seed": seed,
                "baseline_top1": b["top1_accuracy"],
                "candidate_top1": c["top1_accuracy"],
                "delta_top1": d_top1,
                "delta_rps": d_rps,
                "delta_log_loss": d_log,
            })

    payload = {
        "schema_version": "V6.25.14-xg-ordinal-random100-stability-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "EVALUATION_ONLY_FIXED_V62513_FORMAL_WEIGHT_0",
        "fixed_model": {
            "source": "V6.25.13 validation-selected challenger",
            "l2": FIXED_L2,
            "alpha": FIXED_ALPHA,
            "refit_seasons": sorted(core.TRAIN_SEASONS | {core.VALID_SEASON}),
            "holdout_season": core.HOLDOUT_SEASON,
            "holdout_count": len(paired),
            "retuned_on_holdout": False,
        },
        "attachment_audit": {
            "panel_sha256": attach_audit["panel_sha256"],
            "attached_rows": attach_audit["attached_rows"],
        },
        "full_holdout": {
            "baseline": full_baseline,
            "candidate": full_candidate,
            "delta": {k: full_candidate[k] - full_baseline[k] for k in full_baseline},
        },
        "random100": {
            "resamples": RESAMPLES,
            "sample_size": SAMPLE_N,
            "seed_base": SEED_BASE,
            "sampling": "without replacement within each deterministic 100-match sample; samples overlap across seeds",
            "baseline_top1_distribution": _dist(base_top1),
            "candidate_top1_distribution": _dist(cand_top1),
            "delta_top1_distribution": _dist(delta_top1),
            "delta_rps_distribution": _dist(delta_rps),
            "delta_log_loss_distribution": _dist(delta_log),
            "delta_score_top1_distribution": _dist(delta_score_top1),
            "candidate_exact_top1_wins": wins,
            "candidate_exact_top1_ties": ties,
            "candidate_exact_top1_losses": losses,
            "candidate_exact_top1_win_rate": wins / RESAMPLES,
            "candidate_exact_top1_nonloss_rate": (wins + ties) / RESAMPLES,
            "rps_improvement_rate": sum(1 for x in delta_rps if x < 0.0) / RESAMPLES,
            "logloss_improvement_rate": sum(1 for x in delta_log if x < 0.0) / RESAMPLES,
            "score_top1_improvement_rate": sum(1 for x in delta_score_top1 if x > 0.0) / RESAMPLES,
            "first_20_samples": sample_receipts,
        },
        "decision": {
            "stable_positive_exact_top1": bool(_quantile(delta_top1, 0.025) > 0.0),
            "majority_exact_top1_win": bool(wins > losses),
            "majority_proper_score_improvement": bool(
                sum(1 for x in delta_rps if x < 0.0) > RESAMPLES / 2
                and sum(1 for x in delta_log if x < 0.0) > RESAMPLES / 2
            ),
            "promotion_eligible": False,
        },
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "current_rule_change": False,
            "runtime_probability_change": False,
            "holdout_predictions_fixed_before_resampling": True,
            "resampling_used_for_evaluation_only": True,
            "automatic_promotion": False,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "full_holdout": payload["full_holdout"],
        "random100": {k: v for k, v in payload["random100"].items() if k != "first_20_samples"},
        "decision": payload["decision"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
