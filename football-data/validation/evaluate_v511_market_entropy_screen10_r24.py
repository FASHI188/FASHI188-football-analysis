#!/usr/bin/env python3
"""R24 fixed ten-pair 1X2 entropy screen.

R22 draw probability alone scored 5/10; R23 balance-times-under scored 4/10.
R24 tests a third distinct static-market hypothesis: whether normalized no-vig 1X2
entropy (overall market uncertainty) is higher for known draws than for similar one-goal
results. Matching uses competition, season, date, totals and handicap, and excludes all
1X2 probabilities from the matching cost.

Retrospective screening only; formal_weight remains zero.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import linear_sum_assignment

from evaluate_v511_market_draw_screen10_r22 import (
    ROOT,
    ScreenError,
    extract_rows,
    hash_rank,
    load_json,
    optional_distance,
)

DEFAULT_CONFIG = ROOT / "config" / "v511_market_entropy_screen10_r24.json"
DEFAULT_OUT = ROOT / "manifests" / "v511_market_entropy_screen10_r24_status.json"
DEFAULT_PAIRS = ROOT / "manifests" / "v511_market_entropy_screen10_r24_pairs.csv"


def entropy_score(row: dict[str, Any]) -> float:
    values = [float(row["p_home"]), float(row["p_draw"]), float(row["p_away"])]
    entropy = -sum(value * math.log(value) for value in values if value > 0)
    return float(entropy / math.log(3.0))


def eligible_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows, errors = extract_rows()
    for row in rows:
        row["market_entropy_score"] = entropy_score(row)
    return rows, errors


def select_draws(rows: list[dict[str, Any]], count: int, seed: int) -> list[dict[str, Any]]:
    by_competition: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if int(row["goal_difference"]) == 0:
            by_competition.setdefault(str(row["competition_id"]), []).append(row)
    representatives: list[dict[str, Any]] = []
    for competition, values in sorted(by_competition.items()):
        values.sort(key=lambda row: (hash_rank(seed, row["identity"]), row["identity"]))
        representatives.append(values[0])
    representatives.sort(key=lambda row: (hash_rank(seed, f"competition|{row['competition_id']}"), row["competition_id"]))
    if len(representatives) < count:
        raise ScreenError(f"only {len(representatives)} competitions have eligible 1X2 draws; need {count}")
    return representatives[:count]


def pair_cost(draw: dict[str, Any], win: dict[str, Any]) -> float:
    if draw["competition_id"] != win["competition_id"]:
        return 1e9
    season_penalty = 0.0 if draw["season"] == win["season"] else 9.0
    date_gap = (min(abs(int(draw["date_ordinal"]) - int(win["date_ordinal"])), 730) / 180.0) ** 2
    total_line = optional_distance(draw.get("total_line"), win.get("total_line"), 0.5)
    over_probability = optional_distance(draw.get("over_probability"), win.get("over_probability"), 0.10)
    asian_line = optional_distance(draw.get("asian_line"), win.get("asian_line"), 0.5)
    return float(season_penalty + 0.25 * date_gap + 0.5 * total_line + 0.5 * over_probability + 0.5 * asian_line)


def make_pairs(draws: list[dict[str, Any]], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    wins = [row for row in rows if abs(int(row["goal_difference"])) == 1]
    cost = np.asarray([[pair_cost(draw, win) for win in wins] for draw in draws], dtype=float)
    draw_index, win_index = linear_sum_assignment(cost)
    pairs: list[dict[str, Any]] = []
    for left, right in zip(draw_index, win_index):
        draw = draws[int(left)]
        win = wins[int(right)]
        if draw["competition_id"] != win["competition_id"] or cost[int(left), int(right)] >= 1e8:
            raise ScreenError(f"no same-competition R24 pair for {draw['identity']}")
        difference = float(draw["market_entropy_score"] - win["market_entropy_score"])
        result = "win" if difference > 1e-12 else "loss" if difference < -1e-12 else "tie"
        pairs.append({
            "competition_id": draw["competition_id"],
            "draw_identity": draw["identity"],
            "draw_score": f"{draw['home_goals_90']}-{draw['away_goals_90']}",
            "draw_source": draw["source_file"],
            "draw_entropy": draw["market_entropy_score"],
            "draw_p_home": draw["p_home"],
            "draw_p_draw": draw["p_draw"],
            "draw_p_away": draw["p_away"],
            "close_win_identity": win["identity"],
            "close_win_score": f"{win['home_goals_90']}-{win['away_goals_90']}",
            "close_win_source": win["source_file"],
            "close_win_entropy": win["market_entropy_score"],
            "close_win_p_home": win["p_home"],
            "close_win_p_draw": win["p_draw"],
            "close_win_p_away": win["p_away"],
            "entropy_difference": difference,
            "pair_result": result,
            "matching_cost": float(cost[int(left), int(right)]),
            "same_season": draw["season"] == win["season"],
            "date_gap_days": abs(int(draw["date_ordinal"]) - int(win["date_ordinal"])),
            "draw_total_line": draw.get("total_line"),
            "close_win_total_line": win.get("total_line"),
            "draw_over_probability": draw.get("over_probability"),
            "close_win_over_probability": win.get("over_probability"),
            "draw_asian_line": draw.get("asian_line"),
            "close_win_asian_line": win.get("asian_line"),
        })
    return sorted(pairs, key=lambda row: (row["competition_id"], row["draw_identity"]))


def write_pairs(path: Path, pairs: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(pairs[0].keys()) if pairs else []
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(pairs)


def run(config: dict[str, Any], out_path: Path, pairs_path: Path) -> dict[str, Any]:
    rows, errors = eligible_rows()
    contract = config["sample_contract"]
    draws = select_draws(rows, int(contract["draws"]), int(contract["seed"]))
    pairs = make_pairs(draws, rows)
    wins = sum(row["pair_result"] == "win" for row in pairs)
    losses = sum(row["pair_result"] == "loss" for row in pairs)
    ties = sum(row["pair_result"] == "tie" for row in pairs)
    passed = wins >= int(contract["pass_wins"])
    identity_text = "\n".join(row["draw_identity"] for row in pairs)
    receipt = {
        "schema_version": "v511_market_entropy_screen10_r24_status.1",
        "status": (
            "PASS_R24_MARKET_ENTROPY_SCREEN10_ADVANCE_TO_30"
            if passed else "FAIL_R24_MARKET_ENTROPY_SCREEN10_STOP"
        ),
        "classification": config["classification"],
        "formal_weight": 0,
        "eligible_1x2_rows": len(rows),
        "file_errors": errors,
        "sample": {
            "pairs": len(pairs),
            "competitions": len({row["competition_id"] for row in pairs}),
            "wins": wins,
            "losses": losses,
            "ties": ties,
            "accuracy": wins / len(pairs),
            "pass_wins": int(contract["pass_wins"]),
            "passed": passed,
            "mean_entropy_difference": float(np.mean([row["entropy_difference"] for row in pairs])),
            "median_entropy_difference": float(np.median([row["entropy_difference"] for row in pairs])),
            "selected_draw_identity_sha256": hashlib.sha256(identity_text.encode("utf-8")).hexdigest(),
        },
        "signal_contract": config["signal_contract"],
        "sample_contract": contract,
        "matching_contract": config["matching_contract"],
        "ruling": {
            "advance_to_30_pair_gate": passed,
            "no_repeated_resampling_allowed": True,
            "retrospective_market_reference_only": True,
            "formal_pre_match_snapshot_claim_allowed": False,
            "model_training_performed": False,
            "outcome_tuned_weights_used": False,
            "manual_draw_offset_used": False,
            "class_weight_used": False,
            "threshold_tuning_used": False,
            "current_match_probability_allowed": False,
            "unified_matrix_allowed": False,
            "exact_score_allowed": False,
            "ev_allowed": False,
        },
        "hard_limits": config["hard_limits"],
    }
    write_pairs(pairs_path, pairs)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return receipt


def self_test() -> None:
    uniform = {"p_home": 1/3, "p_draw": 1/3, "p_away": 1/3}
    if not math.isclose(entropy_score(uniform), 1.0, abs_tol=1e-12):
        raise ScreenError("R24 entropy self-test failed")
    print(json.dumps({"self_test": "PASS", "entropy": entropy_score(uniform)}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--pairs", type=Path, default=DEFAULT_PAIRS)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    print(json.dumps(run(load_json(args.config), args.out, args.pairs), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
