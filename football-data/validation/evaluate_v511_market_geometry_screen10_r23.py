#!/usr/bin/env python3
"""R23 fixed ten-pair market-geometry screen.

R22 showed that de-vig closing draw probability alone scored 5/10 and therefore stopped.
R23 tests a distinct, label-free market geometry signal on a new deterministic ten-draw
sample: team-strength balance from 1X2 multiplied by de-vig under probability. Matching
uses competition, season, date, conditional home share and handicap line, but excludes the
under probability that defines the new signal.

All prices are retrospective closing references unless original quote provenance exists.
Research only; formal_weight remains zero.
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

DEFAULT_CONFIG = ROOT / "config" / "v511_market_geometry_screen10_r23.json"
DEFAULT_OUT = ROOT / "manifests" / "v511_market_geometry_screen10_r23_status.json"
DEFAULT_PAIRS = ROOT / "manifests" / "v511_market_geometry_screen10_r23_pairs.csv"


def geometry_score(row: dict[str, Any]) -> float:
    q = float(row["home_share_non_draw"])
    balance = max(0.0, min(1.0, 4.0 * q * (1.0 - q)))
    p_under = 1.0 - float(row["over_probability"])
    return float(balance * p_under)


def eligible_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows, errors = extract_rows()
    eligible = [
        row for row in rows
        if row.get("over_probability") is not None
        and math.isfinite(float(row["over_probability"]))
        and row.get("total_line") is not None
        and math.isfinite(float(row["total_line"]))
    ]
    for row in eligible:
        row["market_geometry_score"] = geometry_score(row)
    return eligible, errors


def select_draws(rows: list[dict[str, Any]], count: int, seed: int, max_per_competition: int) -> list[dict[str, Any]]:
    by_competition: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if int(row["goal_difference"]) == 0:
            by_competition.setdefault(str(row["competition_id"]), []).append(row)
    for values in by_competition.values():
        values.sort(key=lambda row: (hash_rank(seed, row["identity"]), row["identity"]))

    competition_order = sorted(
        by_competition,
        key=lambda competition: (hash_rank(seed, f"competition|{competition}"), competition),
    )
    selected: list[dict[str, Any]] = [by_competition[competition][0] for competition in competition_order]
    selected = selected[:count]
    used = {row["identity"] for row in selected}
    counts = {competition: 0 for competition in by_competition}
    for row in selected:
        counts[str(row["competition_id"])] += 1

    extras = sorted(
        (row for values in by_competition.values() for row in values if row["identity"] not in used),
        key=lambda row: (hash_rank(seed, f"extra|{row['identity']}"), row["identity"]),
    )
    for row in extras:
        if len(selected) >= count:
            break
        competition = str(row["competition_id"])
        if counts[competition] >= max_per_competition:
            continue
        selected.append(row)
        counts[competition] += 1
    if len(selected) < count:
        raise ScreenError(
            f"only {len(selected)} eligible draws after competition cap {max_per_competition}; need {count}"
        )
    return selected


def pair_cost(draw: dict[str, Any], win: dict[str, Any]) -> float:
    if draw["competition_id"] != win["competition_id"]:
        return 1e9
    season_penalty = 0.0 if draw["season"] == win["season"] else 9.0
    home_share = ((float(draw["home_share_non_draw"]) - float(win["home_share_non_draw"])) / 0.10) ** 2
    date_gap = (min(abs(int(draw["date_ordinal"]) - int(win["date_ordinal"])), 730) / 180.0) ** 2
    asian_line = optional_distance(draw.get("asian_line"), win.get("asian_line"), 0.5)
    return float(season_penalty + home_share + 0.25 * date_gap + 0.5 * asian_line)


def make_pairs(draws: list[dict[str, Any]], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    wins = [row for row in rows if abs(int(row["goal_difference"])) == 1]
    if not wins:
        raise ScreenError("no eligible 1X2+OU one-goal wins")
    cost = np.asarray([[pair_cost(draw, win) for win in wins] for draw in draws], dtype=float)
    draw_index, win_index = linear_sum_assignment(cost)
    pairs: list[dict[str, Any]] = []
    for left, right in zip(draw_index, win_index):
        draw = draws[int(left)]
        win = wins[int(right)]
        if draw["competition_id"] != win["competition_id"] or cost[int(left), int(right)] >= 1e8:
            raise ScreenError(f"no same-competition R23 pair for {draw['identity']}")
        difference = float(draw["market_geometry_score"] - win["market_geometry_score"])
        result = "win" if difference > 1e-12 else "loss" if difference < -1e-12 else "tie"
        pairs.append({
            "competition_id": draw["competition_id"],
            "draw_identity": draw["identity"],
            "draw_score": f"{draw['home_goals_90']}-{draw['away_goals_90']}",
            "draw_source": draw["source_file"],
            "draw_geometry_score": draw["market_geometry_score"],
            "draw_home_share_non_draw": draw["home_share_non_draw"],
            "draw_under_probability": 1.0 - float(draw["over_probability"]),
            "draw_total_line": draw["total_line"],
            "draw_asian_line": draw["asian_line"],
            "close_win_identity": win["identity"],
            "close_win_score": f"{win['home_goals_90']}-{win['away_goals_90']}",
            "close_win_source": win["source_file"],
            "close_win_geometry_score": win["market_geometry_score"],
            "close_win_home_share_non_draw": win["home_share_non_draw"],
            "close_win_under_probability": 1.0 - float(win["over_probability"]),
            "close_win_total_line": win["total_line"],
            "close_win_asian_line": win["asian_line"],
            "geometry_difference": difference,
            "pair_result": result,
            "matching_cost": float(cost[int(left), int(right)]),
            "same_season": draw["season"] == win["season"],
            "date_gap_days": abs(int(draw["date_ordinal"]) - int(win["date_ordinal"])),
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
    draws = select_draws(
        rows,
        int(contract["draws"]),
        int(contract["seed"]),
        int(contract["max_draws_per_competition"]),
    )
    pairs = make_pairs(draws, rows)
    wins = sum(row["pair_result"] == "win" for row in pairs)
    losses = sum(row["pair_result"] == "loss" for row in pairs)
    ties = sum(row["pair_result"] == "tie" for row in pairs)
    passed = wins >= int(contract["pass_wins"])
    identity_text = "\n".join(row["draw_identity"] for row in pairs)
    receipt = {
        "schema_version": "v511_market_geometry_screen10_r23_status.1",
        "status": (
            "PASS_R23_MARKET_GEOMETRY_SCREEN10_ADVANCE_TO_30"
            if passed else "FAIL_R23_MARKET_GEOMETRY_SCREEN10_STOP"
        ),
        "classification": config["classification"],
        "formal_weight": 0,
        "eligible_1x2_ou_rows": len(rows),
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
            "mean_geometry_difference": float(np.mean([row["geometry_difference"] for row in pairs])),
            "median_geometry_difference": float(np.median([row["geometry_difference"] for row in pairs])),
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
    row = {"home_share_non_draw": 0.5, "over_probability": 0.4}
    if not math.isclose(geometry_score(row), 0.6, abs_tol=1e-12):
        raise ScreenError("R23 geometry score self-test failed")
    print(json.dumps({"self_test": "PASS", "geometry_score": geometry_score(row)}, indent=2))


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
