#!/usr/bin/env python3
"""V6.25.17 compact opportunity quantity-quality ordinal challenger.

Research/development only; formal_weight=0.

V6.25.15 showed that blindly concatenating 37 xG and shot-state features dilutes
exact-total discrimination. This challenger instead compresses the pre-match
information into a small set of interpretable opportunity-structure variables:

- expected total xG and non-penalty xG;
- expected total shot and shot-on-target volume;
- xG per expected shot and xG per expected SOT;
- npxG per expected shot;
- home-v-away xG-per-shot quality imbalance;
- recent total shot and SOT tempo versus season baselines.

All quantities are computed strictly before the match from the immutable V6.18.9
xG state panel and V6.25.3/V6.25.7 date-before-match shot histories. The target
architecture remains the seven-threshold ordinal residual model from V6.25.13.

Governance: 2025/26 was already observed earlier in the research program, so it
is only a reused development benchmark and cannot support promotion.
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

core.true = True
core.false = False

OUT = ROOT / "manifests" / "v6_total_compact_quantity_quality_v62517_status.json"
EPS = 1e-9
L2_GRID = core.L2_GRID
ALPHA_GRID = core.ALPHA_GRID
PROPER_TOL = core.PROPER_TOL


def _panel_state_lookup() -> dict[tuple[str, str, str, str, str], dict[str, Any]]:
    lookup, _ = core._load_panel()
    return lookup


def _compact_features(
    row: dict[str, Any],
    panel_record: dict[str, Any],
    history: list[Any],
) -> list[float] | None:
    if len(history) < 20:
        return None
    home = panel_record["home_prematch_state"]["v"]
    away = panel_record["away_prematch_state"]["v"]

    league_shots = sum(r.hs + r.as_ for r in history) / max(EPS, 2.0 * len(history))
    league_sot = sum(r.hst + r.ast for r in history) / max(EPS, 2.0 * len(history))
    if league_shots <= 0.0 or league_sot <= 0.0:
        return None
    hr = shot._team_rates(history, str(row["home_team"]), league_shots, league_sot)
    ar = shot._team_rates(history, str(row["away_team"]), league_shots, league_sot)
    if hr["n"] < 2 or ar["n"] < 2:
        return None

    home_shots = 0.5 * (float(hr["sf"]) + float(ar["sa"]))
    away_shots = 0.5 * (float(ar["sf"]) + float(hr["sa"]))
    home_sot = 0.5 * (float(hr["sotf"]) + float(ar["sota"]))
    away_sot = 0.5 * (float(ar["sotf"]) + float(hr["sota"]))

    home_xg = 0.5 * (float(home["xg"]) + float(away["xga"]))
    away_xg = 0.5 * (float(away["xg"]) + float(home["xga"]))
    home_npxg = 0.5 * (float(home["npxg"]) + float(away["npxga"]))
    away_npxg = 0.5 * (float(away["npxg"]) + float(home["npxga"]))

    total_shots = max(EPS, home_shots + away_shots)
    total_sot = max(EPS, home_sot + away_sot)
    total_xg = max(EPS, home_xg + away_xg)
    total_npxg = max(EPS, home_npxg + away_npxg)
    xg_per_shot = total_xg / total_shots
    npxg_per_shot = total_npxg / total_shots
    xg_per_sot = total_xg / total_sot
    home_quality = max(EPS, home_xg) / max(EPS, home_shots)
    away_quality = max(EPS, away_xg) / max(EPS, away_shots)

    dyn = dynamic._dynamic_state(history, str(row["home_team"]), str(row["away_team"]))
    recent_shot_tempo = float(dyn[-2]) if len(dyn) >= 2 else 0.0
    recent_sot_tempo = float(dyn[-1]) if len(dyn) >= 1 else 0.0

    return [
        1.0,
        math.log(total_xg),
        math.log(total_npxg),
        math.log(total_shots),
        math.log(total_sot),
        math.log(max(EPS, xg_per_shot)),
        math.log(max(EPS, npxg_per_shot)),
        math.log(max(EPS, xg_per_sot)),
        abs(math.log(max(EPS, home_quality) / max(EPS, away_quality))),
        recent_shot_tempo,
        recent_sot_tempo,
    ]


def _build_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    xg_rows, xg_audit = core._strict_rows_with_xg()
    panel = _panel_state_lookup()
    domains = sorted({str(r["competition_id"]) for r in xg_rows})
    stats_by_domain: dict[str, list[Any]] = {}
    stat_meta: dict[str, Any] = {}
    for cid in domains:
        stats, meta = shot._read_stat_rows(cid)
        stats_by_domain[cid] = stats
        stat_meta[cid] = meta

    out: list[dict[str, Any]] = []
    missing = Counter()
    for row in xg_rows:
        cid = str(row["competition_id"])
        day = str(row["date"])
        pkey = (cid, str(row["season"]), day, str(row["home_team"]), str(row["away_team"]))
        prec = panel.get(pkey)
        if prec is None:
            missing[f"{cid}:panel"] += 1
            continue
        cutoff = datetime.fromisoformat(day + "T00:00:00+00:00")
        history = shot._stat_history(stats_by_domain[cid], str(row["season"]), cutoff)
        features = _compact_features(row, prec, history)
        if features is None:
            missing[f"{cid}:shot"] += 1
            continue
        item = dict(row)
        item["xg_x"] = features
        out.append(item)

    return out, {
        "xg_attachment": xg_audit,
        "input_xg_rows": len(xg_rows),
        "combined_rows": len(out),
        "combined_attach_rate": len(out) / len(xg_rows) if xg_rows else 0.0,
        "missing": dict(sorted(missing.items())),
        "feature_dimension": len(out[0]["xg_x"]) if out else 0,
        "shot_source_coverage": stat_meta,
    }


def _run() -> dict[str, Any]:
    rows, attach = _build_rows()
    train = [r for r in rows if r["season"] in core.TRAIN_SEASONS]
    valid = [r for r in rows if r["season"] == core.VALID_SEASON]
    benchmark = [r for r in rows if r["season"] == core.HOLDOUT_SEASON]
    if min(len(train), len(valid), len(benchmark)) < 850:
        raise RuntimeError(f"insufficient rows train={len(train)} valid={len(valid)} benchmark={len(benchmark)}")

    valid_base = core._summary(core._evaluate(valid, None, 0.0))
    leaderboard: list[dict[str, Any]] = [{"l2": None, "alpha": 0.0, **valid_base, "proper_nonworse": True}]
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
    final_models = None
    if float(selected["alpha"]) > 0.0:
        final_models = core._fit_models(train + valid, float(selected["l2"]))

    base = core._summary(core._evaluate(benchmark, None, 0.0))
    candidate = core._summary(core._evaluate(benchmark, final_models, float(selected["alpha"])))
    per_domain: dict[str, Any] = {}
    for cid in sorted({str(r["competition_id"]) for r in benchmark}):
        subset = [r for r in benchmark if str(r["competition_id"]) == cid]
        b = core._summary(core._evaluate(subset, None, 0.0))
        c = core._summary(core._evaluate(subset, final_models, float(selected["alpha"])))
        per_domain[cid] = {"baseline": b, "candidate": c, "delta": core._delta(b, c)}

    return {
        "schema_version": "V6.25.17-compact-quantity-quality-ordinal-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "DEVELOPMENT_COMPACT_QUANTITY_QUALITY_FORMAL_WEIGHT_0",
        "chronology": {
            "fit_seasons": sorted(core.TRAIN_SEASONS),
            "validation_season": core.VALID_SEASON,
            "reused_development_benchmark_season": core.HOLDOUT_SEASON,
            "fit_count": len(train),
            "validation_count": len(valid),
            "benchmark_count": len(benchmark),
        },
        "feature_contract": {
            "dimension": attach["feature_dimension"],
            "variables": [
                "intercept", "log_total_xg", "log_total_npxg", "log_total_shots", "log_total_sot",
                "log_xg_per_shot", "log_npxg_per_shot", "log_xg_per_sot",
                "abs_home_away_xg_per_shot_log_ratio", "recent_shot_tempo", "recent_sot_tempo"
            ],
            "xg_source": "immutable V6.18.9 pre-match state panel",
            "shot_source": "strict date-before-match current-season histories",
            "same_day_excluded": True,
            "postmatch_target_features": False
        },
        "attachment_audit": attach,
        "validation_baseline": valid_base,
        "validation_leaderboard": leaderboard,
        "selected": selected,
        "development_benchmark": {
            "baseline": base,
            "candidate": candidate,
            "delta": core._delta(base, candidate),
            "per_domain": per_domain
        },
        "decision": {
            "candidate_nonzero": bool(float(selected["alpha"]) > 0.0),
            "validation_proper_gate_pass": bool(selected["proper_nonworse"]),
            "benchmark_exact_top1_improved": bool(candidate["total_top1_accuracy"] > base["total_top1_accuracy"]),
            "benchmark_rps_nonworse": bool(candidate["total_mean_rps"] <= base["total_mean_rps"] + PROPER_TOL),
            "benchmark_logloss_nonworse": bool(candidate["total_log_loss"] <= base["total_log_loss"] + PROPER_TOL),
            "promotion_eligible": False
        },
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "current_rule_change": False,
            "runtime_probability_change": False,
            "2025_26_is_reused_development_benchmark": True,
            "promotion_from_this_benchmark_forbidden": True,
            "future_independent_forward_gate_required": True
        }
    }


def main() -> int:
    payload = _run()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "selected": payload["selected"],
        "development_benchmark": payload["development_benchmark"],
        "decision": payload["decision"]
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
