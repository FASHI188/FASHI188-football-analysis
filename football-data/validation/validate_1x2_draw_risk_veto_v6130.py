#!/usr/bin/env python3
"""Research-only strict time-forward DRAW-RISK VETO audit for selective 1X2.

The veto never rewrites a home/away pick into draw. It can only reject an otherwise
accepted V6.12.7-style high-confidence home/away selection when the de-vigged 1X2
structure says draw is an unusually large share of the remaining uncertainty.

For every annual test fold:
1) choose home/away confidence thresholds using only the pre-fold validation tail;
2) choose a draw-risk veto on that same pre-fold validation tail;
3) freeze both and evaluate the untouched next 12 months.

Historical odds lack original quote timestamps, therefore this is retrospective
research only (formal_weight=0) and cannot change CURRENT/runtime probabilities.
"""
from __future__ import annotations

import csv
import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
ENGINE = ROOT / "engine"
for p in (VALIDATION, ENGINE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import validate_1x2_nested_walkforward_v6127 as base
from diagnose_1x2_market_anchor_v697 import _extract_odds
from platform_core import parse_match_date

OUT = ROOT / "manifests" / "v6_1x2_draw_risk_veto_v6130_status.json"
FOLDS = base.FOLDS
VALIDATION_TAIL_FRACTION = base.VALIDATION_TAIL_FRACTION
MIN_RETENTION = 0.85
MIN_SELECTED = 150
MIN_HOME = 90
MIN_AWAY = 20
DRAW_FLOORS = (0.20, 0.22, 0.24, 0.26, 0.28, 0.30)
DRAW_SHARE_FLOORS = (0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75)
Z90 = base.Z90


def _d(value: str) -> date:
    return date.fromisoformat(value[:10])


def _actual(raw: dict[str, str]) -> str | None:
    ftr = str(raw.get("FTR") or raw.get("Result") or "").strip().upper()
    if ftr in {"H", "HOME"}:
        return "home"
    if ftr in {"D", "DRAW"}:
        return "draw"
    if ftr in {"A", "AWAY"}:
        return "away"
    try:
        hg = int(float(str(raw.get("FTHG") or raw.get("HG") or "")))
        ag = int(float(str(raw.get("FTAG") or raw.get("AG") or "")))
    except (TypeError, ValueError):
        return None
    return "home" if hg > ag else "away" if ag > hg else "draw"


def _read_rows() -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: list[dict[str, Any]] = []
    providers: dict[str, int] = defaultdict(int)
    processed = ROOT / "processed"
    for comp_dir in sorted(p for p in processed.iterdir() if p.is_dir()):
        cid = comp_dir.name
        for path in sorted(comp_dir.glob("*.csv")):
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row_index, raw0 in enumerate(csv.DictReader(handle)):
                    raw = {str(k): "" if v is None else str(v) for k, v in raw0.items() if k}
                    actual = _actual(raw)
                    extracted = _extract_odds(raw)
                    if actual is None or extracted is None:
                        continue
                    market, provider = extracted
                    season = str(raw.get("season") or raw.get("Season") or path.stem).strip()
                    date_raw = str(raw.get("Date") or "").strip()
                    if not date_raw:
                        continue
                    try:
                        date_iso = parse_match_date(date_raw, season).isoformat()
                    except Exception:
                        continue
                    pick = max(("home", "draw", "away"), key=lambda d: market[d])
                    pmax = float(market[pick])
                    pdraw = float(market["draw"])
                    residual = max(1e-12, 1.0 - pmax)
                    rows.append({
                        "competition_id": cid,
                        "season": season,
                        "date": date_iso,
                        "row_index": row_index,
                        "actual": actual,
                        "pick": pick,
                        "pmax": pmax,
                        "p_home": float(market["home"]),
                        "p_draw": pdraw,
                        "p_away": float(market["away"]),
                        "draw_share_of_residual": pdraw / residual,
                        "pick_draw_margin": pmax - pdraw,
                        "provider": provider,
                    })
                    providers[provider] += 1
    return rows, dict(providers)


def _veto(r: dict[str, Any], draw_floor: float | None, share_floor: float | None) -> bool:
    if draw_floor is None or share_floor is None:
        return False
    return float(r["p_draw"]) >= draw_floor and float(r["draw_share_of_residual"]) >= share_floor


def _eval(rows: list[dict[str, Any]], ht: float, at: float,
          draw_floor: float | None, share_floor: float | None) -> dict[str, Any]:
    base_selected = [r for r in rows if base._accept(r, ht, at)]
    selected = [r for r in base_selected if not _veto(r, draw_floor, share_floor)]
    hits = sum(1 for r in selected if r["pick"] == r["actual"])
    n = len(selected)
    errors = [r for r in selected if r["pick"] != r["actual"]]
    draw_errors = [r for r in errors if r["actual"] == "draw"]
    opposite_errors = [r for r in errors if r["actual"] in {"home", "away"}]
    removed = [r for r in base_selected if _veto(r, draw_floor, share_floor)]
    removed_draws = sum(1 for r in removed if r["actual"] == "draw")
    removed_correct = sum(1 for r in removed if r["pick"] == r["actual"])
    by_direction = {}
    for d in ("home", "away"):
        sub = [r for r in selected if r["pick"] == d]
        h = sum(1 for r in sub if r["pick"] == r["actual"])
        by_direction[d] = {"count": len(sub), "hits": h, "accuracy": h / len(sub) if sub else None}
    return {
        "count": n,
        "hits": hits,
        "accuracy": hits / n if n else None,
        "wilson90_lower": base._wilson_lower(hits, n),
        "base_selected_count": len(base_selected),
        "retention_vs_base": n / len(base_selected) if base_selected else 0.0,
        "coverage": n / len(rows) if rows else 0.0,
        "draw_floor": draw_floor,
        "draw_share_floor": share_floor,
        "errors": len(errors),
        "draw_errors": len(draw_errors),
        "opposite_errors": len(opposite_errors),
        "draw_error_share": len(draw_errors) / len(errors) if errors else 0.0,
        "removed_count": len(removed),
        "removed_actual_draws": removed_draws,
        "removed_correct_picks": removed_correct,
        "removed_draw_rate": removed_draws / len(removed) if removed else None,
        "by_direction": by_direction,
    }


def _choose_veto(validation: list[dict[str, Any]], ht: float, at: float) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    no_veto = _eval(validation, ht, at, None, None)
    no_veto["admissible"] = True
    no_veto["rule"] = "NO_VETO"
    candidates.append(no_veto)
    for df in DRAW_FLOORS:
        for sf in DRAW_SHARE_FLOORS:
            e = _eval(validation, ht, at, df, sf)
            e["rule"] = "DRAW_RISK_VETO"
            e["admissible"] = (
                e["wilson90_lower"] is not None
                and e["count"] >= MIN_SELECTED
                and e["retention_vs_base"] >= MIN_RETENTION
                and e["by_direction"]["home"]["count"] >= MIN_HOME
                and e["by_direction"]["away"]["count"] >= MIN_AWAY
            )
            candidates.append(e)
    admissible = [c for c in candidates if c["admissible"]]
    admissible.sort(
        key=lambda c: (c["wilson90_lower"], c["accuracy"], c["retention_vs_base"], c["count"]),
        reverse=True,
    )
    return admissible[0], candidates


def _selected_records(rows: list[dict[str, Any]], ht: float, at: float,
                      draw_floor: float | None, share_floor: float | None,
                      fold: str) -> list[dict[str, Any]]:
    out = []
    ordered = sorted(rows, key=lambda r: (r["date"], r["competition_id"], r["season"], r["row_index"]))
    for r in ordered:
        if not base._accept(r, ht, at) or _veto(r, draw_floor, share_floor):
            continue
        out.append({
            "fold": fold,
            "date": r["date"],
            "competition_id": r["competition_id"],
            "pick": r["pick"],
            "actual": r["actual"],
            "pmax": r["pmax"],
            "p_draw": r["p_draw"],
            "draw_share_of_residual": r["draw_share_of_residual"],
            "correct": r["pick"] == r["actual"],
        })
    return out


def _selected100(records: list[dict[str, Any]], step: int = 100) -> dict[str, Any]:
    blocks = []
    for start in range(0, len(records) - 100 + 1, step):
        chunk = records[start:start + 100]
        hits = sum(1 for r in chunk if r["correct"])
        draw_errors = sum(1 for r in chunk if not r["correct"] and r["actual"] == "draw")
        blocks.append({
            "start": start,
            "stop": start + 100,
            "first_date": chunk[0]["date"],
            "last_date": chunk[-1]["date"],
            "hits": hits,
            "accuracy": hits / 100.0,
            "draw_errors": draw_errors,
        })
    acc = [b["accuracy"] for b in blocks]
    return {
        "blocks": blocks,
        "summary": {
            "block_count": len(blocks),
            "worst_accuracy": min(acc) if acc else None,
            "q10_accuracy": sorted(acc)[max(0, math.ceil(0.10 * len(acc)) - 1)] if acc else None,
            "median_accuracy": statistics.median(acc) if acc else None,
            "mean_accuracy": statistics.mean(acc) if acc else None,
            "blocks_ge_70pct": sum(1 for x in acc if x >= 0.70),
            "blocks_lt_70pct": sum(1 for x in acc if x < 0.70),
            "blocks_lt_65pct": sum(1 for x in acc if x < 0.65),
        },
    }


def _aggregate(evals: list[dict[str, Any]]) -> dict[str, Any]:
    n = sum(int(e["count"]) for e in evals)
    hits = sum(int(e["hits"]) for e in evals)
    errors = sum(int(e["errors"]) for e in evals)
    draw_errors = sum(int(e["draw_errors"]) for e in evals)
    base_n = sum(int(e["base_selected_count"]) for e in evals)
    return {
        "count": n,
        "hits": hits,
        "accuracy": hits / n if n else None,
        "wilson90_lower": base._wilson_lower(hits, n),
        "retention_vs_base": n / base_n if base_n else 0.0,
        "errors": errors,
        "draw_errors": draw_errors,
        "draw_error_share": draw_errors / errors if errors else 0.0,
    }


def main() -> int:
    rows, providers = _read_rows()
    rows = sorted(rows, key=lambda r: (r["date"], r["competition_id"], r["season"], r["row_index"]))

    fold_results = []
    baseline_evals = []
    veto_evals = []
    baseline_records = []
    veto_records = []

    for start_s, end_s in FOLDS:
        start = date.fromisoformat(start_s)
        end = date.fromisoformat(end_s)
        history = [r for r in rows if _d(r["date"]) < start]
        test = [r for r in rows if start <= _d(r["date"]) < end]
        if len(history) < 500 or len(test) < 300:
            raise RuntimeError(f"insufficient rows for fold {start_s}: history={len(history)} test={len(test)}")

        tail_n = max(300, int(len(history) * VALIDATION_TAIL_FRACTION))
        validation = history[-tail_n:]
        threshold_rule, threshold_candidates = base._choose(validation)
        ht = float(threshold_rule["home_threshold"])
        at = float(threshold_rule["away_threshold"])

        veto_rule, veto_candidates = _choose_veto(validation, ht, at)
        df = veto_rule["draw_floor"]
        sf = veto_rule["draw_share_floor"]
        fold_name = f"{start_s}_to_{end_s}"

        baseline_test = _eval(test, ht, at, None, None)
        veto_test = _eval(test, ht, at, df, sf)
        baseline_evals.append(baseline_test)
        veto_evals.append(veto_test)
        baseline_records.extend(_selected_records(test, ht, at, None, None, fold_name))
        veto_records.extend(_selected_records(test, ht, at, df, sf, fold_name))

        fold_results.append({
            "fold": fold_name,
            "history_rows": len(history),
            "selection_validation_rows": len(validation),
            "test_rows": len(test),
            "selected_threshold_rule": threshold_rule,
            "threshold_admissible_count": sum(1 for c in threshold_candidates if c.get("admissible")),
            "selected_veto_rule": veto_rule,
            "veto_admissible_count": sum(1 for c in veto_candidates if c.get("admissible")),
            "test_without_veto": baseline_test,
            "test_with_veto": veto_test,
            "test_accuracy_uplift": (
                veto_test["accuracy"] - baseline_test["accuracy"]
                if veto_test["accuracy"] is not None and baseline_test["accuracy"] is not None else None
            ),
        })

    agg_base = _aggregate(baseline_evals)
    agg_veto = _aggregate(veto_evals)
    payload = {
        "schema_version": "V6.13.0-draw-risk-veto-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "RETROSPECTIVE_MARKET_RESEARCH_STRICT_TIME_FORWARD_SPLITS",
        "design": {
            "principle": "veto only; never rewrite home/away to draw",
            "folds": [{"start": s, "end": e} for s, e in FOLDS],
            "base_threshold_selection": "V6.12.7 pre-fold validation-tail procedure",
            "veto_features": ["de-vigged draw probability", "draw share of residual uncertainty"],
            "veto_rule": "reject when p_draw >= draw_floor AND p_draw/(1-pmax) >= draw_share_floor",
            "draw_floor_grid": list(DRAW_FLOORS),
            "draw_share_floor_grid": list(DRAW_SHARE_FLOORS),
            "minimum_validation_retention_vs_base": MIN_RETENTION,
            "test_year_never_used_for_threshold_or_veto_selection": True,
        },
        "provider_counts": providers,
        "fold_results": fold_results,
        "aggregate_without_veto": agg_base,
        "aggregate_with_veto": agg_veto,
        "aggregate_accuracy_uplift": (
            agg_veto["accuracy"] - agg_base["accuracy"]
            if agg_veto["accuracy"] is not None and agg_base["accuracy"] is not None else None
        ),
        "draw_error_reduction": agg_base["draw_errors"] - agg_veto["draw_errors"],
        "selected100_nonoverlap_without_veto": _selected100(baseline_records, 100),
        "selected100_nonoverlap_with_veto": _selected100(veto_records, 100),
        "selected100_rolling50_without_veto": _selected100(baseline_records, 50),
        "selected100_rolling50_with_veto": _selected100(veto_records, 50),
        "interpretation_gate": {
            "candidate_is_useful_only_if": [
                "aggregate accuracy improves on untouched folds",
                "at least three of four annual folds do not deteriorate materially",
                "retention remains >=85%",
                "selected-100 tail stability does not worsen materially"
            ]
        },
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "historical_market_quotes_lack_original_timestamp": True,
            "no_draw_override": True,
            "formal_probability_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "automatic_promotion": False,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "fold_results": fold_results,
        "aggregate_without_veto": agg_base,
        "aggregate_with_veto": agg_veto,
        "aggregate_accuracy_uplift": payload["aggregate_accuracy_uplift"],
        "draw_error_reduction": payload["draw_error_reduction"],
        "selected100_nonoverlap_without_veto": payload["selected100_nonoverlap_without_veto"]["summary"],
        "selected100_nonoverlap_with_veto": payload["selected100_nonoverlap_with_veto"]["summary"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
