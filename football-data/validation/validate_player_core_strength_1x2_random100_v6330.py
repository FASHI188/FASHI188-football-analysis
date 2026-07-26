#!/usr/bin/env python3
"""V6.33.0 player-core strength direct 1X2 fixed random100 challenge.

Why this exists
---------------
V6.32 showed that formal score-derived probabilities, closing market probabilities and a nonlinear
xG/shot/form/Elo model share most directional errors. V6.33 therefore adds genuinely different
pre-match information: player-level core strength reconstructed only from lineups, transfers and
historical Transfermarkt market valuations observable before the target match.

Strict PIT construction
-----------------------
For each team before each target match:
- previous-season starter frequency is the initial core prior;
- current-season starts strictly before the target update expected-XI frequency;
- dated transfers strictly before the target remove outgoing players and add incoming players;
- each player's latest market valuation with valuation date <= target date is used;
- the target match lineup is NEVER used to construct its own features;
- same-date matches are predicted before any same-date result/lineup update can influence state.

Evaluation discipline
---------------------
- dynamic-strength candidate remains selected on 2024/25 only, as in V6.28;
- CatBoost hyperparameters are selected on 2024/25 only after training on 2022/23+2023/24;
- final fit uses 2022/23+2023/24+2024/25;
- 2025/26 is development test and fixed seed 633100 selects exactly 100 eligible matches;
- all 100 matches count; no confidence filter, league dropping, result threshold tuning, or seed replacement;
- this is research-only and cannot promote CURRENT even if 65% is reached.
"""
from __future__ import annotations

import csv
import gzip
import json
import math
import random
import sys
import urllib.request
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from catboost import CatBoostClassifier

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "validation", ROOT / "engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import validate_architecture_order_v6190 as met  # noqa: E402
import validate_dynamic_strength_1x2_random100_v6280 as v6280  # noqa: E402
import validate_direct_dynamic_1x2_random100_v6281 as v6281  # noqa: E402
from dynamic_strength_oof_screen_v470 import (  # noqa: E402
    MODEL_ROOT,
    build_season_indexes,
    challenger_matrix,
    load_domain_data,
    team_features,
    to_match,
)
from football_v460_engine import (  # noqa: E402
    _merge_parameters,
    build_score_matrix,
    expected_goals,
    fit_current_season_state,
    load_config,
    low_score_factors,
)
from platform_core import PlatformError, derive_score_marginals, load_json  # noqa: E402

OUT = ROOT / "manifests" / "v6_player_core_strength_1x2_random100_v6330_status.json"
CACHE = Path("/tmp/football-v6330-cache")
VALUATION_FILE = CACHE / "player_valuations.csv.gz"
TRAIN_SEASONS = ("2022/23", "2023/24")
SELECT_SEASON = "2024/25"
TEST_SEASON = "2025/26"
SEED = 633100
TARGET = 100
EPS = 1e-12
MIN_VALUED_CORE = 8

DEPTHS = (4, 6)
L2S = (20.0, 50.0)
DRAW_WEIGHTS = (0.8, 1.0, 1.2, 1.4)
ITERATIONS = 350
LEARNING_RATE = 0.04


def _download_valuations() -> Path:
    if VALUATION_FILE.exists() and VALUATION_FILE.stat().st_size > 0:
        return VALUATION_FILE
    cfg = load_json(ROOT / "config" / "dynamic_strength_public_evidence_v470.json")
    base = str(cfg["source"]["dataset_delivery_base"]).rstrip("/")
    url = base + "/player_valuations.csv.gz"
    CACHE.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "FASHI188-football-analysis/6.33"})
    with urllib.request.urlopen(request, timeout=180) as response, VALUATION_FILE.open("wb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            output.write(chunk)
    if not VALUATION_FILE.exists() or VALUATION_FILE.stat().st_size == 0:
        raise PlatformError("player valuation download is empty")
    return VALUATION_FILE


def _parse_valuation_date(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:10]).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _number(value: Any) -> float | None:
    try:
        x = float(str(value or "").strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x) or x <= 0.0:
        return None
    return x


