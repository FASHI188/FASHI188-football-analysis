#!/usr/bin/env python3
"""V6.29.0 score-driven ordered-logit team-rating direct 1X2 random100.

Research question
-----------------
Can a genuinely direct W/D/L model, with team strength updated sequentially from prior results and a
separate draw region, improve Top-1 accuracy beyond the V6.28 continuity-aware score-matrix signal?
No bookmaker 1X2 probabilities are used.

Model
-----
For a match, latent performance difference is
    z = rating_home - rating_away + home_advantage.
Two symmetric ordered-logit cutpoints (-tau, +tau) give P(A), P(D), P(H). After all matches on a
calendar date are predicted, ratings update by the score (gradient of log likelihood) so same-day
results cannot leak into one another:
    r_home <- r_home + eta * d log p_y / dz
    r_away <- r_away - eta * d log p_y / dz.
At each new season, continuing-team ratings are multiplied by a retention factor; teams not present
in the immediately preceding competition season restart at 0.

Parameter discipline
--------------------
- Base-rate home advantage and draw width are estimated from completed seasons prior to the target.
- eta grid = {0.03, 0.06, 0.12, 0.24}; offseason retention grid = {0.50, 0.75, 0.90, 1.00}.
- Candidates are selected only by 2024/25 RPS after warming on 2021/22--2023/24; joint log breaks ties.
- For the untouched 2025/26 test, selected eta/retention are frozen and base-rate cutpoints are
  re-estimated using only completed 2021/22--2024/25 outcomes.
- Test population is restricted to the same continuity-aware V6.28 eligible 2025/26 rows; same fixed
  seed 628100 and first 100 rows are used for comparability.
- No test-season tuning, league dropping, or post-hoc threshold selection.

This is a module-level diagnostic only. Random100 cannot promote CURRENT V5.0.1.
"""
from __future__ import annotations

import json
import math
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "validation", ROOT / "engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import validate_architecture_order_v6190 as met  # noqa: E402
import validate_dynamic_strength_1x2_random100_v6280 as v6280  # noqa: E402
import validate_direct_dynamic_1x2_random100_v6281 as v6281  # noqa: E402
from dynamic_strength_oof_screen_v470 import MODEL_ROOT, build_season_indexes, load_domain_data  # noqa: E402
from football_v460_engine import _merge_parameters, load_config  # noqa: E402
from platform_core import PlatformError, load_json  # noqa: E402

OUT = ROOT / "manifests" / "v6_score_driven_ordered_1x2_random100_v6290_status.json"
CACHE = Path("/tmp/football-v629-score-driven-cache")
WARM_SEASONS = ("2021/22", "2022/23", "2023/24")
SELECT_SEASON = "2024/25"
TEST_SEASON = "2025/26"
SEED = 628100
TARGET = 100
ETA_GRID = (0.03, 0.06, 0.12, 0.24)
RETENTION_GRID = (0.50, 0.75, 0.90, 1.00)
EPS = 1e-15


def logistic(x: float) -> float:
    if x >= 0:
        e = math.exp(-x)
        return 1.0 / (1.0 + e)
    e = math.exp(x)
    return e / (1.0 + e)


def ordered_probs(diff: float, home_adv: float, tau: float) -> list[float]:
    z = float(diff) + float(home_adv)
    s1 = logistic(-float(tau) - z)
    s2 = logistic(float(tau) - z)
    p_away = s1
    p_draw = max(EPS, s2 - s1)
    p_home = max(EPS, 1.0 - s2)
    raw = [p_home, p_draw, p_away]
    total = sum(raw)
    return [x / total for x in raw]


def grad_logp_z(diff: float, home_adv: float, tau: float, outcome: int) -> float:
    z = float(diff) + float(home_adv)
    s1 = logistic(-float(tau) - z)
    s2 = logistic(float(tau) - z)
    ds1 = -s1 * (1.0 - s1)
    ds2 = -s2 * (1.0 - s2)
    # outcome index: 0 home, 1 draw, 2 away
    if outcome == 2:
        p = max(EPS, s1)
        dp = ds1
    elif outcome == 1:
        p = max(EPS, s2 - s1)
        dp = ds2 - ds1
    else:
        p = max(EPS, 1.0 - s2)
        dp = -ds2
    g = dp / p
    return max(-8.0, min(8.0, g))


