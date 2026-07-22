#!/usr/bin/env python3
"""V6.2.4 Understat xG residual challenge.

Tests genuinely different football-performance information against the closing 1X2 market:
rolling xG/xGA, non-penalty xG/xGA, PPDA, deep completions and xPTS from Understat.
For each match, only prior Understat league matches are in state. Fit 2022/23-2023/24,
select on 2024/25, evaluate 2025/26 once. Research only; no CURRENT/runtime mutation.
"""
from __future__ import annotations

import codecs
import hashlib
import json
import math
import re
import sys
import urllib.request
from collections import defaultdict
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

OUT = ROOT / "manifests" / "v6_understat_xg_residual_v624_status.json"
UNDERSTAT_LEAGUES = {
    "ENG_PremierLeague": "EPL",
    "ESP_LaLiga": "La_liga",
    "GER_Bundesliga": "Bundesliga",
    "ITA_SerieA": "Serie_A",
    "FRA_Ligue1": "Ligue_1",
}
YEAR_BY_SEASON = {"2022/23": 2022, "2023/24": 2023, "2024/25": 2024, "2025/26": 2025}
SEASON_CODES = {"2022/23": "2223", "2023/24": "2324", "2024/25": "2425", "2025/26": "2526"}
HALF_LIFE_GRID = (5.0, 10.0, 20.0)
L2_GRID = (1.0, 10.0, 100.0, 1000.0)
DRAW_RATIO_GRID = (0.80, 0.85, 0.90, 0.95, 1.00)
EPS = 1e-12
XG_KEYS = ("xg", "xga", "npxg", "npxga", "ppda", "oppda", "deep", "deep_allowed", "xpts")


