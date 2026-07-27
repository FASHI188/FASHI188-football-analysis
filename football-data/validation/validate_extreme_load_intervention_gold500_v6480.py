#!/usr/bin/env python3
"""V6.48.0 extreme cross-competition load intervention.

Uses V6.47's strict-prior schedule-load data but does NOT perturb every match.
Selection is on 2023/24 only; 2024/25 is an untouched historical holdout.
A_FAST100 labels are opened only if the holdout passes.
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
for p in (ROOT / "validation", ROOT / "engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import validate_crosscompetition_load_gold500_v6470 as v647  # noqa: E402

OUT = ROOT / "manifests" / "v6_extreme_load_intervention_gold500_v6480_status.json"
SELECTION_SEASON = "2023/24"
HOLDOUT_SEASON = "2024/25"
PART = "A_FAST100"
RULES = ("short3_nonleague", "two_in7_nonleague", "rest_gap", "three_in14_nonleague")
MARGIN_CAPS = (0.05, 0.10, 0.15)
MIN_SELECTION_OVERRIDES = 10
MIN_HOLDOUT_OVERRIDES = 8
HOLDOUT_REQUIRED_UPLIFT_PP = 0.5
FAST_REQUIRED_HITS = 63
FAST_REQUIRED_UPLIFT_HITS = 3


def feature_map(row: dict[str, Any]) -> dict[str, float]:
    return {name: float(value) for name, value in zip(v647.ALL_FEATURE_NAMES, row["load_features"])}


def side_shocks(f: dict[str, float], rule: str) -> tuple[bool, bool]:
    if rule == "short3_nonleague":
        h = f["home_short_rest_3d"] >= 0.5 and f["home_nonleague_7d"] >= 1.0 and f["away_short_rest_3d"] < 0.5
        a = f["away_short_rest_3d"] >= 0.5 and f["away_nonleague_7d"] >= 1.0 and f["home_short_rest_3d"] < 0.5
    elif rule == "two_in7_nonleague":
        h = f["home_games_7d"] >= 2.0 and f["home_nonleague_7d"] >= 1.0 and f["away_games_7d"] <= 1.0
        a = f["away_games_7d"] >= 2.0 and f["away_nonleague_7d"] >= 1.0 and f["home_games_7d"] <= 1.0
    elif rule == "rest_gap":
        h = f["home_days_since_last"] <= 3.0 and f["away_days_since_last"] >= 5.0 and f["home_nonleague_7d"] >= 1.0
        a = f["away_days_since_last"] <= 3.0 and f["home_days_since_last"] >= 5.0 and f["away_nonleague_7d"] >= 1.0
    elif rule == "three_in14_nonleague":
        h = f["home_games_14d"] >= 3.0 and f["home_nonleague_14d"] >= 1.0 and f["away_games_14d"] <= 2.0
        a = f["away_games_14d"] >= 3.0 and f["away_nonleague_14d"] >= 1.0 and f["home_games_14d"] <= 2.0
    else:
        raise RuntimeError(f"unknown rule {rule}")
    return bool(h), bool(a)


def market_margin(p: list[float]) -> float:
    s = sorted((float(x) for x in p), reverse=True)
    return s[0] - s[1]


def market_second(p: list[float]) -> int:
    return sorted(range(3), key=lambda i: float(p[i]), reverse=True)[1]


def decision(row: dict[str, Any], rule: str, margin_cap: float) -> tuple[int, bool, str | None]:
    p = [float(x) for x in row["market"]]
    mp = max(range(3), key=lambda i: p[i])
    if mp == 1 or market_margin(p) > float(margin_cap):
        return mp, False, None
    hshock, ashock = side_shocks(feature_map(row), rule)
    if hshock == ashock:
        return mp, False, None
    shocked_pick = 0 if hshock else 2
    if mp != shocked_pick:
        return mp, False, None
    return market_second(p), True, "home" if hshock else "away"


def evaluate(rows: list[dict[str, Any]], rule: str, margin_cap: float) -> dict[str, Any]:
    market_hits = candidate_hits = overrides = wins = losses = neutral = 0
    by_league: dict[str, Counter] = {}
    audit = []
    for row in rows:
        y = int(row["y"]); p = [float(x) for x in row["market"]]
        mp = max(range(3), key=lambda i: p[i]); cp, changed, side = decision(row, rule, margin_cap)
        market_hits += int(mp == y); candidate_hits += int(cp == y)
        cid = str(row["competition_id"]); c = by_league.setdefault(cid, Counter())
        c["n"] += 1; c["market_hits"] += int(mp == y); c["candidate_hits"] += int(cp == y)
        if changed:
            overrides += 1; c["overrides"] += 1
            if cp == y and mp != y: wins += 1; c["wins"] += 1
            elif mp == y and cp != y: losses += 1; c["losses"] += 1
            else: neutral += 1; c["neutral"] += 1
            audit.append({"competition_id": cid, "date": str(row["date"]), "home_team": str(row["home_team"]), "away_team": str(row["away_team"]),
                          "actual": y, "market_pick": mp, "candidate_pick": cp, "shocked_side": side})
    n = len(rows)
    return {
        "count": n, "market_hits": market_hits, "candidate_hits": candidate_hits,
        "market_top1": market_hits/n if n else None, "candidate_top1": candidate_hits/n if n else None,
        "uplift_pp": ((candidate_hits-market_hits)/n*100.0) if n else None,
        "overrides": overrides, "override_wins": wins, "override_losses": losses, "override_neutral": neutral,
        "net_override_gain": wins-losses,
        "proper_scores": "identical_to_market_by_construction",
        "by_league": {cid: dict(v) for cid, v in by_league.items()},
        "override_audit": audit,
    }


def build_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    base_rows, base_audit, _ = v647.v632._build_rows()
    base_rows = [dict(r) for r in base_rows if str(r["season"]) in v647.TRAIN_SEASONS or str(r["season"]) == v647.TEST_SEASON]
    games, games_audit = v647.load_all_games(); histories = v647.build_histories(games)
    joined, attach_audit = v647.attach_load(base_rows, games, histories)
    return joined, {"base_status": base_audit.get("status") if isinstance(base_audit, dict) else None, "games": games_audit, "attach": attach_audit}


def main() -> int:
    rows, build_audit = build_rows()
    by_season = Counter(str(r["season"]) for r in rows)
    selection = [r for r in rows if str(r["season"]) == SELECTION_SEASON]
    holdout = [r for r in rows if str(r["season"]) == HOLDOUT_SEASON]
    if len(selection) < 900 or len(holdout) < 900:
        raise RuntimeError(f"coverage too small selection={len(selection)} holdout={len(holdout)}")

    leaderboard = []
    for rule in RULES:
        for cap in MARGIN_CAPS:
            res = evaluate(selection, rule, cap)
            leaderboard.append({"rule": rule, "margin_cap": cap, "selection": res})
    eligible = [x for x in leaderboard if x["selection"]["overrides"] >= MIN_SELECTION_OVERRIDES]
    if not eligible:
        eligible = leaderboard
    selected = min(eligible, key=lambda x: (-x["selection"]["candidate_hits"], -x["selection"]["net_override_gain"], x["selection"]["overrides"], RULES.index(x["rule"]), MARGIN_CAPS.index(x["margin_cap"])))
    hold = evaluate(holdout, str(selected["rule"]), float(selected["margin_cap"]))
    historical_gate = bool(
        selected["selection"]["uplift_pp"] >= 0.0
        and selected["selection"]["overrides"] >= MIN_SELECTION_OVERRIDES
        and hold["uplift_pp"] >= HOLDOUT_REQUIRED_UPLIFT_PP
        and hold["overrides"] >= MIN_HOLDOUT_OVERRIDES
    )

    if not historical_gate:
        fast_payload: dict[str, Any] = {"opened": False, "reason": "untouched 2024/25 holdout gate failed; A_FAST100 labels not read"}
        decision_text = "HISTORICAL_HOLDOUT_FAILED_A100_NOT_OPENED"
    else:
        gold_features = v647.load_jsonl(v647.GOLD_FEATURES)
        fast_features = [r for r in gold_features if str(r["partition"]) == PART]
        if len(gold_features) != 500 or len(fast_features) != 100:
            raise RuntimeError(f"Gold500 contract changed total={len(gold_features)} fast={len(fast_features)}")
        test_rows = [r for r in rows if str(r["season"]) == v647.TEST_SEASON]
        tmap = {(str(r["competition_id"]), str(r["date"]), v647.v632._token(str(r["competition_id"]), str(r["home_team"])), v647.v632._token(str(r["competition_id"]), str(r["away_team"]))): r for r in test_rows}
        labels = v647.load_fast100_labels_only(v647.GOLD_LABELS)
        fast = []
        for row in fast_features:
            cid = str(row["competition_id"]); key = (cid, str(row["date"]), v647.v632._token(cid, str(row["home_team"])), v647.v632._token(cid, str(row["away_team"])))
            src = tmap.get(key)
            if src is None: raise RuntimeError(f"Fast100 load join miss {key}")
            lab = labels[int(row["gold_index"])]
            fast.append({"gold_index": int(row["gold_index"]), "competition_id": cid, "date": str(row["date"]), "home_team": str(row["home_team"]), "away_team": str(row["away_team"]),
                         "market": [float(x) for x in row["market"]], "load_features": list(src["load_features"]), "y": int(lab["label"])})
        fres = evaluate(fast, str(selected["rule"]), float(selected["margin_cap"]))
        gate = bool(fres["candidate_hits"] >= FAST_REQUIRED_HITS and fres["candidate_hits"] >= fres["market_hits"] + FAST_REQUIRED_UPLIFT_HITS)
        fast_payload = {"opened": True, **fres, "gate_passed": gate, "required_hits": FAST_REQUIRED_HITS, "required_uplift_hits": FAST_REQUIRED_UPLIFT_HITS}
        decision_text = "OPEN_CONFIRM300" if gate else "FAST100_FAILED_CONFIRM300_NOT_OPENED"

    payload = {
        "schema_version": "V6.48.0-extreme-load-intervention-gold500-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS", "formal_current_version": "V5.0.1", "formal_weight": 0,
        "classification": "DEVELOPMENT_RESEARCH_EXTREME_LOAD_DECISION_INTERVENTION",
        "governance_contract": {
            "CURRENT_unchanged": True, "Gold500_identity_partition_unchanged": True,
            "uses_V647_strict_prior_load_only": True, "future_schedule_used": False,
            "proper_probabilities_modified": False, "proper_scores_equal_market": True,
            "selection_season_only": SELECTION_SEASON, "untouched_holdout_season": HOLDOUT_SEASON,
            "A_FAST100_labels_read_only_if_holdout_gate_passes": True,
            "B_CONFIRM300_labels_read": False, "B_CONFIRM300_scored": False, "C_SEALED100_labels_read": False,
            "league_dropping": False, "confidence_filtering": False, "seed_replacement": False, "A100_parameter_tuning": False,
        },
        "policy_contract": {"rules": list(RULES), "margin_caps": list(MARGIN_CAPS), "alternate_pick": "market_second_ranked_outcome",
                            "only_override_when_market_top1_is_uniquely_shocked_home_or_away_side": True,
                            "minimum_selection_overrides": MIN_SELECTION_OVERRIDES, "minimum_holdout_overrides": MIN_HOLDOUT_OVERRIDES,
                            "holdout_required_uplift_pp": HOLDOUT_REQUIRED_UPLIFT_PP},
        "build_audit": {"joined_by_season": dict(by_season), **build_audit},
        "selection_leaderboard": leaderboard,
        "selected_policy": selected,
        "holdout_2024_25": hold,
        "historical_holdout_gate_passed": historical_gate,
        "fast100": fast_payload,
        "decision": decision_text,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"selected": {"rule": selected["rule"], "margin_cap": selected["margin_cap"], "selection": selected["selection"]},
                      "holdout": hold, "historical_holdout_gate_passed": historical_gate, "fast100": fast_payload, "decision": decision_text}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