def result_idx(h: int, a: int) -> int:
    return 0 if h > a else 1 if h == a else 2


def empirical_cutpoints(games: list[dict[str, Any]], seasons: tuple[str, ...]) -> tuple[float, float, dict[str, float]]:
    selected = [g for g in games if g["season"] in seasons]
    if not selected:
        raise PlatformError("no games for ordered-logit base-rate estimation")
    n = len(selected)
    h = sum(1 for g in selected if int(g["home_goals"]) > int(g["away_goals"])) / n
    a = sum(1 for g in selected if int(g["home_goals"]) < int(g["away_goals"])) / n
    h = min(0.95, max(0.02, h))
    a = min(0.95, max(0.02, a))
    logit_h = math.log(h / (1.0 - h))
    logit_a = math.log(a / (1.0 - a))
    home_adv = 0.5 * (logit_h - logit_a)
    tau = max(0.05, -0.5 * (logit_h + logit_a))
    return home_adv, tau, {"home_rate": h, "away_rate": a, "draw_rate": 1.0 - h - a, "rows": n}


def season_order(games: list[dict[str, Any]]) -> list[str]:
    by = defaultdict(list)
    for g in games:
        by[g["season"]].append(g)
    return sorted(by, key=lambda s: min(g["date"] for g in by[s]))


def simulate(
    games: list[dict[str, Any]],
    seasons: tuple[str, ...],
    eta: float,
    retention: float,
    home_adv: float,
    tau: float,
    collect_season: str | None = None,
) -> tuple[list[dict[str, Any]], dict[int, float]]:
    wanted = set(seasons)
    by_season = defaultdict(list)
    for g in games:
        if g["season"] in wanted:
            by_season[g["season"]].append(g)
    ordered = [s for s in season_order(games) if s in wanted]
    ratings: dict[int, float] = {}
    previous_teams: set[int] = set()
    rows: list[dict[str, Any]] = []
    for season in ordered:
        sgames = sorted(by_season[season], key=lambda g: (g["date"], g["game_id"]))
        current_teams = {int(g["home_id"]) for g in sgames} | {int(g["away_id"]) for g in sgames}
        if previous_teams:
            for team in list(ratings):
                if team in current_teams and team in previous_teams:
                    ratings[team] *= float(retention)
                elif team in current_teams:
                    ratings[team] = 0.0
        for t in current_teams:
            ratings.setdefault(t, 0.0)
        by_date = defaultdict(list)
        for g in sgames:
            by_date[g["date"]].append(g)
        for dt in sorted(by_date):
            day = sorted(by_date[dt], key=lambda g: g["game_id"])
            pending = []
            for g in day:
                hid = int(g["home_id"]); aid = int(g["away_id"])
                diff = ratings.get(hid, 0.0) - ratings.get(aid, 0.0)
                p = ordered_probs(diff, home_adv, tau)
                y = result_idx(int(g["home_goals"]), int(g["away_goals"]))
                if collect_season is None or season == collect_season:
                    rows.append({
                        "match_key": f"{g['competition_id']}:{season}:{g['game_id']}",
                        "competition_id": g["competition_id"],
                        "season": season,
                        "date": g["date"].date().isoformat(),
                        "probabilities": p,
                        "actual": y,
                        "home_rating": ratings.get(hid, 0.0),
                        "away_rating": ratings.get(aid, 0.0),
                        "rating_difference": diff,
                    })
                pending.append((hid, aid, diff, y))
            # Same-date results update only after all predictions are frozen.
            delta = defaultdict(float)
            for hid, aid, diff, y in pending:
                gscore = grad_logp_z(diff, home_adv, tau, y)
                delta[hid] += float(eta) * gscore
                delta[aid] -= float(eta) * gscore
            for team, change in delta.items():
                ratings[team] = ratings.get(team, 0.0) + change
        previous_teams = current_teams
    return rows, ratings


def rps3(p: list[float], y: int) -> float:
    return ((p[0] - (1.0 if y == 0 else 0.0)) ** 2 + (p[0] + p[1] - (1.0 if y <= 1 else 0.0)) ** 2) / 2.0


