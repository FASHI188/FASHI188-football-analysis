#!/usr/bin/env python3
"""V6.2.3 rolling match-quality residual challenge.

Tests whether pre-match rolling performance signals from Football-Data match statistics
(shots, shots on target, corners, goals and cards) add signal beyond the de-vigged closing
1X2 market. Current-match statistics are never used in its own prediction: features are read
from team state before the row is updated. Fit 2022/23-2023/24, select 2024/25, evaluate
2025/26 once. Research only; no CURRENT/runtime mutation.
"""
from __future__ import annotations

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
import v6_market_residual_fusion_v620 as v620
import v6_market_offset_residual_v621 as v621
from draw_recalibration_kl_v5535_r2 import _completed_outer_seasons_last_complete_only
from platform_core import PlatformError, atomic_write_json, canonical_team_name, load_json, normalize_team_token

OUT = ROOT / "manifests" / "v6_market_form_residual_v623_status.json"
SEASON_CODES = {"2022/23": "2223", "2023/24": "2324", "2024/25": "2425", "2025/26": "2526"}
HALF_LIFE_GRID = (5.0, 10.0, 20.0)
L2_GRID = (1.0, 10.0, 100.0, 1000.0)
DRAW_RATIO_GRID = (0.80, 0.85, 0.90, 0.95, 1.00)
EPS = 1e-12
STAT_KEYS = ("shots_for", "shots_against", "sot_for", "sot_against", "corners_for", "corners_against", "goals_for", "goals_against", "yellow", "red")


def _num(row: dict[str, str], key: str) -> float | None:
    value = str(row.get(key) or "").strip()
    if value == "":
        return None
    try:
        x = float(value)
    except ValueError:
        return None
    return x if math.isfinite(x) else None


def _match_stats(raw: dict[str, str]) -> tuple[dict[str, float], dict[str, float]] | None:
    values = {k: _num(raw, k) for k in ("HS", "AS", "HST", "AST", "HC", "AC", "FTHG", "FTAG", "HY", "AY", "HR", "AR")}
    required = ("HS", "AS", "HST", "AST", "HC", "AC", "FTHG", "FTAG")
    if any(values[k] is None for k in required):
        return None
    home = {
        "shots_for": float(values["HS"]), "shots_against": float(values["AS"]),
        "sot_for": float(values["HST"]), "sot_against": float(values["AST"]),
        "corners_for": float(values["HC"]), "corners_against": float(values["AC"]),
        "goals_for": float(values["FTHG"]), "goals_against": float(values["FTAG"]),
        "yellow": float(values["HY"] or 0.0), "red": float(values["HR"] or 0.0),
    }
    away = {
        "shots_for": float(values["AS"]), "shots_against": float(values["HS"]),
        "sot_for": float(values["AST"]), "sot_against": float(values["HST"]),
        "corners_for": float(values["AC"]), "corners_against": float(values["HC"]),
        "goals_for": float(values["FTAG"]), "goals_against": float(values["FTHG"]),
        "yellow": float(values["AY"] or 0.0), "red": float(values["AR"] or 0.0),
    }
    return home, away


class EWState:
    def __init__(self) -> None:
        self.n = 0
        self.v = {k: 0.0 for k in STAT_KEYS}

    def update(self, obs: dict[str, float], alpha: float) -> None:
        if self.n == 0:
            self.v = {k: float(obs[k]) for k in STAT_KEYS}
        else:
            for k in STAT_KEYS:
                self.v[k] = (1.0 - alpha) * self.v[k] + alpha * float(obs[k])
        self.n += 1


def _team_key(cid: str, name: str) -> str:
    try:
        name = canonical_team_name(cid, name)
    except Exception:
        pass
    return normalize_team_token(name)