def _fetch_understat_teams(league: str, year: int) -> tuple[dict[str, Any], dict[str, Any]]:
    url = f"https://understat.com/league/{league}/{year}"
    request = urllib.request.Request(url, headers={"User-Agent": "football-v6.2-xg-research/1.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        html = response.read().decode("utf-8", errors="replace")
    match = re.search(r"var\s+teamsData\s*=\s*JSON\.parse\('(.+?)'\)", html, flags=re.DOTALL)
    if not match:
        raise PlatformError(f"Understat teamsData not found: {url}")
    encoded = match.group(1)
    try:
        decoded = codecs.decode(encoded, "unicode_escape")
        data = json.loads(decoded)
    except Exception as exc:
        raise PlatformError(f"Understat teamsData decode failed: {url}: {exc}") from exc
    if not isinstance(data, dict) or not data:
        raise PlatformError(f"Understat teamsData invalid: {url}")
    return data, {"url": url, "html_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(), "team_count": len(data)}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def _ppda(value: Any) -> float:
    if not isinstance(value, dict):
        return 0.0
    att = _f(value.get("att"))
    deff = _f(value.get("def"))
    return att / deff if deff > 0 else 0.0


class XGState:
    def __init__(self) -> None:
        self.n = 0
        self.v = {k: 0.0 for k in XG_KEYS}

    def update(self, obs: dict[str, float], alpha: float) -> None:
        if self.n == 0:
            self.v = {k: float(obs[k]) for k in XG_KEYS}
        else:
            for k in XG_KEYS:
                self.v[k] = (1.0 - alpha) * self.v[k] + alpha * float(obs[k])
        self.n += 1


def _understat_team_token(cid: str, title: str) -> str:
    try:
        title = canonical_team_name(cid, title)
    except Exception:
        pass
    return normalize_team_token(title)


def _history_obs(item: dict[str, Any]) -> dict[str, float]:
    return {
        "xg": _f(item.get("xG")),
        "xga": _f(item.get("xGA")),
        "npxg": _f(item.get("npxG")),
        "npxga": _f(item.get("npxGA")),
        "ppda": _ppda(item.get("ppda")),
        "oppda": _ppda(item.get("ppda_allowed")),
        "deep": _f(item.get("deep")),
        "deep_allowed": _f(item.get("deep_allowed")),
        "xpts": _f(item.get("xpts")),
    }


def _date_only(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) < 10:
        raise PlatformError(f"invalid Understat date: {text}")
    return text[:10]


def _build_state_maps(cid: str, seasons_data: dict[str, dict[str, Any]], half_life: float) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, int]]:
    alpha = 1.0 - math.exp(math.log(0.5) / half_life)
    histories: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    team_titles: dict[str, str] = {}
    for season in ("2022/23", "2023/24", "2024/25", "2025/26"):
        teams = seasons_data[season]
        for team in teams.values():
            title = str(team.get("title") or "").strip()
            if not title:
                continue
            token = _understat_team_token(cid, title)
            team_titles[token] = title
            history = team.get("history") or []
            if not isinstance(history, list):
                continue
            for item in history:
                if not isinstance(item, dict):
                    continue
                histories[token].append((_date_only(item.get("date")), item))
    state_map: dict[tuple[str, str], dict[str, Any]] = {}
    stats = {"team_tokens": len(histories), "history_rows": 0}
    for token, rows in histories.items():
        rows.sort(key=lambda pair: pair[0])
        state = XGState()
        seen_dates: set[str] = set()
        for date, item in rows:
            # Duplicate date for the same team would be ambiguous; fail closed by keeping first pre-match state.
            if date not in seen_dates:
                state_map[(date, token)] = {"n": state.n, "v": dict(state.v), "title": team_titles.get(token)}
                seen_dates.add(date)
            state.update(_history_obs(item), alpha)
            stats["history_rows"] += 1
    return state_map, stats


def _xg_features(home: dict[str, Any], away: dict[str, Any]) -> list[float]:
    h = home["v"]
    a = away["v"]
    h_xg_edge = float(h["xg"]) - float(a["xga"])
    a_xg_edge = float(a["xg"]) - float(h["xga"])
    h_npxg_edge = float(h["npxg"]) - float(a["npxga"])
    a_npxg_edge = float(a["npxg"]) - float(h["npxga"])
    h_deep_edge = float(h["deep"]) - float(a["deep_allowed"])
    a_deep_edge = float(a["deep"]) - float(h["deep_allowed"])
    return [
        1.0,
        h_xg_edge - a_xg_edge,
        h_xg_edge + a_xg_edge,
        abs(h_xg_edge - a_xg_edge),
        h_npxg_edge - a_npxg_edge,
        h_npxg_edge + a_npxg_edge,
        (float(h["xpts"]) - float(a["xpts"])),
        (float(h["ppda"]) - float(a["ppda"])),
        (float(h["oppda"]) - float(a["oppda"])),
        h_deep_edge - a_deep_edge,
        h_deep_edge + a_deep_edge,
        min(int(home["n"]), int(away["n"])) / 20.0,
    ]


def _attach_xg(cid: str, rows: list[dict[str, Any]], state_map: dict[tuple[str, str], dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    output: list[dict[str, Any]] = []
    stats = {"input": len(rows), "matched": 0, "missing_home_state": 0, "missing_away_state": 0}
    for row in rows:
        date = str(row["date"])
        try:
            home_token = _understat_team_token(cid, str(row["home_team"]))
            away_token = _understat_team_token(cid, str(row["away_team"]))
            home = state_map.get((date, home_token))
            away = state_map.get((date, away_token))
            if home is None:
                stats["missing_home_state"] += 1
                continue
            if away is None:
                stats["missing_away_state"] += 1
                continue
            item = dict(row)
            item["market_probability"] = item["market"]
            item["xg_x"] = _xg_features(home, away)
            market = item["market"]
            side = float(market["home"]) / max(EPS, float(market["home"]) + float(market["away"]))
            item["draw_offset"] = v621._logit(float(market["draw"]))
            item["side_offset"] = v621._logit(side)
            output.append(item)
            stats["matched"] += 1
        except Exception:
            continue
    return output, stats


def _fit_models(rows: list[dict[str, Any]], l2: float) -> dict[str, Any]:
    draw = v621._fit_offset_binary(rows, "xg_x", "draw_y", "draw_offset", l2)
    decisive = [r for r in rows if r["is_decisive"]]
    side = v621._fit_offset_binary(decisive, "xg_x", "side_y", "side_offset", l2)
    return {"draw_model": draw, "side_model": side, "l2": l2}


def _prob(row: dict[str, Any], model: dict[str, Any]) -> dict[str, float]:
    pd = v621._clip(v621._predict_offset(model["draw_model"], row, "xg_x", "draw_offset"))
    ph = v621._clip(v621._predict_offset(model["side_model"], row, "xg_x", "side_offset"))
    rem = 1.0 - pd
    return {"home": rem * ph, "draw": pd, "away": rem * (1.0 - ph)}


def main() -> int:
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    formal_status = load_json(base.FORMAL_STATUS)
    if len((formal_status.get("reports") or {})) != 17:
        raise PlatformError("formal domain registry must contain 17 domains")

    understat_cache: dict[str, dict[str, dict[str, Any]]] = {}
    understat_audit: dict[str, Any] = {}
    market_cache: dict[str, dict[str, list[dict[str, Any]]]] = {}
    source_audit: dict[str, Any] = {}
    failures: dict[str, str] = {}
    for cid, league in UNDERSTAT_LEAGUES.items():
        try:
            report = load_json(base.REPORT_ROOT / f"{cid}.json")
            seasons = _completed_outer_seasons_last_complete_only(report)[-4:]
            if seasons != ["2022/23", "2023/24", "2024/25", "2025/26"]:
                raise PlatformError(f"unexpected seasons for {cid}: {seasons}")
            model_rows = v620._build_domain_rows_with_identity(cid, seasons)
            understat_cache[cid] = {}
            understat_audit[cid] = {}
            market_cache[cid] = {}
            source_audit[cid] = {}
            for season in seasons:
                teams, ua = _fetch_understat_teams(league, YEAR_BY_SEASON[season])
                understat_cache[cid][season] = teams
                understat_audit[cid][season] = ua
                csv_rows, market_url = v620._download_csv(v620.LEAGUES[cid], SEASON_CODES[season])
                market_rows, market_stats = v620._match_market(cid, model_rows[season], csv_rows)
                market_cache[cid][season] = market_rows
                source_audit[cid][season] = {
                    "market_url": market_url,
                    "market_rows": len(market_rows),
                    "market_match_stats": market_stats,
                }
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"
    if failures:
        payload = {
            "schema_version": "V6.2.4-understat-xg-residual-r1",
            "generated_at_utc": generated.isoformat(),
            "status": "FAIL_DATA",
            "failures": failures,
            "governance": {"formal_weight_change": False, "runtime_probability_change": False, "current_rule_change": False},
        }
        atomic_write_json(OUT, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    candidates: list[dict[str, Any]] = []
    prepared: dict[float, dict[str, Any]] = {}
    for half_life in HALF_LIFE_GRID:
        fit: list[dict[str, Any]] = []
        validation: list[dict[str, Any]] = []
        holdout: list[dict[str, Any]] = []
        attach_audit: dict[str, Any] = {}
        for cid in UNDERSTAT_LEAGUES:
            state_map, state_stats = _build_state_maps(cid, understat_cache[cid], half_life)
            attach_audit[cid] = {"state": state_stats, "seasons": {}}
            for season in ("2022/23", "2023/24", "2024/25", "2025/26"):
                rows, astats = _attach_xg(cid, market_cache[cid][season], state_map)
                attach_audit[cid]["seasons"][season] = astats
                if season in ("2022/23", "2023/24"):
                    fit.extend(rows)
                elif season == "2024/25":
                    validation.extend(rows)
                else:
                    holdout.extend(rows)
        prepared[half_life] = {"fit": fit, "validation": validation, "holdout": holdout, "audit": attach_audit}
        # Market decision boundary can differ from argmax, but proper scores are boundary invariant.
        market_validation = v621._score(validation, "market_probability", 0.90)
        for l2 in L2_GRID:
            try:
                model = _fit_models(fit, l2)
                work: list[dict[str, Any]] = []
                for row in validation:
                    item = dict(row)
                    item["xg_probability"] = _prob(row, model)
                    work.append(item)
                for dr in DRAW_RATIO_GRID:
                    score = v621._score(work, "xg_probability", dr)
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
        data = prepared[float(selected["half_life"])]
        refit = _fit_models(data["fit"] + data["validation"], float(selected["l2"]))
        hwork: list[dict[str, Any]] = []
        for row in data["holdout"]:
            item = dict(row)
            item["xg_probability"] = _prob(row, refit)
            hwork.append(item)
        xg_holdout = v621._score(hwork, "xg_probability", float(selected["draw_ratio"]))
        market_holdout = v621._score(data["holdout"], "market_probability", 0.90)
        result_status = "PASS"
        row_counts = {"fit": len(data["fit"]), "validation": len(data["validation"]), "holdout": len(data["holdout"])}
        selected_attach_audit = data["audit"]
    else:
        selected = None
        refit = None
        xg_holdout = None
        data = prepared[10.0]
        market_holdout = v621._score(data["holdout"], "market_probability", 0.90)
        result_status = "NO_PROPER_SCORE_SAFE_XG_RESIDUAL"
        row_counts = {"fit": len(data["fit"]), "validation": len(data["validation"]), "holdout": len(data["holdout"])}
        selected_attach_audit = data["audit"]

    result: dict[str, Any] = {
        "status": result_status,
        "selected_candidate": selected,
        "market_holdout": market_holdout,
        "xg_holdout": xg_holdout,
        "refit_audit": refit,
    }
    if xg_holdout is not None:
        result["accuracy_gain_pp_vs_market"] = 100.0 * (float(xg_holdout["accuracy"]) - float(market_holdout["accuracy"]))
        result["proper_score_delta_vs_market"] = {
            "brier": float(xg_holdout["mean_brier"]) - float(market_holdout["mean_brier"]),
            "rps": float(xg_holdout["mean_rps"]) - float(market_holdout["mean_rps"]),
            "log_loss": float(xg_holdout["mean_log_loss"]) - float(market_holdout["mean_log_loss"]),
        }

    payload = {
        "schema_version": "V6.2.4-understat-xg-residual-r1",
        "generated_at_utc": generated.isoformat(),
        "status": "PASS",
        "scope": {
            "competitions": list(UNDERSTAT_LEAGUES),
            "fit_seasons": ["2022/23", "2023/24"],
            "selection_validation_season": "2024/25",
            "development_holdout_season": "2025/26",
            "baseline": "de-vigged average closing 1X2",
            "incremental_inputs": ["xG", "xGA", "npxG", "npxGA", "PPDA", "OPPDA", "deep", "deep_allowed", "xPTS"],
            "feature_timing": "Understat team state read before current league match update",
            "half_life_grid_matches": list(HALF_LIFE_GRID),
            "l2_grid": list(L2_GRID),
            "draw_ratio_grid": list(DRAW_RATIO_GRID),
        },
        "row_counts": row_counts,
        "understat_source_audit": understat_audit,
        "selected_attach_audit": selected_attach_audit,
        "market_source_audit": source_audit,
        "result": result,
        "governance": {
            "research_challenge_only": True,
            "current_match_xg_leakage": False,
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