def score_rows(rows: list[dict[str, Any]]) -> dict[str, float]:
    if not rows:
        raise PlatformError("no rows to score")
    return {
        "count": len(rows),
        "top1": mean(int(max(range(3), key=lambda i: r["probabilities"][i]) == int(r["actual"])) for r in rows),
        "brier": mean(met.brier3(r["probabilities"], int(r["actual"])) for r in rows),
        "logloss": mean(met.logloss3(r["probabilities"], int(r["actual"])) for r in rows),
        "rps": mean(rps3(r["probabilities"], int(r["actual"])) for r in rows),
    }


def select_candidate(games: list[dict[str, Any]]) -> tuple[dict[str, float], list[dict[str, Any]], dict[str, Any]]:
    pre = WARM_SEASONS
    base_home_adv, base_tau, base_rates = empirical_cutpoints(games, pre)
    candidates = []
    all_seasons = WARM_SEASONS + (SELECT_SEASON,)
    for eta in ETA_GRID:
        for retention in RETENTION_GRID:
            rows, _ = simulate(games, all_seasons, eta, retention, base_home_adv, base_tau, collect_season=SELECT_SEASON)
            scored = score_rows(rows)
            candidates.append({"eta": eta, "retention": retention, **scored})
    candidates.sort(key=lambda r: (r["rps"], r["logloss"], -r["top1"], r["eta"], r["retention"]))
    selected = candidates[0]
    return {"eta": float(selected["eta"]), "retention": float(selected["retention"])}, candidates, {"home_adv": base_home_adv, "tau": base_tau, **base_rates}