def _feature_vector(home: EWState, away: EWState) -> list[float]:
    h, a = home.v, away.v
    # Oriented pre-match differences plus balance/volume terms. Counts expose cold-start uncertainty.
    h_sot_edge = h["sot_for"] - a["sot_against"]
    a_sot_edge = a["sot_for"] - h["sot_against"]
    h_shot_edge = h["shots_for"] - a["shots_against"]
    a_shot_edge = a["shots_for"] - h["shots_against"]
    h_goal_edge = h["goals_for"] - a["goals_against"]
    a_goal_edge = a["goals_for"] - h["goals_against"]
    h_corner_edge = h["corners_for"] - a["corners_against"]
    a_corner_edge = a["corners_for"] - h["corners_against"]
    return [
        1.0,
        h_sot_edge - a_sot_edge,
        h_sot_edge + a_sot_edge,
        abs(h_sot_edge - a_sot_edge),
        h_shot_edge - a_shot_edge,
        h_shot_edge + a_shot_edge,
        h_goal_edge - a_goal_edge,
        h_goal_edge + a_goal_edge,
        h_corner_edge - a_corner_edge,
        (h["red"] - a["red"]),
        (h["yellow"] - a["yellow"]),
        min(home.n, away.n) / 20.0,
    ]


def _build_raw_feature_map(cid: str, csv_by_season: dict[str, list[dict[str, str]]], half_life: float) -> tuple[dict[tuple[str, str, str], list[float]], dict[str, int]]:
    alpha = 1.0 - math.exp(math.log(0.5) / half_life)
    teams: dict[str, EWState] = defaultdict(EWState)
    feature_map: dict[tuple[str, str, str], list[float]] = {}
    stats: Counter = Counter()
    raw_all: list[tuple[str, dict[str, str]]] = []
    for season in ("2022/23", "2023/24", "2024/25", "2025/26"):
        for row in csv_by_season[season]:
            raw_all.append((season, row))
    raw_all.sort(key=lambda pair: (v620._parse_date(str(pair[1].get("Date") or "")), str(pair[1].get("HomeTeam") or ""), str(pair[1].get("AwayTeam") or "")))
    for season, raw in raw_all:
        try:
            date = v620._parse_date(str(raw.get("Date") or ""))
            home_name = str(raw.get("HomeTeam") or "").strip()
            away_name = str(raw.get("AwayTeam") or "").strip()
            hk = _team_key(cid, home_name)
            ak = _team_key(cid, away_name)
            home_state = teams[hk]
            away_state = teams[ak]
            feature_map[(date, hk, ak)] = _feature_vector(home_state, away_state)
            obs = _match_stats(raw)
            if obs is None:
                stats["missing_match_stats"] += 1
                continue
            home_obs, away_obs = obs
            home_state.update(home_obs, alpha)
            away_state.update(away_obs, alpha)
            stats["state_updates"] += 1
        except Exception:
            stats["raw_row_rejected"] += 1
    return feature_map, dict(sorted(stats.items()))


def _same_team(a: str, b: str) -> bool:
    return normalize_team_token(a) == normalize_team_token(b)


