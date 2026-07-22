#!/usr/bin/env python3
"""V6.2.0 market-residual fusion challenge for full-match 1X2 accuracy.

Research-only experiment. It tests whether synchronized bookmaker market probabilities add
out-of-sample information to the frozen V6.0.1 direct-outcome model.

Design:
- fit V6 direct model on the same first two completed seasons used by V6.0.1;
- choose fusion weight and draw decision ratio on 2024/25 only;
- evaluate once on 2025/26 for ENG/ESP/GER/ITA/FRA top divisions;
- market inputs are Football-Data closing average 1X2 odds when available;
- no holdout tuning, no formal weight mutation, no CURRENT mutation.
"""
from __future__ import annotations

import csv
import difflib
import io
import json
import math
import sys
import urllib.request
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
from draw_recalibration_kl_v5535 import _season_key
from draw_recalibration_kl_v5535_r2 import _completed_outer_seasons_last_complete_only
from platform_core import PlatformError, atomic_write_json, canonical_team_name, load_json, normalize_team_token, read_processed_matches

OUT = ROOT / "manifests" / "v6_market_residual_fusion_v620_status.json"
V601_STATUS = ROOT / "manifests" / "v6_direct_outcome_draw_boundary_v601_status.json"

LEAGUES = {
    "ENG_PremierLeague": "E0",
    "ESP_LaLiga": "SP1",
    "GER_Bundesliga": "D1",
    "ITA_SerieA": "I1",
    "FRA_Ligue1": "F1",
}
SEASON_CODES = {"2024/25": "2425", "2025/26": "2526"}
ALPHA_GRID = tuple(i / 10.0 for i in range(11))
DRAW_RATIO_GRID = (0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.00)
EPS = 1e-12