def _load_valuations(wanted_players: set[int]) -> tuple[dict[int, tuple[list[datetime], list[float]]], dict[str, Any]]:
    path = _download_valuations()
    by_player: dict[int, list[tuple[datetime, float]]] = defaultdict(list)
    total = kept = bad = 0
    latest = None
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        required = {"player_id", "date", "market_value_in_eur"}
        if not required.issubset(fields):
            raise PlatformError(f"unexpected player_valuations schema: {sorted(fields)}")
        for row in reader:
            total += 1
            try:
                pid = int(str(row.get("player_id") or "").strip())
            except ValueError:
                bad += 1
                continue
            if pid not in wanted_players:
                continue
            date = _parse_valuation_date(row.get("date"))
            value = _number(row.get("market_value_in_eur"))
            if date is None or value is None:
                bad += 1
                continue
            by_player[pid].append((date, value))
            kept += 1
            latest = date if latest is None or date > latest else latest
    output = {}
    for pid, rows in by_player.items():
        rows.sort(key=lambda x: x[0])
        output[pid] = ([x[0] for x in rows], [x[1] for x in rows])
    return output, {
        "total_rows": total,
        "wanted_players": len(wanted_players),
        "players_with_valuation_history": len(output),
        "kept_valuation_rows": kept,
        "bad_rows": bad,
        "latest_valuation_date": latest.date().isoformat() if latest else None,
    }


def _valuation(histories: dict[int, tuple[list[datetime], list[float]]], player: int, cutoff: datetime) -> float | None:
    item = histories.get(player)
    if item is None:
        return None
    dates, values = item
    idx = bisect_right(dates, cutoff) - 1
    return values[idx] if idx >= 0 else None


def _transfer_active_adjustments(team_id: int, prior_end: datetime, cutoff: datetime, data: dict[str, Any]) -> tuple[set[int], set[int]]:
    incoming: set[int] = set()
    outgoing: set[int] = set()
    for date, player, from_id, to_id in data["transfers"].get(team_id, []):
        if not (prior_end < date < cutoff):
            continue
        if from_id == team_id:
            outgoing.add(player)
            incoming.discard(player)
        if to_id == team_id:
            incoming.add(player)
            outgoing.discard(player)
    return incoming, outgoing