def _attach(cid: str, model_rows: list[dict[str, Any]], raw_rows: list[dict[str, str]], features: dict[tuple[str, str, str], list[float]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in model_rows:
        by_date[row["date"]].append(row)
    used: set[int] = set()
    output: list[dict[str, Any]] = []
    stats: Counter = Counter()
    for raw in raw_rows:
        try:
            market, family = v620._closing_market(raw)
            if market is None:
                stats["missing_market"] += 1
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
                ranked.sort(key=lambda x: x[0], reverse=True)
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
            fk = (date, _team_key(cid, home_raw), _team_key(cid, away_raw))
            vector = features.get(fk)
            if vector is None:
                stats["feature_unmatched"] += 1
                continue
            item = dict(chosen)
            item["market"] = market
            item["market_probability"] = market
            item["form_x"] = vector
            market_side = float(market["home"]) / max(EPS, float(market["home"]) + float(market["away"]))
            item["draw_offset"] = v621._logit(float(market["draw"]))
            item["side_offset"] = v621._logit(market_side)
            output.append(item)
            stats["matched"] += 1
            stats[f"odds_{family}"] += 1
        except Exception:
            stats["row_rejected"] += 1
    return output, dict(sorted(stats.items()))


def _fit_models(rows: list[dict[str, Any]], l2: float) -> dict[str, Any]:
    draw = v621._fit_offset_binary(rows, "form_x", "draw_y", "draw_offset", l2)
    decisive = [r for r in rows if r["is_decisive"]]
    side = v621._fit_offset_binary(decisive, "form_x", "side_y", "side_offset", l2)
    return {"draw_model": draw, "side_model": side, "l2": l2}


def _prob(row: dict[str, Any], model: dict[str, Any]) -> dict[str, float]:
    pd = v621._clip(v621._predict_offset(model["draw_model"], row, "form_x", "draw_offset"))
    ph = v621._clip(v621._predict_offset(model["side_model"], row, "form_x", "side_offset"))
    rem = 1.0 - pd
    return {"home": rem * ph, "draw": pd, "away": rem * (1.0 - ph)}


def main() -> int:
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    formal_status = load_json(base.FORMAL_STATUS)
    if len((formal_status.get("reports") or {})) != 17:
        raise PlatformError("formal domain registry must contain 17 domains")

    raw_cache: dict[str, dict[str, list[dict[str, str]]]] = {}
    model_cache: dict[str, dict[str, list[dict[str, Any]]]] = {}
    source_urls: dict[str, dict[str, str]] = {}
    for cid, code in v620.LEAGUES.items():
        report = load_json(base.REPORT_ROOT / f"{cid}.json")
        seasons = _completed_outer_seasons_last_complete_only(report)[-4:]
        if seasons != ["2022/23", "2023/24", "2024/25", "2025/26"]:
            raise PlatformError(f"unexpected seasons for {cid}: {seasons}")
        model_cache[cid] = v620._build_domain_rows_with_identity(cid, seasons)
        raw_cache[cid] = {}
        source_urls[cid] = {}
        for season in seasons:
            raw, url = v620._download_csv(code, SEASON_CODES[season])
            raw_cache[cid][season] = raw
            source_urls[cid][season] = url

    candidates: list[dict[str, Any]] = []
    prepared_by_half_life: dict[float, dict[str, Any]] = {}
    for half_life in HALF_LIFE_GRID:
        fit: list[dict[str, Any]] = []
        validation: list[dict[str, Any]] = []
        holdout: list[dict[str, Any]] = []
        audit: dict[str, Any] = {}
        for cid in v620.LEAGUES:
            fmap, state_stats = _build_raw_feature_map(cid, raw_cache[cid], half_life)
            audit[cid] = {"state": state_stats, "seasons": {}}
            for season in ("2022/23", "2023/24", "2024/25", "2025/26"):
                attached, stats = _attach(cid, model_cache[cid][season], raw_cache[cid][season], fmap)
                audit[cid]["seasons"][season] = {"matched": len(attached), "attach_stats": stats, "url": source_urls[cid][season]}
                if season in ("2022/23", "2023/24"):
                    fit.extend(attached)
                elif season == "2024/25":
                    validation.extend(attached)
                else:
                    holdout.extend(attached)
        prepared_by_half_life[half_life] = {"fit": fit, "validation": validation, "holdout": holdout, "audit": audit}
        market_validation = v621._score(validation, "market_probability", 0.90)
        for l2 in L2_GRID:
            try:
                model = _fit_models(fit, l2)
                work: list[dict[str, Any]] = []
                for row in validation:
                    item = dict(row)
                    item["form_probability"] = _prob(row, model)
                    work.append(item)
                for dr in DRAW_RATIO_GRID:
                    score = v621._score(work, "form_probability", dr)
                    proper_safe = (
                        float(score["mean_brier"]) <= float(market_validation["mean_brier"]) + 1e-12
                        and float(score["mean_rps"]) <= float(market_validation["mean_rps"]) + 1e-12
                        and float(score["mean_log_loss"]) <= float(market_validation["mean_log_loss"]) + 1e-12
                    )
                    candidates.append({"half_life": half_life, "l2": l2, "draw_ratio": dr, "validation": score, "proper_scores_nonworse_than_market": proper_safe})
            except Exception as exc:
                candidates.append({"half_life": half_life, "l2": l2, "status": "FAILED", "error": f"{type(exc).__name__}: {exc}", "proper_scores_nonworse_than_market": False})

    eligible = [c for c in candidates if c.get("proper_scores_nonworse_than_market") and c.get("validation")]
    if eligible:
        eligible.sort(key=lambda c: (-float(c["validation"]["accuracy"]), float(c["validation"]["mean_log_loss"]), float(c["half_life"]), float(c["l2"]), float(c["draw_ratio"])))
        selected = eligible[0]
        data = prepared_by_half_life[float(selected["half_life"])]
        refit = _fit_models(data["fit"] + data["validation"], float(selected["l2"]))
        hwork: list[dict[str, Any]] = []
        for row in data["holdout"]:
            item = dict(row)
            item["form_probability"] = _prob(row, refit)
            hwork.append(item)
        form_holdout = v621._score(hwork, "form_probability", float(selected["draw_ratio"]))
        market_holdout = v621._score(data["holdout"], "market_probability", 0.90)
        result_status = "PASS"
        selected_audit = data["audit"]
        row_counts = {"fit": len(data["fit"]), "validation": len(data["validation"]), "holdout": len(data["holdout"])}
    else:
        selected = None
        refit = None
        form_holdout = None
        data = prepared_by_half_life[10.0]
        market_holdout = v621._score(data["holdout"], "market_probability", 0.90)
        result_status = "NO_PROPER_SCORE_SAFE_FORM_RESIDUAL"
        selected_audit = data["audit"]
        row_counts = {"fit": len(data["fit"]), "validation": len(data["validation"]), "holdout": len(data["holdout"])}

    result: dict[str, Any] = {
        "status": result_status,
        "selected_candidate": selected,
        "market_holdout": market_holdout,
        "form_holdout": form_holdout,
        "refit_audit": refit,
    }
    if form_holdout is not None:
        result["accuracy_gain_pp_vs_market"] = 100.0 * (float(form_holdout["accuracy"]) - float(market_holdout["accuracy"]))
        result["proper_score_delta_vs_market"] = {
            "brier": float(form_holdout["mean_brier"]) - float(market_holdout["mean_brier"]),
            "rps": float(form_holdout["mean_rps"]) - float(market_holdout["mean_rps"]),
            "log_loss": float(form_holdout["mean_log_loss"]) - float(market_holdout["mean_log_loss"]),
        }

    payload = {
        "schema_version": "V6.2.3-market-form-residual-r1",
        "generated_at_utc": generated.isoformat(),
        "status": "PASS",
        "scope": {
            "competitions": list(v620.LEAGUES),
            "fit_seasons": ["2022/23", "2023/24"],
            "selection_validation_season": "2024/25",
            "development_holdout_season": "2025/26",
            "baseline": "de-vigged average closing 1X2",
            "incremental_inputs": ["shots", "shots_on_target", "corners", "goals", "yellow_cards", "red_cards"],
            "feature_timing": "state read strictly before current match update",
            "half_life_grid_matches": list(HALF_LIFE_GRID),
            "l2_grid": list(L2_GRID),
            "draw_ratio_grid": list(DRAW_RATIO_GRID),
        },
        "row_counts": row_counts,
        "selected_source_audit": selected_audit,
        "result": result,
        "governance": {
            "research_challenge_only": True,
            "current_match_stats_leakage": False,
            "holdout_used_for_selection": False,
            "closing_market_is_offset": True,
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
