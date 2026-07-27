#!/usr/bin/env python3
"""V6.57.0 strict-PIT recent-core absence / return intervention.

Purpose
-------
Approximate real pre-match player availability without fabricating historical injury
bulletin timestamps. The expected core is rebuilt exactly from information available
strictly before each target match: previous-season starter prior, current-season prior
starts, dated transfers, and latest valuation <= target date. The target-match lineup
is never used.

For each team, derive only from PRIOR lineups:
- expected-core valuation share absent from the immediately prior lineup;
- expected-core valuation share absent from each of the last 2 / last 3 lineups;
- expected-core overlap with prior lineup and average start rate over prior 3;
- prior-lineup continuity;
- expected-core value share newly missing vs the prior-2 lineup;
- expected-core value share returning vs the prior-2 lineup.

Decision layer
--------------
The closing-market Top-1 remains default. If the market Top-1 is home or away and that
picked side has materially greater recent-core absence pressure than the opponent, a
predeclared rule may switch ONLY to the market's second-ranked outcome. The probability
vector is unchanged, so Brier/logloss/RPS remain market-identical by construction.

Staged validation
-----------------
- expected-core/absence features are result-blind;
- rule grid is selected on 2023/24 only;
- fixed rule is evaluated on untouched 2024/25;
- A_FAST100 labels open only if 2023/24 uplift >0, 2024/25 uplift >= +0.5pp and both
  periods have minimum intervention support;
- B300/C100 never read;
- CURRENT V5.0.1 unchanged; formal_weight=0.
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "validation", ROOT / "engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import build_gold500_feature_library_v6360 as g0  # noqa: E402
import build_gold500_feature_library_v6361 as g1  # noqa: E402
import validate_player_core_strength_1x2_random100_v6330 as v633  # noqa: E402
import validate_rich_market_catboost_full500_v6510 as v651  # noqa: E402

OUT = ROOT / "manifests" / "v6_core_absence_intervention_full500_v6570_status.json"
SEASONS = ("2022/23", "2023/24", "2024/25", "2025/26")
SELECT_SEASON = "2023/24"
HOLDOUT_SEASON = "2024/25"
EPS = 1e-12
MIN_PRIOR_GAMES = 3
MIN_VALUED_CORE = 8
SCORE_SPECS = ("absence", "absence_plus_new", "absence_continuity")
DELTA_THRESHOLDS = (0.05, 0.10, 0.15, 0.20, 0.25)
MIN_PICKED_MISS1 = (0.05, 0.10, 0.15, 0.20)
MARKET_MARGIN_CAPS = (0.05, 0.10, 0.15, 0.20, 0.30)
MIN_SELECTION_OVERRIDES = 12
MIN_HOLDOUT_OVERRIDES = 10
HOLDOUT_REQUIRED_UPLIFT_PP = 0.5


def value_share(players: set[int], values: dict[int, float], denom: float) -> float:
    return sum(float(values.get(p, 0.0)) for p in players) / max(EPS, denom)


def availability_context(
    team_id: int,
    season: str,
    cutoff,
    indexes: dict[str, Any],
    data: dict[str, Any],
    valuations: dict[int, tuple[list, list[float]]],
) -> dict[str, float] | None:
    previous = indexes["previous"].get(season)
    if not previous or previous not in indexes["by_season"]:
        return None
    prior_counts: Counter = indexes["starter_counts"].get((previous, team_id), Counter())
    if not prior_counts:
        return None
    prior_end = indexes["season_end"][previous]
    incoming, outgoing = v633._transfer_active_adjustments(team_id, prior_end, cutoff, data)

    current_games = [
        g for g in indexes["by_season"].get(season, [])
        if g["date"] < cutoff and team_id in {g["home_id"], g["away_id"]}
    ]
    if len(current_games) < MIN_PRIOR_GAMES:
        return None

    current_scores: dict[int, float] = defaultdict(float)
    for age, game in enumerate(reversed(current_games[-12:])):
        weight = math.exp(-math.log(2.0) * age / 4.0)
        for player in data["starters"].get((game["game_id"], team_id), set()):
            current_scores[int(player)] += weight

    max_prior = max(prior_counts.values()) if prior_counts else 1
    pool = set(prior_counts) | set(current_scores) | incoming
    pool -= outgoing
    selection_score: dict[int, float] = {}
    values: dict[int, float] = {}
    for player in pool:
        prior_component = 0.35 * float(prior_counts.get(player, 0)) / max(1.0, float(max_prior))
        current_component = float(current_scores.get(player, 0.0))
        incoming_component = 0.10 if player in incoming else 0.0
        selection_score[int(player)] = current_component + prior_component + incoming_component
        val = v633._valuation(valuations, int(player), cutoff)
        if val is not None:
            values[int(player)] = float(val)

    valued = [(p, values[p], selection_score.get(p, 0.0)) for p in pool if p in values]
    if len(valued) < MIN_VALUED_CORE:
        return None
    expected = sorted(valued, key=lambda x: (x[2], x[1]), reverse=True)[:11]
    if len(expected) < MIN_VALUED_CORE:
        return None
    expected_ids = {int(p) for p, _v, _s in expected}
    expected_values = {int(p): float(v) for p, v, _s in expected}
    total_value = sum(expected_values.values())
    if total_value <= 0:
        return None

    last_games = current_games[-3:]
    lineups = [set(int(x) for x in data["starters"].get((g["game_id"], team_id), set())) for g in last_games]
    if any(len(s) < 8 for s in lineups):
        return None
    l3, l2, l1 = lineups[-3], lineups[-2], lineups[-1]

    miss1 = expected_ids - l1
    miss2 = expected_ids - l1 - l2
    miss3 = expected_ids - l1 - l2 - l3
    newly_missing = expected_ids & l2 - l1
    returners = expected_ids & l1 - l2

    expected_overlap_last1 = len(expected_ids & l1) / max(1, len(expected_ids))
    start_rate3 = sum(len(expected_ids & s) for s in (l3, l2, l1)) / max(1.0, 3.0 * len(expected_ids))
    union12 = l1 | l2
    continuity12 = len(l1 & l2) / max(1, len(union12))

    miss1_share = value_share(miss1, expected_values, total_value)
    miss2_share = value_share(miss2, expected_values, total_value)
    miss3_share = value_share(miss3, expected_values, total_value)
    newly_missing_share = value_share(newly_missing, expected_values, total_value)
    returner_share = value_share(returners, expected_values, total_value)
    absence_pressure = 0.50 * miss1_share + 0.30 * miss2_share + 0.20 * miss3_share

    return {
        "miss1_count": float(len(miss1)),
        "miss1_share": miss1_share,
        "miss2_count": float(len(miss2)),
        "miss2_share": miss2_share,
        "miss3_count": float(len(miss3)),
        "miss3_share": miss3_share,
        "expected_overlap_last1": expected_overlap_last1,
        "start_rate3": start_rate3,
        "lineup_continuity12": continuity12,
        "newly_missing_count": float(len(newly_missing)),
        "newly_missing_share": newly_missing_share,
        "returner_count": float(len(returners)),
        "returner_share": returner_share,
        "absence_pressure": absence_pressure,
        "absence_plus_new": absence_pressure + 0.50 * newly_missing_share,
        "absence_continuity": absence_pressure + 0.25 * (1.0 - continuity12),
        "expected_core_value": total_value,
        "prior_match_count": float(len(current_games)),
    }


def build_availability_map(base_rows: list[dict[str, Any]]) -> tuple[dict[tuple[str, str, str, str], dict[str, Any]], dict[str, Any]]:
    data_by_comp = {}
    indexes_by_comp = {}
    names_by_comp = {}
    for cid in v633.v6280.COMPS:
        data = v633.load_domain_data(cid, v633.CACHE)
        data_by_comp[cid] = data
        indexes_by_comp[cid] = v633.build_season_indexes(data)
        names_by_comp[cid] = g0.game_name_lookup(cid, v633.CACHE)

    wanted = v633._all_player_ids(data_by_comp)
    valuations, valuation_audit = v633._load_valuations(wanted)

    tm_rows = []
    misses = Counter()
    by_season = Counter()
    for cid in v633.v6280.COMPS:
        data = data_by_comp[cid]
        indexes = indexes_by_comp[cid]
        for season in SEASONS:
            for target in indexes["by_season"].get(season, []):
                hc = availability_context(int(target["home_id"]), season, target["date"], indexes, data, valuations)
                ac = availability_context(int(target["away_id"]), season, target["date"], indexes, data, valuations)
                if hc is None or ac is None:
                    misses[f"{season}:availability_context"] += 1
                    continue
                names = names_by_comp[cid].get(int(target["game_id"]))
                if not names:
                    misses[f"{season}:game_name"] += 1
                    continue
                tm_rows.append({
                    "competition_id": cid,
                    "season": season,
                    "date": target["date"].date().isoformat(),
                    "home_name": names[0],
                    "away_name": names[1],
                    "home_availability": hc,
                    "away_availability": ac,
                })
                by_season[season] += 1

    crosswalk, crosswalk_audit = g1.build_crosswalk(base_rows, tm_rows)
    amap = {}
    unmapped = duplicates = 0
    for r in tm_rows:
        cid = str(r["competition_id"])
        mh = crosswalk.get(cid, {}).get(str(r["home_name"]))
        ma = crosswalk.get(cid, {}).get(str(r["away_name"]))
        if not mh or not ma:
            unmapped += 1
            continue
        key = (cid, str(r["date"]), v651.v632._token(cid, mh), v651.v632._token(cid, ma))
        if key in amap:
            duplicates += 1
            continue
        amap[key] = {
            "home": r["home_availability"],
            "away": r["away_availability"],
        }
    return amap, {
        "tm_rows_by_season": dict(by_season),
        "misses": dict(misses),
        "unmapped_tm_rows": unmapped,
        "duplicate_keys": duplicates,
        "valuation_audit": valuation_audit,
        "crosswalk": crosswalk_audit,
        "target_lineup_used": False,
        "future_valuation_used": False,
        "future_transfer_used": False,
        "minimum_prior_games": MIN_PRIOR_GAMES,
    }


def join_historical() -> tuple[list[dict[str, Any]], dict[str, Any], dict[tuple[str, str, str, str], dict[str, Any]]]:
    rich, rich_audit = v651.build_historical()
    base_all, _base_audit, _names = v651.v632._build_rows()
    relevant_base = [r for r in base_all if str(r["season"]) in SEASONS]
    amap, availability_audit = build_availability_map(relevant_base)
    rows = []
    misses = Counter()
    for r in rich:
        cid = str(r["competition_id"])
        key = (cid, str(r["date"]), v651.v632._token(cid, str(r["home_team"])), v651.v632._token(cid, str(r["away_team"])))
        av = amap.get(key)
        if av is None:
            misses[f"{r['season']}:availability_join"] += 1
            continue
        z = dict(r)
        z["home_availability"] = av["home"]
        z["away_availability"] = av["away"]
        rows.append(z)
    return rows, {
        "rich_market": rich_audit,
        "availability": availability_audit,
        "joined_by_season": dict(Counter(r["season"] for r in rows)),
        "join_misses": dict(misses),
    }, amap


def market_margin(p: list[float]) -> float:
    s = sorted((float(x) for x in p), reverse=True)
    return s[0] - s[1]


def score(ctx: dict[str, float], spec: str) -> float:
    return float(ctx[spec])


def evaluate(rows: list[dict[str, Any]], spec: str, delta: float, min_miss1: float, margin_cap: float) -> dict[str, Any]:
    market_hits = candidate_hits = overrides = wins = losses = neutral = 0
    picks = Counter()
    actual = Counter()
    by_league = defaultdict(lambda: {"n": 0, "market_hits": 0, "candidate_hits": 0, "overrides": 0})
    for r in rows:
        p = [float(x) for x in r["market"]]
        y = int(r["y"])
        mp = max(range(3), key=lambda i: p[i])
        cp = mp
        do_override = False
        if mp in (0, 2) and market_margin(p) <= margin_cap + 1e-12:
            picked = r["home_availability"] if mp == 0 else r["away_availability"]
            opp = r["away_availability"] if mp == 0 else r["home_availability"]
            gap = score(picked, spec) - score(opp, spec)
            if float(picked["miss1_share"]) >= min_miss1 - 1e-12 and gap >= delta - 1e-12:
                cp = sorted(range(3), key=lambda i: p[i], reverse=True)[1]
                do_override = True
        market_hits += int(mp == y)
        candidate_hits += int(cp == y)
        overrides += int(do_override)
        if do_override:
            if cp == y and mp != y:
                wins += 1
            elif cp != y and mp == y:
                losses += 1
            else:
                neutral += 1
        picks[str(cp)] += 1
        actual[str(y)] += 1
        cid = str(r["competition_id"])
        by_league[cid]["n"] += 1
        by_league[cid]["market_hits"] += int(mp == y)
        by_league[cid]["candidate_hits"] += int(cp == y)
        by_league[cid]["overrides"] += int(do_override)
    n = len(rows)
    return {
        "count": n,
        "market_hits": market_hits,
        "candidate_hits": candidate_hits,
        "market_top1": market_hits / n if n else None,
        "candidate_top1": candidate_hits / n if n else None,
        "uplift_pp": 100.0 * (candidate_hits - market_hits) / n if n else None,
        "overrides": overrides,
        "override_wins": wins,
        "override_losses": losses,
        "override_neutral": neutral,
        "net_override_gain": wins - losses,
        "predicted_counts": dict(picks),
        "actual_counts": dict(actual),
        "by_league": dict(by_league),
        "proper_scores": "identical_to_market_by_construction",
    }


def load_a100_rows(amap: dict[tuple[str, str, str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    feats = [json.loads(x) for x in v651.FEATURES.read_text(encoding="utf-8").splitlines() if x.strip()]
    feats = [r for r in feats if r.get("partition") == v651.PART]
    feats.sort(key=lambda r: int(r["full_index"]))
    labels = []
    with v651.LABELS.open("r", encoding="utf-8") as h:
        for _ in range(100):
            lr = json.loads(h.readline())
            if lr.get("partition") != v651.PART or int(lr["full_index"]) != len(labels):
                raise RuntimeError("A100 label contract changed")
            labels.append(int(lr["label"]))
    rows = []
    misses = []
    for f in feats:
        idx = int(f["full_index"])
        cid = str(f["competition_id"])
        key = (cid, str(f["date"]), v651.v632._token(cid, str(f["home_team"])), v651.v632._token(cid, str(f["away_team"])))
        av = amap.get(key)
        if av is None:
            misses.append(idx)
            continue
        rows.append({
            "full_index": idx,
            "competition_id": cid,
            "market": [float(x) for x in f["market"]],
            "y": int(labels[idx]),
            "home_availability": av["home"],
            "away_availability": av["away"],
        })
    if len(rows) != 100:
        raise RuntimeError(f"A100 availability coverage incomplete: n={len(rows)} misses={misses}")
    return rows


def main() -> int:
    rows, audit, amap = join_historical()
    by_season = Counter(r["season"] for r in rows)
    if by_season.get(SELECT_SEASON, 0) < 900 or by_season.get(HOLDOUT_SEASON, 0) < 900:
        raise RuntimeError(f"historical availability coverage too small: {dict(by_season)}")
    selection = [r for r in rows if r["season"] == SELECT_SEASON]
    holdout = [r for r in rows if r["season"] == HOLDOUT_SEASON]

    board = []
    for spec in SCORE_SPECS:
        for delta in DELTA_THRESHOLDS:
            for min_miss1 in MIN_PICKED_MISS1:
                for cap in MARKET_MARGIN_CAPS:
                    met = evaluate(selection, spec, delta, min_miss1, cap)
                    if met["overrides"] >= MIN_SELECTION_OVERRIDES:
                        board.append({
                            "score_spec": spec,
                            "delta_threshold": delta,
                            "min_picked_miss1_share": min_miss1,
                            "market_margin_cap": cap,
                            "selection": met,
                        })
    if not board:
        raise RuntimeError("no V6.57 rule has minimum selection support")
    board.sort(
        key=lambda z: (
            z["selection"]["net_override_gain"],
            z["selection"]["uplift_pp"],
            -z["selection"]["overrides"],
            z["delta_threshold"],
            z["min_picked_miss1_share"],
            -z["market_margin_cap"],
        ),
        reverse=True,
    )
    chosen = board[0]
    hm = evaluate(
        holdout,
        str(chosen["score_spec"]),
        float(chosen["delta_threshold"]),
        float(chosen["min_picked_miss1_share"]),
        float(chosen["market_margin_cap"]),
    )
    historical_gate = bool(
        chosen["selection"]["uplift_pp"] > 0.0
        and hm["uplift_pp"] >= HOLDOUT_REQUIRED_UPLIFT_PP - 1e-12
        and hm["overrides"] >= MIN_HOLDOUT_OVERRIDES
    )

    payload: dict[str, Any] = {
        "schema_version": "V6.57.0-core-absence-intervention-full500-r1",
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "formal_weight": 0,
        "governance": {
            "availability_features_result_blind": True,
            "target_match_lineup_used": False,
            "future_valuation_used": False,
            "future_transfer_used": False,
            "selection_season": SELECT_SEASON,
            "holdout_season": HOLDOUT_SEASON,
            "A100_values_used_for_rule_selection": False,
            "probability_vector_modified": False,
            "B_CONFIRM300_labels_read": False,
            "C_SEALED100_labels_read": False,
            "CURRENT_unchanged": True,
        },
        "historical_audit": audit,
        "grid": {
            "score_specs": SCORE_SPECS,
            "delta_thresholds": DELTA_THRESHOLDS,
            "min_picked_miss1": MIN_PICKED_MISS1,
            "market_margin_caps": MARKET_MARGIN_CAPS,
            "minimum_selection_overrides": MIN_SELECTION_OVERRIDES,
            "minimum_holdout_overrides": MIN_HOLDOUT_OVERRIDES,
        },
        "selected_rule": chosen,
        "holdout_2024_25": hm,
        "historical_gate": historical_gate,
        "selection_leaderboard_top10": board[:10],
    }

    if not historical_gate:
        payload["A_FAST100"] = {"status": "NOT_OPENED_HISTORICAL_HOLDOUT_GATE_FAILED"}
        payload["next_step"] = "DO_NOT_OPEN_B300"
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    arows = load_a100_rows(amap)
    am = evaluate(
        arows,
        str(chosen["score_spec"]),
        float(chosen["delta_threshold"]),
        float(chosen["min_picked_miss1_share"]),
        float(chosen["market_margin_cap"]),
    )
    a_gate = {
        "required_candidate_hits": 63,
        "required_uplift_vs_market_pp": 3.0,
        "market_hits": am["market_hits"],
        "candidate_hits": am["candidate_hits"],
        "uplift_vs_market_pp": am["uplift_pp"],
        "top1_gate": am["candidate_hits"] >= 63,
        "uplift_gate": am["uplift_pp"] >= 3.0 - 1e-12,
        "proper_score_guard": True,
    }
    a_gate["A_FAST100_passed"] = bool(a_gate["top1_gate"] and a_gate["uplift_gate"])
    payload["A_FAST100"] = {
        "status": "SCORED_AFTER_HISTORICAL_HOLDOUT_GATE",
        "metrics": am,
        "gate": a_gate,
    }
    payload["next_step"] = "OPEN_B_CONFIRM300" if a_gate["A_FAST100_passed"] else "DO_NOT_OPEN_B300"
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