def build_rating_test_map(games: list[dict[str, Any]], selected: dict[str, float]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    fit_seasons = WARM_SEASONS + (SELECT_SEASON,)
    home_adv, tau, rates = empirical_cutpoints(games, fit_seasons)
    all_seasons = fit_seasons + (TEST_SEASON,)
    rows, _ = simulate(games, all_seasons, selected["eta"], selected["retention"], home_adv, tau, collect_season=TEST_SEASON)
    return {r["match_key"]: r for r in rows}, {"home_adv": home_adv, "tau": tau, **rates}


def avg(rows: list[dict[str, Any]], key: str) -> float:
    return sum(float(r[key]) for r in rows) / len(rows)


def main() -> int:
    config = load_config()
    all_joined = []
    audits = {}
    failures = {}
    for cid in v6280.COMPS:
        try:
            data = load_domain_data(cid, CACHE)
            indexes = build_season_indexes(data)
            games = [dict(g, competition_id=cid) for g in data["games"]]
            artifact = load_json(MODEL_ROOT / cid / "model.json")
            test_params_raw = artifact["point_in_time_parameters"].get(TEST_SEASON)
            if not test_params_raw:
                raise PlatformError("missing 2025/26 formal parameter set for comparator")
            test_params = _merge_parameters(config, test_params_raw)
            dynamic_selection = v6280.choose_candidate(cid, test_params, data, indexes)
            dynamic_candidate = dynamic_selection["selected"]
            comparator_rows = v6281.season_feature_rows(cid, TEST_SEASON, dynamic_candidate, data, indexes, test_params)
            comparator_map = {r["match_key"]: r for r in comparator_rows}

            selected, selection_table, selection_base = select_candidate(games)
            rating_map, test_base = build_rating_test_map(games, selected)
            keys = sorted(set(comparator_map) & set(rating_map))
            for key in keys:
                c = comparator_map[key]
                r = rating_map[key]
                y = int(c["actual"])
                rp = [float(x) for x in r["probabilities"]]
                bp = [float(x) for x in c["baseline_1x2"]]
                dp = [float(x) for x in c["dynamic_1x2"]]
                all_joined.append({
                    "match_key": key,
                    "competition_id": cid,
                    "date": c["date"],
                    "actual": y,
                    "baseline_1x2": bp,
                    "dynamic_1x2": dp,
                    "rating_1x2": rp,
                    "baseline_top1": int(max(range(3), key=lambda i: bp[i]) == y),
                    "dynamic_top1": int(max(range(3), key=lambda i: dp[i]) == y),
                    "rating_top1": int(max(range(3), key=lambda i: rp[i]) == y),
                    "baseline_brier": met.brier3(bp, y),
                    "dynamic_brier": met.brier3(dp, y),
                    "rating_brier": met.brier3(rp, y),
                    "baseline_logloss": met.logloss3(bp, y),
                    "dynamic_logloss": met.logloss3(dp, y),
                    "rating_logloss": met.logloss3(rp, y),
                    "baseline_rps": rps3(bp, y),
                    "dynamic_rps": rps3(dp, y),
                    "rating_rps": rps3(rp, y),
                    "rating_difference": float(r["rating_difference"]),
                })
            audits[cid] = {
                "selected_rating_candidate": selected,
                "selection_base_rates": selection_base,
                "test_base_rates": test_base,
                "selection_candidates": selection_table,
                "dynamic_comparator_candidate": dynamic_candidate["id"],
                "joined_test_rows": len(keys),
            }
        except Exception as exc:
            failures[cid] = str(exc)

    ordered = sorted(all_joined, key=lambda r: (r["competition_id"], r["date"], r["match_key"]))
    random.Random(SEED).shuffle(ordered)
    sample = ordered[:TARGET]
    if not sample:
        raise RuntimeError("no joined V6.29 test rows")
    summary = {"count": len(sample)}
    for prefix in ("baseline", "dynamic", "rating"):
        for metric in ("top1", "brier", "logloss", "rps"):
            summary[f"{prefix}_{metric}"] = avg(sample, f"{prefix}_{metric}")
    summary["rating_vs_baseline_top1_pp"] = (summary["rating_top1"] - summary["baseline_top1"]) * 100.0
    summary["rating_vs_dynamic_top1_pp"] = (summary["rating_top1"] - summary["dynamic_top1"]) * 100.0
    checks = {
        "sample_100": len(sample) == TARGET,
        "rating_top1_at_least_baseline_plus_5pp": summary["rating_top1"] >= summary["baseline_top1"] + 0.05 - 1e-12,
        "rating_top1_not_below_dynamic": summary["rating_top1"] >= summary["dynamic_top1"] - 1e-12,
        "rating_brier_not_worse_than_dynamic": summary["rating_brier"] <= summary["dynamic_brier"] + 1e-12,
        "rating_logloss_not_worse_than_dynamic": summary["rating_logloss"] <= summary["dynamic_logloss"] + 1e-12,
        "rating_rps_not_worse_than_dynamic": summary["rating_rps"] <= summary["dynamic_rps"] + 1e-12,
    }
    report = {
        "schema_version": "V6.29.0-score-driven-ordered-logit-rating-random100-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if len(sample) == TARGET else "PARTIAL",
        "formal_current_version": "V5.0.1",
        "classification": "RETROSPECTIVE_SCORE_DRIVEN_DIRECT_1X2_SELECTION_2024_25_TEST_2025_26_RANDOM100",
        "warm_seasons": list(WARM_SEASONS),
        "selection_season": SELECT_SEASON,
        "test_season": TEST_SEASON,
        "seed": SEED,
        "target": TARGET,
        "eligible_joined_population": len(all_joined),
        "candidate_grid": {"eta": list(ETA_GRID), "offseason_retention": list(RETENTION_GRID)},
        "competition_failures": failures,
        "selection_audits": audits,
        "summary": summary,
        "module_gate": {
            "checks": checks,
            "passed": all(checks.values()),
            "on_pass": "RUN_FINAL_MATRIX_SYSTEM_GATE_WITH_V626_TOTAL",
            "on_failure": "REJECT_THIS_SCORE_DRIVEN_RATING_SPEC; DO_NOT_RETUNE_ON_2025_26_RANDOM100",
        },
        "sample": sample,
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "current_rule_change": False,
            "bookmaker_1x2_input_used": False,
            "same_day_results_withheld": True,
            "candidate_selected_on_2024_25_only": True,
            "test_outcomes_used_for_selection": False,
            "test_league_subset_selection": False,
            "random100_is_diagnostic_only": True,
            "automatic_promotion": False,
        },
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "failures": failures,
        "selected": {k: v["selected_rating_candidate"] for k, v in audits.items()},
        "summary": summary,
        "module_gate": report["module_gate"],
    }, ensure_ascii=False, indent=2))
    return 0 if len(sample) == TARGET else 2


if __name__ == "__main__":
    raise SystemExit(main())