def _download_csv(code: str, season_code: str) -> tuple[list[dict[str, str]], str]:
    url = f"https://www.football-data.co.uk/mmz4281/{season_code}/{code}.csv"
    request = urllib.request.Request(url, headers={"User-Agent": "football-v6.2-research/1.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        raw = response.read()
    text = raw.decode("utf-8-sig", errors="replace")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise PlatformError(f"empty Football-Data CSV: {url}")
    return rows, url


def _float(row: dict[str, str], key: str) -> float | None:
    try:
        value = float(str(row.get(key) or "").strip())
    except ValueError:
        return None
    return value if math.isfinite(value) and value > 1.0 else None


def _closing_market(row: dict[str, str]) -> tuple[dict[str, float] | None, str | None]:
    # Average closing market is preferred. Football-Data documents C-suffixed fields as closing odds.
    families = (
        ("AvgCH", "AvgCD", "AvgCA", "average_closing"),
        ("B365CH", "B365CD", "B365CA", "bet365_closing"),
        ("MaxCH", "MaxCD", "MaxCA", "maximum_closing"),
        ("AvgH", "AvgD", "AvgA", "average_preclosing_fallback"),
    )
    for hk, dk, ak, label in families:
        odds = (_float(row, hk), _float(row, dk), _float(row, ak))
        if all(value is not None for value in odds):
            inv = [1.0 / float(value) for value in odds]
            total = sum(inv)
            return {"home": inv[0] / total, "draw": inv[1] / total, "away": inv[2] / total}, label
    return None, None


def _parse_date(value: str) -> str:
    value = value.strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    raise PlatformError(f"unparseable Football-Data date: {value}")


def _build_domain_rows_with_identity(cid: str, seasons: list[str]) -> dict[str, list[dict[str, Any]]]:
    report = load_json(base.REPORT_ROOT / f"{cid}.json")
    all_matches = sorted(read_processed_matches(cid), key=lambda m: (m.date, m.home_team, m.away_team))
    selected = set(seasons)
    folds = {season: base._fold_for_season(report, season) for season in seasons}
    temperatures = {season: base._target_season_temperature(cid, season)[0] for season in seasons}
    teams: dict[str, base.TeamState] = defaultdict(base.TeamState)
    competition = base.CompetitionState()
    rows = {season: [] for season in seasons}
    by_date: dict[datetime, list[Any]] = defaultdict(list)
    for match in all_matches:
        by_date[match.date].append(match)

    for date in sorted(by_date):
        day_matches = sorted(by_date[date], key=lambda m: (m.home_team, m.away_team))
        for match in day_matches:
            season = str(match.season)
            if season not in selected:
                continue
            params = folds[season].get("selected_parameters")
            if not isinstance(params, dict):
                raise PlatformError(f"invalid formal parameters for {cid} {season}")
            try:
                matrix = base._predict_from_loaded_matches(all_matches, match.home_team, match.away_team, match.date, season, params)
            except PlatformError:
                continue
            temperature = float(temperatures[season])
            if abs(temperature - 1.0) > 1e-15:
                matrix = base.temperature_scale_matrix(matrix, temperature)
            margins = base.derive_score_marginals(matrix)
            formal = {key: float(margins["1x2"][key]) for key in base.CLASSES}
            home_state = teams[base._team_key(match.home_team)]
            away_state = teams[base._team_key(match.away_team)]
            draw_x, side_x = base._features(formal, matrix, home_state, away_state, competition, match.date)
            actual = base._actual_result(int(match.home_goals), int(match.away_goals))
            rows[season].append({
                "competition_id": cid,
                "season": season,
                "date": match.date.date().isoformat(),
                "home_team": match.home_team,
                "away_team": match.away_team,
                "formal": formal,
                "draw_x": draw_x,
                "side_x": side_x,
                "actual_result": actual,
                "draw_y": 1 if actual == "draw" else 0,
                "side_y": 1 if actual == "home" else 0,
                "is_decisive": actual != "draw",
            })
        for match in day_matches:
            base._update_state(teams[base._team_key(match.home_team)], teams[base._team_key(match.away_team)], competition, match)
    return rows


def _model_probability(row: dict[str, Any], models: dict[str, Any], pool_weight: float) -> dict[str, float]:
    direct = base._direct_probability(row, models)
    return base._log_pool(row["formal"], direct, pool_weight)


def _log_pool(a: dict[str, float], b: dict[str, float], weight_b: float) -> dict[str, float]:
    logits = {k: (1.0 - weight_b) * math.log(max(EPS, a[k])) + weight_b * math.log(max(EPS, b[k])) for k in base.CLASSES}
    m = max(logits.values())
    values = {k: math.exp(v - m) for k, v in logits.items()}
    total = sum(values.values())
    return {k: v / total for k, v in values.items()}


def _score(rows: list[dict[str, Any]], prob_key: str, draw_ratio: float = 1.0) -> dict[str, Any]:
    count = hits = 0
    brier = rps = logloss = 0.0
    predicted = Counter()
    for row in rows:
        q = row[prob_key]
        pick = v601._pick(q, draw_ratio)
        truth = row["actual_result"]
        hit = int(pick == truth)
        count += 1
        hits += hit
        predicted[pick] += 1
        brier += sum((q[k] - (1.0 if truth == k else 0.0)) ** 2 for k in base.CLASSES)
        truth_vec = {"home": (1.0, 0.0, 0.0), "draw": (0.0, 1.0, 0.0), "away": (0.0, 0.0, 1.0)}[truth]
        c1 = q["home"] - truth_vec[0]
        c2 = q["home"] + q["draw"] - truth_vec[0] - truth_vec[1]
        rps += (c1 * c1 + c2 * c2) / 2.0
        logloss -= math.log(max(EPS, q[truth]))
    return {
        "count": count,
        "hits": hits,
        "accuracy": hits / count if count else None,
        "mean_brier": brier / count if count else None,
        "mean_rps": rps / count if count else None,
        "mean_log_loss": logloss / count if count else None,
        "predicted_direction_counts": dict(predicted),
    }


def _same_team(a: str, b: str) -> bool:
    return normalize_team_token(a) == normalize_team_token(b)


def _match_market(cid: str, model_rows: list[dict[str, Any]], market_rows: list[dict[str, str]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in model_rows:
        by_date[row["date"]].append(row)
    used: set[int] = set()
    matched: list[dict[str, Any]] = []
    stats: Counter = Counter()

    for raw in market_rows:
        try:
            market, family = _closing_market(raw)
            if market is None:
                stats["missing_complete_odds"] += 1
                continue
            date = _parse_date(str(raw.get("Date") or ""))
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
            item["market"] = market
            item["market_odds_family"] = family
            matched.append(item)
            stats[f"odds_family_{family}"] += 1
            stats["matched"] += 1
        except Exception:
            stats["row_rejected"] += 1
    return matched, dict(sorted(stats.items()))


def main() -> int:
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    v601_status = load_json(V601_STATUS)
    selected_candidate = ((v601_status.get("result") or {}).get("selected_candidate") or {})
    l2 = float(selected_candidate.get("l2", 1.0))
    pool_weight = float(selected_candidate.get("pool_weight", 0.75))
    frozen_draw_ratio = float(selected_candidate.get("draw_ratio", 0.80))

    formal_status = load_json(base.FORMAL_STATUS)
    domains = sorted((formal_status.get("reports") or {}).keys())
    if len(domains) != 17:
        raise PlatformError(f"expected 17 formal domains, found {len(domains)}")

    rows_by_domain: dict[str, dict[str, list[dict[str, Any]]]] = {}
    roles: dict[str, Any] = {}
    fit_rows: list[dict[str, Any]] = []
    validation_rows_all: list[dict[str, Any]] = []
    for cid in domains:
        report = load_json(base.REPORT_ROOT / f"{cid}.json")
        seasons = _completed_outer_seasons_last_complete_only(report)[-4:]
        built = _build_domain_rows_with_identity(cid, seasons)
        rows_by_domain[cid] = built
        ordered = sorted(built, key=_season_key)
        roles[cid] = {"fit": ordered[:2], "validation": ordered[2], "holdout": ordered[3]}
        for season in ordered[:2]:
            fit_rows.extend(built[season])
        validation_rows_all.extend(built[ordered[2]])

    validation_model = base._fit_models(fit_rows, l2)
    holdout_model = base._fit_models(fit_rows + validation_rows_all, l2)

    validation_matched: list[dict[str, Any]] = []
    holdout_matched: list[dict[str, Any]] = []
    source_audit: dict[str, Any] = {}
    for cid, code in LEAGUES.items():
        source_audit[cid] = {}
        for season in ("2024/25", "2025/26"):
            market_rows, url = _download_csv(code, SEASON_CODES[season])
            model_rows = rows_by_domain[cid][season]
            matched, stats = _match_market(cid, model_rows, market_rows)
            model = validation_model if season == "2024/25" else holdout_model
            for row in matched:
                row["v601_probability"] = _model_probability(row, model, pool_weight)
            if season == "2024/25":
                validation_matched.extend(matched)
            else:
                holdout_matched.extend(matched)
            source_audit[cid][season] = {"url": url, "csv_rows": len(market_rows), "model_rows": len(model_rows), "match_stats": stats}

    if len(validation_matched) < 1000 or len(holdout_matched) < 1000:
        raise PlatformError(f"insufficient matched market rows: validation={len(validation_matched)} holdout={len(holdout_matched)}")

    market_validation = _score(validation_matched, "market", 1.0)
    v601_validation = _score(validation_matched, "v601_probability", frozen_draw_ratio)
    market_holdout = _score(holdout_matched, "market", 1.0)
    v601_holdout = _score(holdout_matched, "v601_probability", frozen_draw_ratio)

    candidates: list[dict[str, Any]] = []
    for alpha in ALPHA_GRID:
        for draw_ratio in DRAW_RATIO_GRID:
            work = []
            for row in validation_matched:
                item = dict(row)
                item["fusion"] = _log_pool(row["market"], row["v601_probability"], alpha)
                work.append(item)
            score = _score(work, "fusion", draw_ratio)
            proper_safe = (
                float(score["mean_brier"]) <= float(market_validation["mean_brier"]) + 1e-12
                and float(score["mean_log_loss"]) <= float(market_validation["mean_log_loss"]) + 1e-12
                and float(score["mean_rps"]) <= float(market_validation["mean_rps"]) + 1e-12
            )
            candidates.append({"alpha_v601": alpha, "draw_ratio": draw_ratio, "validation": score, "proper_scores_nonworse_than_market": proper_safe})

    eligible = [c for c in candidates if c["proper_scores_nonworse_than_market"]]
    if not eligible:
        eligible = candidates
    eligible.sort(key=lambda c: (-float(c["validation"]["accuracy"]), float(c["validation"]["mean_log_loss"]), float(c["alpha_v601"])))
    selected = eligible[0]

    holdout_work = []
    for row in holdout_matched:
        item = dict(row)
        item["fusion"] = _log_pool(row["market"], row["v601_probability"], float(selected["alpha_v601"]))
        holdout_work.append(item)
    fusion_holdout = _score(holdout_work, "fusion", float(selected["draw_ratio"]))

    result = {
        "status": "PASS",
        "validation_selected": selected,
        "market_validation": market_validation,
        "v601_validation": v601_validation,
        "market_holdout": market_holdout,
        "v601_holdout": v601_holdout,
        "fusion_holdout": fusion_holdout,
        "fusion_accuracy_gain_pp_vs_v601": 100.0 * (float(fusion_holdout["accuracy"]) - float(v601_holdout["accuracy"])),
        "fusion_accuracy_gain_pp_vs_market": 100.0 * (float(fusion_holdout["accuracy"]) - float(market_holdout["accuracy"])),
        "fusion_proper_scores_better_than_market_holdout": {
            "brier": float(fusion_holdout["mean_brier"]) < float(market_holdout["mean_brier"]),
            "rps": float(fusion_holdout["mean_rps"]) < float(market_holdout["mean_rps"]),
            "log_loss": float(fusion_holdout["mean_log_loss"]) < float(market_holdout["mean_log_loss"]),
        },
    }
    payload = {
        "schema_version": "V6.2.0-market-residual-fusion-r1",
        "generated_at_utc": generated.isoformat(),
        "status": "PASS",
        "scope": {
            "competitions": list(LEAGUES),
            "selection_season": "2024/25",
            "development_holdout_season": "2025/26",
            "football_data_market": "closing average 1X2 preferred; multiplicative de-vig",
            "v601_l2": l2,
            "v601_pool_weight": pool_weight,
            "v601_draw_ratio": frozen_draw_ratio,
        },
        "matched_counts": {"validation": len(validation_matched), "holdout": len(holdout_matched)},
        "source_audit": source_audit,
        "result": result,
        "governance": {
            "research_challenge_only": True,
            "holdout_used_for_selection": False,
            "formal_weight_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "automatic_promotion": False,
            "pristine_v610_v613_forward_test_untouched": True,
        },
    }
    atomic_write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
