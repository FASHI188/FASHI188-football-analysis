#!/usr/bin/env python3
"""V6.25.15 xG-quality + strict-PIT shot-quantity ordinal total challenger.

Research/development only; formal_weight=0.

This challenger combines two already audited pre-match information layers:
- immutable V6.18.9 Understat state: xG/xGA/npxG/npxGA/PPDA/deep/xPTS;
- V6.25.7 strict-PIT shot state: rolling shots/SOT, recent-vs-season and venue state.

The statistical target/architecture is unchanged from V6.25.13: seven ordinal
survival residuals P(T>k), k=0..6, with the formal total marginal as the offset,
PAV monotonic repair, and re-embedding into the same joint score matrix.

IMPORTANT GOVERNANCE
--------------------
The 2025/26 five-league season was already observed while assessing V6.25.13.
Therefore this V6.25.15 run may use 2025/26 only as a DEVELOPMENT BENCHMARK. It
is not an untouched holdout and can never be promotion evidence. Any eventual
promotion requires a genuinely later frozen forward sample or another untouched
chronological domain/season under a pre-registered contract.
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "engine", ROOT / "validation"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import v6_total_xg_ordinal_residual_v62513 as core  # noqa: E402
import v6_total_shot_dynamic_23split_v6257 as dynamic  # noqa: E402
import v6_total_shot_feature_offset_v6253 as shot  # noqa: E402

# Execution-only compatibility with the V6.25.13 receipt typo.
core.true = True
core.false = False

OUT = ROOT / "manifests" / "v6_total_xg_shot_ordinal_v62515_status.json"
L2_GRID = core.L2_GRID
ALPHA_GRID = core.ALPHA_GRID
PROPER_TOL = core.PROPER_TOL


def _augment_with_shot_state(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    domains = sorted({str(r["competition_id"]) for r in rows})
    stat_rows: dict[str, list[Any]] = {}
    stat_meta: dict[str, Any] = {}
    for cid in domains:
        data, meta = shot._read_stat_rows(cid)
        stat_rows[cid] = data
        stat_meta[cid] = meta

    output: list[dict[str, Any]] = []
    misses = Counter()
    for row in rows:
        cid = str(row["competition_id"])
        cutoff = datetime.fromisoformat(str(row["date"]) + "T00:00:00+00:00")
        history = shot._stat_history(stat_rows[cid], str(row["season"]), cutoff)
        base_shot = shot._shot_features(history, str(row["home_team"]), str(row["away_team"]))
        if base_shot is None:
            misses[cid] += 1
            continue
        dyn = dynamic._dynamic_state(history, str(row["home_team"]), str(row["away_team"]))
        item = dict(row)
        item["xg_x"] = [
            *[float(v) for v in row["xg_x"]],
            *[float(v) for v in base_shot],
            *[float(v) for v in dyn],
        ]
        output.append(item)
    return output, {
        "input_rows": len(rows),
        "attached_rows": len(output),
        "attach_rate": len(output) / len(rows) if rows else 0.0,
        "missing_shot_feature_by_domain": dict(sorted(misses.items())),
        "raw_shot_coverage": stat_meta,
        "feature_dimension": len(output[0]["xg_x"]) if output else 0,
    }


def _run() -> dict[str, Any]:
    xg_rows, xg_attach = core._strict_rows_with_xg()
    rows, shot_attach = _augment_with_shot_state(xg_rows)
    train = [r for r in rows if r["season"] in core.TRAIN_SEASONS]
    valid = [r for r in rows if r["season"] == core.VALID_SEASON]
    benchmark = [r for r in rows if r["season"] == core.HOLDOUT_SEASON]
    if min(len(train), len(valid), len(benchmark)) < 850:
        raise RuntimeError(
            f"insufficient combined chronology rows train={len(train)} valid={len(valid)} benchmark={len(benchmark)}"
        )

    valid_base = core._summary(core._evaluate(valid, None, 0.0))
    leaderboard: list[dict[str, Any]] = [{
        "l2": None,
        "alpha": 0.0,
        **valid_base,
        "proper_nonworse": True,
    }]
    for l2 in L2_GRID:
        models = core._fit_models(train, float(l2))
        for alpha in ALPHA_GRID:
            if alpha <= 0.0:
                continue
            res = core._summary(core._evaluate(valid, models, float(alpha)))
            res.update({
                "l2": float(l2),
                "alpha": float(alpha),
                "proper_nonworse": bool(
                    float(res["total_mean_rps"]) <= float(valid_base["total_mean_rps"]) + PROPER_TOL
                    and float(res["total_log_loss"]) <= float(valid_base["total_log_loss"]) + PROPER_TOL
                ),
            })
            leaderboard.append(res)

    eligible = [r for r in leaderboard if bool(r["proper_nonworse"])]
    selected = min(
        eligible,
        key=lambda r: (
            -float(r["total_top1_accuracy"]),
            float(r["total_mean_rps"]),
            float(r["total_log_loss"]),
            float(r["alpha"]),
            float(r["l2"] or 1e18),
        ),
    )

    models = None
    if float(selected["alpha"]) > 0.0:
        models = core._fit_models(train + valid, float(selected["l2"]))
    base = core._summary(core._evaluate(benchmark, None, 0.0))
    candidate = core._summary(core._evaluate(benchmark, models, float(selected["alpha"])))

    per_domain: dict[str, Any] = {}
    for cid in sorted({str(r["competition_id"]) for r in benchmark}):
        subset = [r for r in benchmark if str(r["competition_id"]) == cid]
        b = core._summary(core._evaluate(subset, None, 0.0))
        c = core._summary(core._evaluate(subset, models, float(selected["alpha"])))
        per_domain[cid] = {"baseline": b, "candidate": c, "delta": core._delta(b, c)}

    return {
        "schema_version": "V6.25.15-xg-shot-ordinal-development-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "DEVELOPMENT_RESEARCH_XG_QUALITY_PLUS_SHOT_QUANTITY_FORMAL_WEIGHT_0",
        "chronology": {
            "fit_seasons": sorted(core.TRAIN_SEASONS),
            "validation_season": core.VALID_SEASON,
            "reused_development_benchmark_season": core.HOLDOUT_SEASON,
            "fit_count": len(train),
            "validation_count": len(valid),
            "benchmark_count": len(benchmark),
        },
        "feature_contract": {
            "xg_panel": "immutable V6.18.9 hash-bound pre-match state",
            "shot_state": "V6.25.3 + V6.25.7 strict date-before-match rolling shot/SOT state",
            "concept": "shot quantity + shot quality",
            "combined_feature_dimension": shot_attach["feature_dimension"],
            "same_day_features_excluded": True,
            "postmatch_target_features": False,
        },
        "attachment_audit": {"xg": xg_attach, "shot": shot_attach},
        "model": {
            "architecture": "same seven-threshold ordinal residual/PAV joint-matrix mapping as V6.25.13",
            "l2_grid": list(L2_GRID),
            "alpha_grid": list(ALPHA_GRID),
        },
        "validation_baseline": valid_base,
        "validation_leaderboard": leaderboard,
        "selected": selected,
        "development_benchmark": {
            "baseline": base,
            "candidate": candidate,
            "delta": core._delta(base, candidate),
            "per_domain": per_domain,
        },
        "decision": {
            "candidate_nonzero": bool(float(selected["alpha"]) > 0.0),
            "validation_proper_gate_pass": bool(selected["proper_nonworse"]),
            "benchmark_exact_top1_improved": bool(candidate["total_top1_accuracy"] > base["total_top1_accuracy"]),
            "benchmark_rps_nonworse": bool(candidate["total_mean_rps"] <= base["total_mean_rps"] + PROPER_TOL),
            "benchmark_logloss_nonworse": bool(candidate["total_log_loss"] <= base["total_log_loss"] + PROPER_TOL),
            "promotion_eligible": False,
        },
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "current_rule_change": False,
            "runtime_probability_change": False,
            "benchmark_2025_26_already_observed_before_architecture_creation": True,
            "benchmark_must_not_be_called_untouched_holdout": True,
            "benchmark_must_not_support_promotion": True,
            "future_independent_forward_gate_required": True,
            "automatic_promotion": False,
        },
    }


def main() -> int:
    payload = _run()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "chronology": payload["chronology"],
        "selected": payload["selected"],
        "development_benchmark": payload["development_benchmark"],
        "decision": payload["decision"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
