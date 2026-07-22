#!/usr/bin/env python3
"""V6.2.2 opening-to-closing market-movement residual challenge.

Tests whether the path from early/opening market probabilities to closing market probabilities
contains incremental 1X2 signal beyond the closing endpoint itself. Uses Football-Data's first
and C-suffixed closing 1X2 average prices. Fit: 2022/23-2023/24, select: 2024/25,
holdout: 2025/26. Research only; no CURRENT/runtime mutation.
"""
from __future__ import annotations

import csv
import difflib
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for path in (ENGINE, VALIDATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import v6_direct_outcome_mvp_v600 as base
import v6_direct_outcome_draw_boundary_v601 as v601
import v6_market_residual_fusion_v620 as v620
import v6_market_offset_residual_v621 as v621
from draw_recalibration_kl_v5535_r2 import _completed_outer_seasons_last_complete_only
from platform_core import PlatformError, atomic_write_json, canonical_team_name, load_json, normalize_team_token

OUT = ROOT / "manifests" / "v6_market_movement_residual_v622_status.json"
SEASON_CODES = {"2022/23": "2223", "2023/24": "2324", "2024/25": "2425", "2025/26": "2526"}
L2_GRID = (0.1, 1.0, 10.0, 100.0, 1000.0)
DRAW_RATIO_GRID = (0.75, 0.80, 0.85, 0.90, 0.95, 1.00)
EPS = 1e-12


def _de_vig(odds: tuple[float | None, float | None, float | None]) -> dict[str, float] | None:
    if not all(v is not None and float(v) > 1.0 for v in odds):
        return None
    inv = [1.0 / float(v) for v in odds]
    total = sum(inv)
    return {"home": inv[0] / total, "draw": inv[1] / total, "away": inv[2] / total}


def _opening_market(row: dict[str, str]) -> tuple[dict[str, float] | None, str | None]:
    families = (
        ("AvgH", "AvgD", "AvgA", "average_first_snapshot"),
        ("B365H", "B365D", "B365A", "bet365_first_snapshot"),
        ("MaxH", "MaxD", "MaxA", "maximum_first_snapshot"),
    )
    for hk, dk, ak, label in families:
        p = _de_vig((v620._float(row, hk), v620._float(row, dk), v620._float(row, ak)))
        if p is not None:
            return p, label
    return None, None


def _same_team(a: str, b: str) -> bool:
    return normalize_team_token(a) == normalize_team_token(b)


def _match_with_movement(cid: str, model_rows: list[dict[str, Any]], market_rows: list[dict[str, str]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in model_rows:
        by_date[row["date"]].append(row)
    used: set[int] = set()
    output: list[dict[str, Any]] = []
    stats: Counter = Counter()
    for raw in market_rows:
        try:
            close_p, close_family = v620._closing_market(raw)
            open_p, open_family = _opening_market(raw)
            if close_p is None:
                stats["missing_closing"] += 1
                continue
            if open_p is None:
                stats["missing_opening"] += 1
                continue
            date = v620._parse_date(str(raw.get("Date") or ""))
            candidates = by_date.get(date, [])
            if not candidates:
                stats["date_unmatched"] += 1
                continue
            home_raw = str(raw.get("HomeTeam") or "").strip()
            away_raw = str(raw.get("AwayTeam") or "").strip()
            try:
                home = canonical_team_name(cid, home_raw)
                away = canonical_team_name(cid, away_raw)
            except Exception:
                home, away = home_raw, away_raw
            exact = [r for r in candidates if _same_team(r["home_team"], home) and _same_team(r["away_team"], away)]
            chosen = exact[0] if len(exact) == 1 else None
            if chosen is None:
                ranked: list[tuple[float, dict[str, Any]]] = []
                for candidate in candidates:
                    hs = difflib.SequenceMatcher(None, normalize_team_token(candidate["home_team"]), normalize_team_token(home)).ratio()
                    aas = difflib.SequenceMatcher(None, normalize_team_token(candidate["away_team"]), normalize_team_token(away)).ratio()
                    ranked.append(((hs + aas) / 2.0, candidate))
                ranked.sort(key=lambda pair: pair[0], reverse=True)
                if ranked and ranked[0][0] >= 0.82 and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= 0.08):
                    chosen = ranked[0][1]
                    stats["fuzzy_unique_match"] += 1
            if chosen is None:
                stats["identity_unmatched"] += 1
                continue
            marker = id(chosen)
            if marker in used:
                stats["duplicate_model_match"] += 1
                continue
            used.add(marker)
            item = dict(chosen)
            item["market"] = close_p
            item["opening_market"] = open_p
            item["closing_family"] = close_family
            item["opening_family"] = open_family
            _prepare_movement_features(item)
            output.append(item)
            stats["matched"] += 1
            stats[f"close_{close_family}"] += 1
            stats[f"open_{open_family}"] += 1
        except Exception:
            stats["row_rejected"] += 1
    return output, dict(sorted(stats.items()))


def _safe_log_ratio(a: float, b: float) -> float:
    return math.log(max(EPS, a) / max(EPS, b))


def _prepare_movement_features(row: dict[str, Any]) -> None:
    op = row["opening_market"]
    cp = row["market"]
    close_side = float(cp["home"]) / max(EPS, float(cp["home"]) + float(cp["away"]))
    open_side = float(op["home"]) / max(EPS, float(op["home"]) + float(op["away"]))
    move_h = float(cp["home"]) - float(op["home"])
    move_d = float(cp["draw"]) - float(op["draw"])
    move_a = float(cp["away"]) - float(op["away"])
    total_move = abs(move_h) + abs(move_d) + abs(move_a)
    row["draw_move_x"] = [
        1.0,
        move_d,
        move_h - move_a,
        total_move,
        _safe_log_ratio(float(cp["draw"]), float(op["draw"])),
        max(float(cp["home"]), float(cp["away"])) - float(cp["draw"]),
        max(float(op["home"]), float(op["away"])) - float(op["draw"]),
    ]
    row["side_move_x"] = [
        1.0,
        close_side - open_side,
        abs(close_side - open_side),
        total_move,
        _safe_log_ratio(close_side, open_side),
        float(cp["draw"]) - float(op["draw"]),
    ]
    row["draw_offset"] = v621._logit(float(cp["draw"]))
    row["side_offset"] = v621._logit(close_side)


def _fit_models(rows: list[dict[str, Any]], l2: float) -> dict[str, Any]:
    draw = v621._fit_offset_binary(rows, "draw_move_x", "draw_y", "draw_offset", l2)
    decisive = [r for r in rows if r["is_decisive"]]
    side = v621._fit_offset_binary(decisive, "side_move_x", "side_y", "side_offset", l2)
    return {"draw_model": draw, "side_model": side, "l2": l2}


def _prob(row: dict[str, Any], model: dict[str, Any]) -> dict[str, float]:
    pd = v621._clip(v621._predict_offset(model["draw_model"], row, "draw_move_x", "draw_offset"))
    ph = v621._clip(v621._predict_offset(model["side_model"], row, "side_move_x", "side_offset"))
    rem = 1.0 - pd
    return {"home": rem * ph, "draw": pd, "away": rem * (1.0 - ph)}


def _score(rows: list[dict[str, Any]], key: str, draw_ratio: float) -> dict[str, Any]:
    return v621._score(rows, key, draw_ratio)


def main() -> int:
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    formal_status = load_json(base.FORMAL_STATUS)
    if len((formal_status.get("reports") or {})) != 17:
        raise PlatformError("formal domain registry must contain 17 domains")

    all_rows: dict[str, dict[str, list[dict[str, Any]]]] = {}
    source_audit: dict[str, Any] = {}
    failures: dict[str, str] = {}
    for cid, code in v620.LEAGUES.items():
        try:
            report = load_json(base.REPORT_ROOT / f"{cid}.json")
            seasons = _completed_outer_seasons_last_complete_only(report)[-4:]
            if seasons != ["2022/23", "2023/24", "2024/25", "2025/26"]:
                raise PlatformError(f"unexpected seasons for {cid}: {seasons}")
            built = v620._build_domain_rows_with_identity(cid, seasons)
            all_rows[cid] = {}
            source_audit[cid] = {}
            for season in seasons:
                csv_rows, url = v620._download_csv(code, SEASON_CODES[season])
                matched, stats = _match_with_movement(cid, built[season], csv_rows)
                all_rows[cid][season] = matched
                source_audit[cid][season] = {
                    "url": url,
                    "csv_rows": len(csv_rows),
                    "model_rows": len(built[season]),
                    "matched": len(matched),
                    "match_stats": stats,
                }
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"
    if failures:
        payload = {"schema_version": "V6.2.2-market-movement-residual-r1", "generated_at_utc": generated.isoformat(), "status": "FAIL_DATA", "failures": failures}
        atomic_write_json(OUT, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    fit: list[dict[str, Any]] = []
    validation: list[dict[str, Any]] = []
    holdout: list[dict[str, Any]] = []
    for cid in v620.LEAGUES:
        fit.extend(all_rows[cid]["2022/23"])
        fit.extend(all_rows[cid]["2023/24"])
        validation.extend(all_rows[cid]["2024/25"])
        holdout.extend(all_rows[cid]["2025/26"])
    for rows in (fit, validation, holdout):
        for row in rows:
            row["market_probability"] = row["market"]

    market_validation_by_ratio = [{"draw_ratio": dr, "score": _score(validation, "market_probability", dr)} for dr in DRAW_RATIO_GRID]
    market_validation_by_ratio.sort(key=lambda x: (-float(x["score"]["accuracy"]), float(x["score"]["mean_log_loss"]), float(x["draw_ratio"])))
    market_selected_ratio = float(market_validation_by_ratio[0]["draw_ratio"])
    market_validation = market_validation_by_ratio[0]["score"]
    market_holdout = _score(holdout, "market_probability", market_selected_ratio)

    candidates: list[dict[str, Any]] = []
    for l2 in L2_GRID:
        try:
            model = _fit_models(fit, l2)
            work: list[dict[str, Any]] = []
            for row in validation:
                item = dict(row)
                item["movement_probability"] = _prob(row, model)
                work.append(item)
            for dr in DRAW_RATIO_GRID:
                score = _score(work, "movement_probability", dr)
                proper_safe = (
                    float(score["mean_brier"]) <= float(market_validation["mean_brier"]) + 1e-12
                    and float(score["mean_rps"]) <= float(market_validation["mean_rps"]) + 1e-12
                    and float(score["mean_log_loss"]) <= float(market_validation["mean_log_loss"]) + 1e-12
                )
                candidates.append({"l2": l2, "draw_ratio": dr, "validation": score, "proper_scores_nonworse_than_market": proper_safe})
        except Exception as exc:
            candidates.append({"l2": l2, "status": "FAILED", "error": f"{type(exc).__name__}: {exc}", "proper_scores_nonworse_than_market": False})

    eligible = [c for c in candidates if c.get("proper_scores_nonworse_than_market") and c.get("validation")]
    if eligible:
        eligible.sort(key=lambda c: (-float(c["validation"]["accuracy"]), float(c["validation"]["mean_log_loss"]), float(c["l2"]), float(c["draw_ratio"])))
        selected = eligible[0]
        refit = _fit_models(fit + validation, float(selected["l2"]))
        hwork: list[dict[str, Any]] = []
        for row in holdout:
            item = dict(row)
            item["movement_probability"] = _prob(row, refit)
            hwork.append(item)
        residual_holdout = _score(hwork, "movement_probability", float(selected["draw_ratio"]))
        result_status = "PASS"
    else:
        selected = None
        refit = None
        residual_holdout = None
        result_status = "NO_PROPER_SCORE_SAFE_MOVEMENT_RESIDUAL"

    result: dict[str, Any] = {
        "status": result_status,
        "market_selected_draw_ratio": market_selected_ratio,
        "market_validation": market_validation,
        "market_holdout": market_holdout,
        "selected_candidate": selected,
        "movement_holdout": residual_holdout,
        "refit_audit": refit,
    }
    if residual_holdout is not None:
        result["accuracy_gain_pp_vs_market"] = 100.0 * (float(residual_holdout["accuracy"]) - float(market_holdout["accuracy"]))
        result["proper_score_delta_vs_market"] = {
            "brier": float(residual_holdout["mean_brier"]) - float(market_holdout["mean_brier"]),
            "rps": float(residual_holdout["mean_rps"]) - float(market_holdout["mean_rps"]),
            "log_loss": float(residual_holdout["mean_log_loss"]) - float(market_holdout["mean_log_loss"]),
        }

    payload = {
        "schema_version": "V6.2.2-market-movement-residual-r1",
        "generated_at_utc": generated.isoformat(),
        "status": "PASS",
        "scope": {
            "competitions": list(v620.LEAGUES),
            "fit_seasons": ["2022/23", "2023/24"],
            "selection_validation_season": "2024/25",
            "development_holdout_season": "2025/26",
            "baseline": "de-vigged average closing 1X2",
            "incremental_features": "first-snapshot to closing probability movement only",
            "l2_grid": list(L2_GRID),
            "draw_ratio_grid": list(DRAW_RATIO_GRID),
        },
        "row_counts": {"fit": len(fit), "validation": len(validation), "holdout": len(holdout)},
        "source_audit": source_audit,
        "result": result,
        "governance": {
            "research_challenge_only": True,
            "holdout_used_for_selection": False,
            "closing_market_remains_offset": True,
            "formal_weight_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "automatic_promotion": False,
            "v610_v613_pristine_forward_untouched": True,
        },
    }
    atomic_write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
