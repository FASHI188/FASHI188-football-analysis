#!/usr/bin/env python3
"""V6.7.0 draw-residual override challenger.

Goal: fix *decision* errors caused by systematically under-called draws without rewriting
market probabilities. The synchronized de-vigged 1X2 market remains the probability source.
A binary specialist estimates only whether a non-draw market Top-1 is likely to be wrong
specifically because the match finishes level. It may override Home/Away -> Draw only when a
threshold selected on 2024/25 improves validation accuracy. 2025/26 is holdout only.

Fit: 2022/23 + 2023/24
Select: 2024/25
Holdout: 2025/26
Research only. No CURRENT/formal/runtime mutation.
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
ENGINE = ROOT / "engine"
for p in (VALIDATION, ENGINE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import v6_direct_outcome_mvp_v600 as base
import v6_market_residual_fusion_v620 as mkt
import v6_multimarket_draw_side_v643 as mm
from platform_core import PlatformError

OUT = ROOT / "manifests" / "v6_draw_residual_override_v670_status.json"
SEASONS = ("2022/23", "2023/24", "2024/25", "2025/26")
SEASON_CODES = {"2022/23": "2223", "2023/24": "2324", "2024/25": "2425", "2025/26": "2526"}
L2_GRID = (1.0, 10.0, 100.0, 1000.0)
THRESHOLDS = tuple(round(0.25 + i * 0.01, 2) for i in range(36))  # .25 .. .60
MIN_OVERRIDES_VALIDATION = 40
EPS = 1e-12


def _logit(p: float) -> float:
    p = min(1 - 1e-8, max(1e-8, p))
    return math.log(p / (1 - p))


def _augment(row: dict) -> dict:
    s = row["surface"]
    q = s["one"]
    pick = max(("home", "draw", "away"), key=lambda k: q[k])
    side_pick = "home" if q["home"] >= q["away"] else "away"
    favorite = max(q["home"], q["away"])
    side_gap = abs(q["home"] - q["away"])
    top_gap = q[pick] - max(q[k] for k in ("home", "draw", "away") if k != pick)
    under_edge = s["under_prob"] - 0.5
    ah_balance = abs(s["ah_home_prob"] - 0.5)
    ah_market_side = "home" if s["ah_home_prob"] >= 0.5 else "away"
    side_disagree = 1.0 if ah_market_side != side_pick else 0.0
    closeness = 1.0 - side_gap
    x = [
        1.0,
        _logit(q["draw"]),
        q["draw"],
        favorite,
        side_gap,
        top_gap,
        under_edge,
        abs(s["ah_line"]),
        ah_balance,
        side_disagree,
        q["draw"] * s["under_prob"],
        closeness * s["under_prob"],
    ]
    z = dict(row)
    z["market_pick"] = pick
    z["draw_override_x"] = x
    z["draw_y"] = 1 if row["actual_result"] == "draw" else 0
    return z


def _build() -> tuple[dict[str, list[dict]], dict]:
    by_season = {s: [] for s in SEASONS}
    audit = {}
    for cid, code in mm.LEAGUES.items():
        model = mkt._build_domain_rows_with_identity(cid, list(SEASONS))
        audit[cid] = {}
        for season in SEASONS:
            raw, url = mkt._download_csv(code, SEASON_CODES[season])
            matched, stats = mm.match_rows(cid, model[season], raw)
            rows = [_augment(r) for r in matched]
            by_season[season].extend(rows)
            audit[cid][season] = {
                "url": url,
                "model_rows": len(model[season]),
                "csv_rows": len(raw),
                "matched": len(rows),
                "stats": stats,
            }
    return by_season, audit


def _fit(rows: list[dict], l2: float):
    usable = [r for r in rows if r["market_pick"] != "draw"]
    return base._fit_binary(usable, "draw_override_x", "draw_y", l2)


def _score(rows: list[dict], model=None, threshold: float | None = None) -> dict:
    total = hits = 0
    market_hits = 0
    overrides = draw_hits = original_correct_lost = 0
    original_wrong_draw_captured = 0
    pred = Counter()
    actual = Counter()
    for r in rows:
        market_pick = r["market_pick"]
        truth = r["actual_result"]
        market_hit = int(market_pick == truth)
        market_hits += market_hit
        pick = market_pick
        if model is not None and threshold is not None and market_pick != "draw":
            p_draw_err = base._predict_binary(model, r["draw_override_x"])
            if p_draw_err >= threshold:
                overrides += 1
                pick = "draw"
                if truth == "draw":
                    draw_hits += 1
                    if not market_hit:
                        original_wrong_draw_captured += 1
                elif market_hit:
                    original_correct_lost += 1
        hit = int(pick == truth)
        total += 1
        hits += hit
        pred[pick] += 1
        actual[truth] += 1
    draw_pred = pred["draw"]
    actual_draw = actual["draw"]
    return {
        "count": total,
        "hits": hits,
        "accuracy": hits / total if total else None,
        "market_hits": market_hits,
        "market_accuracy": market_hits / total if total else None,
        "accuracy_gain_pp": 100 * (hits - market_hits) / total if total else None,
        "override_count": overrides,
        "override_rate": overrides / total if total else None,
        "draw_prediction_count": draw_pred,
        "draw_hits": draw_hits,
        "draw_precision": draw_hits / draw_pred if draw_pred else None,
        "draw_recall": draw_hits / actual_draw if actual_draw else None,
        "actual_draw_count": actual_draw,
        "original_wrong_draws_captured": original_wrong_draw_captured,
        "original_correct_picks_lost": original_correct_lost,
        "paired_net_hits": original_wrong_draw_captured - original_correct_lost,
        "predicted_direction_counts": dict(pred),
        "actual_direction_counts": dict(actual),
    }


def main() -> int:
    by_season, source_audit = _build()
    fit_rows = by_season["2022/23"] + by_season["2023/24"]
    validation = by_season["2024/25"]
    holdout = by_season["2025/26"]
    if min(len(fit_rows), len(validation), len(holdout)) < 700:
        raise PlatformError(f"insufficient rows: {len(fit_rows)}/{len(validation)}/{len(holdout)}")

    baseline_validation = _score(validation)
    baseline_holdout = _score(holdout)
    candidates = []
    for l2 in L2_GRID:
        model = _fit(fit_rows, l2)
        for threshold in THRESHOLDS:
            sc = _score(validation, model, threshold)
            eligible = sc["override_count"] >= MIN_OVERRIDES_VALIDATION
            candidates.append({"l2": l2, "threshold": threshold, "eligible": eligible, "validation": sc})

    eligible = [c for c in candidates if c["eligible"]]
    eligible.sort(
        key=lambda c: (
            -c["validation"]["accuracy"],
            -c["validation"]["paired_net_hits"],
            -(c["validation"]["draw_precision"] or 0.0),
            c["validation"]["override_count"],
            c["l2"],
            c["threshold"],
        )
    )
    selected = eligible[0] if eligible else None
    validation_gain = (selected or {}).get("validation", {}).get("accuracy_gain_pp") if selected else None

    holdout_result = None
    gate = False
    if selected is not None and validation_gain is not None and validation_gain > 0:
        refit = _fit(fit_rows + validation, float(selected["l2"]))
        holdout_result = _score(holdout, refit, float(selected["threshold"]))
        gate = bool(
            holdout_result["accuracy_gain_pp"] > 0
            and holdout_result["paired_net_hits"] > 0
            and holdout_result["override_count"] >= 40
        )

    out = {
        "schema_version": "V6.7.0-draw-residual-override-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "design": {
            "probability_source_unchanged": "de-vigged synchronized closing 1X2",
            "specialist_role": "decision-only Home/Away -> Draw override",
            "fit_seasons": ["2022/23", "2023/24"],
            "selection_season": "2024/25",
            "holdout_season": "2025/26",
            "holdout_used_for_selection": False,
            "probabilities_rewritten": False,
            "proper_scores_changed_by_design": False,
            "minimum_validation_overrides": MIN_OVERRIDES_VALIDATION,
        },
        "row_counts": {"fit": len(fit_rows), "validation": len(validation), "holdout": len(holdout)},
        "source_audit": source_audit,
        "baseline_validation": baseline_validation,
        "selected_candidate": selected,
        "baseline_holdout": baseline_holdout,
        "holdout_result": holdout_result,
        "research_gate_passed": gate,
        "interpretation": (
            "PASS_DRAW_OVERRIDE_SIGNAL" if gate else
            "REJECT_NO_STABLE_INCREMENTAL_DRAW_OVERRIDE"
        ),
        "governance": {
            "research_only": True,
            "automatic_promotion": False,
            "fresh_forward_confirmation_required": True,
            "formal_weight_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
        },
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