def _team_player_features(
    team_id: int,
    season: str,
    cutoff: datetime,
    indexes: dict[str, Any],
    data: dict[str, Any],
    valuations: dict[int, tuple[list[datetime], list[float]]],
) -> dict[str, Any] | None:
    previous = indexes["previous"].get(season)
    if not previous or previous not in indexes["by_season"]:
        return None
    prior_counts: Counter = indexes["starter_counts"].get((previous, team_id), Counter())
    if not prior_counts:
        return None
    prior_end = indexes["season_end"][previous]
    incoming, outgoing = _transfer_active_adjustments(team_id, prior_end, cutoff, data)

    # Recency-weighted current-season starter evidence. Only completed matches strictly before cutoff.
    current_games = [g for g in indexes["by_season"].get(season, []) if g["date"] < cutoff and team_id in {g["home_id"], g["away_id"]}]
    current_scores: dict[int, float] = defaultdict(float)
    for age, game in enumerate(reversed(current_games[-12:])):
        weight = math.exp(-math.log(2.0) * age / 4.0)
        for player in data["starters"].get((game["game_id"], team_id), set()):
            current_scores[player] += weight

    max_prior = max(prior_counts.values()) if prior_counts else 1
    selection_score: dict[int, float] = {}
    pool = set(prior_counts) | set(current_scores) | incoming
    pool -= outgoing
    for player in pool:
        prior_component = 0.35 * float(prior_counts.get(player, 0)) / max(1.0, float(max_prior))
        current_component = float(current_scores.get(player, 0.0))
        incoming_component = 0.10 if player in incoming else 0.0
        selection_score[player] = current_component + prior_component + incoming_component

    valued = []
    for player in pool:
        value = _valuation(valuations, player, cutoff)
        if value is not None:
            valued.append((player, float(value), float(selection_score.get(player, 0.0))))
    if len(valued) < MIN_VALUED_CORE:
        return None

    by_value = sorted(valued, key=lambda x: (x[1], x[2]), reverse=True)
    by_selection = sorted(valued, key=lambda x: (x[2], x[1]), reverse=True)
    top11_value = by_value[:11]
    expected11 = by_selection[:11]
    if len(expected11) < MIN_VALUED_CORE:
        return None

    def logsum(items: list[tuple[int, float, float]], n: int | None = None) -> float:
        z = items if n is None else items[:n]
        return math.log1p(sum(v for _, v, _ in z))

    values = sorted((v for _, v, _ in top11_value), reverse=True)
    expected_values = sorted((v for _, v, _ in expected11), reverse=True)
    total11 = sum(values)
    top3 = sum(values[:3])
    expected_total = sum(expected_values)
    overlap = len({p for p, _, _ in top11_value} & {p for p, _, _ in expected11})
    current_unique = len(current_scores)
    return {
        "pool_count": len(pool),
        "valued_count": len(valued),
        "valuation_coverage": len(valued) / max(1, len(pool)),
        "log_top11_value": math.log1p(total11),
        "log_top5_value": logsum(top11_value, 5),
        "log_top3_value": logsum(top11_value, 3),
        "log_expected11_value": math.log1p(expected_total),
        "median_top11_log_value": math.log1p(values[len(values)//2]) if values else 0.0,
        "top3_share": top3 / max(EPS, total11),
        "expected_vs_value_overlap": overlap / max(1, min(11, len(expected11))),
        "current_prior_match_count": len(current_games),
        "current_unique_starters": current_unique,
        "incoming_count": len(incoming),
        "outgoing_count": len(outgoing),
    }


def _pair_features(home: dict[str, Any], away: dict[str, Any]) -> list[float]:
    diff_keys = (
        "log_top11_value", "log_top5_value", "log_top3_value", "log_expected11_value",
        "median_top11_log_value", "top3_share", "valuation_coverage", "expected_vs_value_overlap",
        "current_prior_match_count", "current_unique_starters", "incoming_count", "outgoing_count",
    )
    out = [float(home[k]) - float(away[k]) for k in diff_keys]
    out.extend([
        float(home["log_top11_value"]), float(away["log_top11_value"]),
        float(home["log_expected11_value"]), float(away["log_expected11_value"]),
        float(home["valuation_coverage"]), float(away["valuation_coverage"]),
    ])
    return out


def _all_player_ids(data_by_comp: dict[str, dict[str, Any]]) -> set[int]:
    out: set[int] = set()
    for data in data_by_comp.values():
        for players in data["starters"].values():
            out.update(players)
        for events in data["transfers"].values():
            for _, player, _, _ in events:
                out.add(player)
    return out


def _actual(h: int, a: int) -> int:
    return 0 if h > a else 1 if h == a else 2


def _one(matrix: list[dict[str, Any]]) -> list[float]:
    one = derive_score_marginals(matrix)["1x2"]
    return [float(one[k]) for k in ("home", "draw", "away")]


def _raw_dynamic(audit: dict[str, Any]) -> list[float]:
    return v6281.raw_features(audit)


def _season_rows(
    cid: str,
    season: str,
    dynamic_candidate: dict[str, Any],
    data: dict[str, Any],
    indexes: dict[str, Any],
    params: dict[str, float],
    valuations: dict[int, tuple[list[datetime], list[float]]],
) -> list[dict[str, Any]]:
    config = load_config()
    games = indexes["by_season"].get(season, [])
    previous = indexes["previous"].get(season)
    if not games or not previous or previous not in indexes["by_season"]:
        return []
    prior_rows = [to_match(g, cid) for g in indexes["by_season"][previous]]
    prior_cutoff = max(g["date"] for g in indexes["by_season"][previous]) + timedelta(days=1)
    try:
        prior_state = fit_current_season_state(prior_rows, prior_cutoff, params, config)
    except PlatformError:
        prior_state = None

    out = []
    for target in games:
        history_games = [g for g in games if g["date"] < target["date"]]
        history = [to_match(g, cid) for g in history_games]
        try:
            current_state = fit_current_season_state(history, target["date"], params, config)
            base_means = expected_goals(current_state, f"club_{target['home_id']}", f"club_{target['away_id']}", params, config)
            baseline = build_score_matrix(
                float(base_means["mu_home"]), float(base_means["mu_away"]), current_state["nb_dispersion_k"],
                params["beta_binomial_concentration"], int(config["max_total_goals_exact"]), low_score_factors(current_state, params),
            )
        except PlatformError:
            continue

        hf = team_features(target["home_id"], season, target["date"], indexes, data["transfers"])
        af = team_features(target["away_id"], season, target["date"], indexes, data["transfers"])
        if not hf.get("feature_complete") or not af.get("feature_complete"):
            continue
        try:
            dynamic_matrix, audit = challenger_matrix(
                current_state, prior_state, target["home_id"], target["away_id"], hf, af,
                dynamic_candidate, params, config,
            )
        except PlatformError:
            continue

        hp = _team_player_features(target["home_id"], season, target["date"], indexes, data, valuations)
        ap = _team_player_features(target["away_id"], season, target["date"], indexes, data, valuations)
        if hp is None or ap is None:
            continue
        baseline_p = _one(baseline)
        dynamic_p = _one(dynamic_matrix)
        player_x = _pair_features(hp, ap)
        x = [
            *baseline_p,
            *dynamic_p,
            dynamic_p[0] - dynamic_p[2],
            dynamic_p[1],
            max(dynamic_p) - sorted(dynamic_p)[-2],
            *_raw_dynamic(audit),
            *player_x,
        ]
        out.append({
            "match_key": f"{cid}:{season}:{target['game_id']}",
            "competition_id": cid,
            "season": season,
            "date": target["date"].date().isoformat(),
            "home_team_id": int(target["home_id"]),
            "away_team_id": int(target["away_id"]),
            "y": _actual(int(target["home_goals"]), int(target["away_goals"])),
            "actual_score": [int(target["home_goals"]), int(target["away_goals"])],
            "baseline": baseline_p,
            "dynamic": dynamic_p,
            "x": x,
            "home_player_context": hp,
            "away_player_context": ap,
        })
    return out


def _fit(rows: list[dict[str, Any]], depth: int, l2: float, draw_weight: float) -> CatBoostClassifier:
    model = CatBoostClassifier(
        loss_function="MultiClass",
        iterations=ITERATIONS,
        depth=int(depth),
        learning_rate=LEARNING_RATE,
        l2_leaf_reg=float(l2),
        random_seed=SEED,
        random_strength=0.5,
        bootstrap_type="Bayesian",
        bagging_temperature=0.5,
        allow_writing_files=False,
        verbose=False,
        thread_count=2,
    )
    weights = [float(draw_weight) if int(r["y"]) == 1 else 1.0 for r in rows]
    model.fit([r["x"] for r in rows], [int(r["y"]) for r in rows], sample_weight=weights)
    return model


def _apply(model: CatBoostClassifier, rows: list[dict[str, Any]], key: str = "candidate") -> None:
    probs = model.predict_proba([r["x"] for r in rows])
    for row, p in zip(rows, probs):
        row[key] = [float(v) for v in p]


def _metrics(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    n = len(rows)
    hits = 0
    brier = logloss = rps = 0.0
    counts = Counter()
    for r in rows:
        p = [float(v) for v in r[key]]
        y = int(r["y"])
        pick = max(range(3), key=lambda i: p[i])
        hits += int(pick == y)
        counts[str(pick)] += 1
        brier += met.brier3(p, y)
        logloss += met.logloss3(p, y)
        rps += v6281.rps3(p, y)
    return {
        "count": n,
        "hits": hits,
        "top1": hits / n if n else None,
        "brier": brier / n if n else None,
        "logloss": logloss / n if n else None,
        "rps": rps / n if n else None,
        "predicted_counts": dict(counts),
    }


def main() -> int:
    config = load_config()
    data_by_comp = {}
    indexes_by_comp = {}
    dynamic_selection = {}
    params_by_comp_season = {}

    for cid in v6280.COMPS:
        data = load_domain_data(cid, CACHE)
        indexes = build_season_indexes(data)
        artifact = load_json(MODEL_ROOT / cid / "model.json")
        raw_test = artifact["point_in_time_parameters"].get(TEST_SEASON)
        if not raw_test:
            raise PlatformError(f"{cid}: missing test parameters")
        test_params = _merge_parameters(config, raw_test)
        selection = v6280.choose_candidate(cid, test_params, data, indexes)
        data_by_comp[cid] = data
        indexes_by_comp[cid] = indexes
        dynamic_selection[cid] = selection["selected"]
        params_by_comp_season[cid] = {}
        for season in TRAIN_SEASONS + (SELECT_SEASON, TEST_SEASON):
            raw = artifact["point_in_time_parameters"].get(season)
            if raw:
                params_by_comp_season[cid][season] = _merge_parameters(config, raw)

    wanted_players = _all_player_ids(data_by_comp)
    valuations, valuation_audit = _load_valuations(wanted_players)

    rows = []
    by_comp_audit = {}
    for cid in v6280.COMPS:
        counts = {}
        comp_rows = []
        for season in TRAIN_SEASONS + (SELECT_SEASON, TEST_SEASON):
            params = params_by_comp_season[cid].get(season)
            if params is None:
                continue
            rs = _season_rows(
                cid, season, dynamic_selection[cid], data_by_comp[cid], indexes_by_comp[cid],
                params, valuations,
            )
            counts[season] = len(rs)
            comp_rows.extend(rs)
        if sum(counts.get(s, 0) for s in TRAIN_SEASONS) < 500:
            raise PlatformError(f"{cid}: insufficient player-model train rows {counts}")
        by_comp_audit[cid] = {"rows_by_season": counts, "dynamic_candidate": dynamic_selection[cid]["id"]}
        rows.extend(comp_rows)

    train = [r for r in rows if r["season"] in TRAIN_SEASONS]
    validation = [dict(r) for r in rows if r["season"] == SELECT_SEASON]
    test = [dict(r) for r in rows if r["season"] == TEST_SEASON]
    if len(validation) < 1200 or len(test) < 1200:
        raise PlatformError(f"insufficient pooled validation/test rows: validation={len(validation)} test={len(test)}")

    leaderboard = []
    for depth in DEPTHS:
        for l2 in L2S:
            for draw_weight in DRAW_WEIGHTS:
                model = _fit(train, depth, l2, draw_weight)
                trial = [dict(r) for r in validation]
                _apply(model, trial)
                cm = _metrics(trial, "candidate")
                dm = _metrics(trial, "dynamic")
                proper_guard = cm["logloss"] <= dm["logloss"] + 0.03 and cm["rps"] <= dm["rps"] + 0.02
                leaderboard.append({
                    "depth": depth, "l2": l2, "draw_weight": draw_weight,
                    "candidate": cm, "dynamic": dm, "proper_guard": proper_guard,
                })
    eligible = [x for x in leaderboard if x["proper_guard"]] or leaderboard
    selected = min(
        eligible,
        key=lambda x: (-x["candidate"]["top1"], x["candidate"]["logloss"], x["candidate"]["rps"], x["depth"], x["l2"], x["draw_weight"]),
    )
    leaderboard.sort(key=lambda x: (-x["candidate"]["top1"], x["candidate"]["logloss"], x["candidate"]["rps"]))

    final_train = [r for r in rows if r["season"] in TRAIN_SEASONS + (SELECT_SEASON,)]
    final_model = _fit(final_train, int(selected["depth"]), float(selected["l2"]), float(selected["draw_weight"]))
    _apply(final_model, test)

    ordered = sorted(test, key=lambda r: (r["competition_id"], r["date"], r["match_key"]))
    random.Random(SEED).shuffle(ordered)
    sample = ordered[:TARGET]
    if len(sample) != TARGET:
        raise PlatformError(f"random100 incomplete: {len(sample)}")

    sample_metrics = {
        "baseline": _metrics(sample, "baseline"),
        "dynamic": _metrics(sample, "dynamic"),
        "candidate": _metrics(sample, "candidate"),
    }
    full_metrics = {
        "baseline": _metrics(test, "baseline"),
        "dynamic": _metrics(test, "dynamic"),
        "candidate": _metrics(test, "candidate"),
    }
    by_comp = {}
    for cid in v6280.COMPS:
        rs = [r for r in sample if r["competition_id"] == cid]
        if rs:
            by_comp[cid] = {
                "count": len(rs),
                "baseline_top1": _metrics(rs, "baseline")["top1"],
                "dynamic_top1": _metrics(rs, "dynamic")["top1"],
                "candidate_top1": _metrics(rs, "candidate")["top1"],
            }

    target_hit = sample_metrics["candidate"]["top1"] >= 0.65 - 1e-12
    payload = {
        "schema_version": "V6.33.0-player-core-strength-1x2-random100-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "DEVELOPMENT_RESEARCH_NEW_INFORMATION_FIXED_RANDOM100_NO_PROMOTION",
        "target": "1X2_TOP1_AT_LEAST_65_PERCENT_ON_ALL_100_RANDOM_MATCHES",
        "sample_contract": {
            "test_season": TEST_SEASON,
            "eligible_domains": list(v6280.COMPS),
            "seed": SEED,
            "sample_count": TARGET,
            "all_sampled_matches_scored": True,
            "confidence_filtering": False,
            "posthoc_league_dropping": False,
            "seed_replacement": False,
            "v632_sample_not_reused": True,
        },
        "data_contract": {
            "target_lineup_used_as_feature": False,
            "future_valuation_used": False,
            "future_transfer_used": False,
            "same_date_update_before_prediction": False,
            "minimum_valued_core_players": MIN_VALUED_CORE,
            "valuation_source": "dcaribou/transfermarkt-datasets player_valuations.csv.gz",
            "known_2026_valuation_freshness_issue": True,
        },
        "valuation_audit": valuation_audit,
        "by_competition_build_audit": by_comp_audit,
        "row_counts": {"train": len(train), "selection": len(validation), "test": len(test), "final_train": len(final_train)},
        "model": {
            "library": "catboost",
            "iterations": ITERATIONS,
            "learning_rate": LEARNING_RATE,
            "grid": {"depth": list(DEPTHS), "l2": list(L2S), "draw_weight": list(DRAW_WEIGHTS)},
            "selected": selected,
            "leaderboard": leaderboard,
        },
        "full_2025_26_development_metrics": full_metrics,
        "random100": {
            "metrics": sample_metrics,
            "by_competition": by_comp,
            "candidate_vs_dynamic_top1_pp": (sample_metrics["candidate"]["top1"] - sample_metrics["dynamic"]["top1"]) * 100.0,
            "candidate_vs_baseline_top1_pp": (sample_metrics["candidate"]["top1"] - sample_metrics["baseline"]["top1"]) * 100.0,
            "target_65_reached": target_hit,
            "sample_rows": [
                {
                    "match_key": r["match_key"], "competition_id": r["competition_id"], "date": r["date"],
                    "actual_score": r["actual_score"], "actual_result": r["y"],
                    "baseline_pick": max(range(3), key=lambda i: r["baseline"][i]),
                    "dynamic_pick": max(range(3), key=lambda i: r["dynamic"][i]),
                    "candidate_pick": max(range(3), key=lambda i: r["candidate"][i]),
                    "candidate_probability": r["candidate"],
                }
                for r in sample
            ],
        },
        "decision": "TARGET_65_REACHED" if target_hit else "TARGET_65_NOT_REACHED",
        "governance": {
            "research_only": True,
            "viewed_test_season": True,
            "random100_cannot_promote_current": True,
            "formal_weight": 0,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "automatic_promotion": False,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"], "decision": payload["decision"],
        "valuation_audit": valuation_audit, "row_counts": payload["row_counts"],
        "selected": {k: selected[k] for k in ("depth", "l2", "draw_weight", "candidate", "dynamic", "proper_guard")},
        "full_2025_26": full_metrics,
        "random100": {k: v for k, v in payload["random100"].items() if k != "sample_rows"},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
