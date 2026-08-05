#!/usr/bin/env python3
"""Build separate sealed blind packets for low-score and exchange draw mechanisms.

R30A targets 0-0/1-1 and matches them to 1-0/0-1 controls.
R30B targets 2-2/3-3 and matches them to 2-1/1-2/3-2/2-3 controls.
The exact R29 sample is excluded. Public artifacts contain fixture and market data only;
labels and scores are written to separate sealed files.
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

import evaluate_v511_market_draw_screen10_r22 as r22
import build_v511_historical_blind_context_screen10_r29 as r29

ROOT = r22.ROOT
DEFAULT_CONFIG = ROOT / "config" / "v511_split_draw_mechanisms_screen10_r30.json"
DEFAULT_OUT = ROOT / "manifests" / "v511_split_draw_mechanisms_screen10_r30_status.json"
DEFAULT_PUBLIC_DIR = ROOT / "manifests" / "r30_public"
DEFAULT_HIDDEN_DIR = ROOT / "manifests" / "r30_hidden"


class R30Error(RuntimeError):
    pass


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise R30Error("config root must be object")
    return value


def score(row: dict[str, Any]) -> tuple[int, int]:
    return int(row["home_goals_90"]), int(row["away_goals_90"])


def hash_rank(seed: int, prefix: str, identity: str) -> str:
    return sha256_text(f"{seed}|{prefix}|{identity}")


def r29_excluded_identities(rows: list[dict[str, Any]]) -> set[str]:
    draws = r22.select_draws(rows, 10, 51122)
    pairs = r22.make_pairs(draws, rows)
    excluded: set[str] = set()
    for pair in pairs:
        excluded.add(str(pair["draw_identity"]))
        excluded.add(str(pair["close_win_identity"]))
    return excluded


def choose_targets(
    candidates: list[dict[str, Any]], count: int, seed: int, max_per_competition: int
) -> list[dict[str, Any]]:
    by_comp: dict[str, list[dict[str, Any]]] = {}
    for row in candidates:
        by_comp.setdefault(str(row["competition_id"]), []).append(row)
    for comp, values in by_comp.items():
        values.sort(key=lambda row: (hash_rank(seed, "row", row["identity"]), row["identity"]))

    comp_order = sorted(by_comp, key=lambda comp: (hash_rank(seed, "comp", comp), comp))
    chosen: list[dict[str, Any]] = []
    counts: dict[str, int] = {comp: 0 for comp in by_comp}
    for comp in comp_order:
        if len(chosen) >= count:
            break
        chosen.append(by_comp[comp][0])
        counts[comp] += 1

    extras = sorted(
        [row for comp in comp_order for row in by_comp[comp][1:]],
        key=lambda row: (hash_rank(seed, "extra", row["identity"]), row["identity"]),
    )
    for row in extras:
        if len(chosen) >= count:
            break
        comp = str(row["competition_id"])
        if counts[comp] >= max_per_competition:
            continue
        chosen.append(row)
        counts[comp] += 1
    if len(chosen) < count:
        raise R30Error(
            f"only {len(chosen)} targets available with max {max_per_competition} per competition; need {count}"
        )
    return chosen


def optional_distance(left: Any, right: Any, scale: float, missing_penalty: float = 0.5) -> float:
    try:
        left_value = float(left)
        right_value = float(right)
    except (TypeError, ValueError):
        return missing_penalty
    if not (math.isfinite(left_value) and math.isfinite(right_value)):
        return missing_penalty
    return ((left_value - right_value) / scale) ** 2


def pair_cost(target: dict[str, Any], control: dict[str, Any], lane: str) -> float:
    competition_penalty = 0.0 if target["competition_id"] == control["competition_id"] else 25.0
    season_penalty = 0.0 if target["season"] == control["season"] else 4.0
    date_gap = (min(abs(int(target["date_ordinal"]) - int(control["date_ordinal"])), 1460) / 365.0) ** 2
    p_draw = optional_distance(target.get("p_draw"), control.get("p_draw"), 0.03)
    home_share = optional_distance(target.get("home_share_non_draw"), control.get("home_share_non_draw"), 0.10)
    total_line = optional_distance(target.get("total_line"), control.get("total_line"), 0.50)
    over_probability = optional_distance(target.get("over_probability"), control.get("over_probability"), 0.10)
    goal_total_penalty = 0.0
    if lane == "R30B":
        goal_total_penalty = ((sum(score(target)) - sum(score(control))) / 2.0) ** 2
    return float(
        competition_penalty + season_penalty + 0.25 * date_gap + p_draw + home_share
        + 0.5 * total_line + 0.5 * over_probability + goal_total_penalty
    )


def match_controls(
    targets: list[dict[str, Any]], controls: list[dict[str, Any]], lane: str
) -> list[tuple[dict[str, Any], dict[str, Any], float]]:
    if len(controls) < len(targets):
        raise R30Error(f"{lane}: only {len(controls)} controls for {len(targets)} targets")
    matrix = np.asarray(
        [[pair_cost(target, control, lane) for control in controls] for target in targets],
        dtype=float,
    )
    left, right = linear_sum_assignment(matrix)
    if len(left) != len(targets):
        raise R30Error(f"{lane}: matching failed to cover all targets")
    return [
        (targets[int(i)], controls[int(j)], float(matrix[int(i), int(j)]))
        for i, j in zip(left, right)
    ]


def candidate_packet(row: dict[str, Any]) -> dict[str, Any]:
    source_row, headers = r29.read_source_row(row["source_file"], int(row["row_number"]))
    return {
        "competition_id": row["competition_id"],
        "season": row["season"],
        "match_date": row["date_key"],
        "home_team": row["home_team"],
        "away_team": row["away_team"],
        "source_file": row["source_file"],
        "source_row_number": row["row_number"],
        "market": r29.market_packet(source_row, headers),
        "context_template": {
            "task_state": 0,
            "attacking_availability": 0,
            "defensive_spine": 0,
            "source_records": [],
            "notes": "unfilled until timestamped pre-match reconstruction"
        },
    }


def forbidden_candidate_key(value: Any) -> bool:
    forbidden = ("score", "label", "result", "home_goals", "away_goals", "goal_difference")
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).casefold()
            if any(token in lowered for token in forbidden):
                return True
            if forbidden_candidate_key(child):
                return True
    elif isinstance(value, list):
        return any(forbidden_candidate_key(item) for item in value)
    return False


def write_public_csv(path: Path, public_pairs: list[dict[str, Any]]) -> None:
    fields = [
        "pair_id", "side", "competition_id", "season", "match_date", "home_team", "away_team",
        "open_draw_probability", "close_draw_probability", "draw_probability_change",
        "total_line", "open_under_probability", "close_under_probability", "under_probability_change",
        "open_asian_line", "close_asian_line", "asian_abs_line_change",
        "source_file", "source_row_number",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for pair in public_pairs:
            for candidate in pair["candidates"]:
                market = candidate["market"]
                opening = market["one_x_two"]["opening"]
                closing = market["one_x_two"]["closing"]
                open_ou = market["over_under"]["opening_prices"]
                close_ou = market["over_under"]["closing_prices"]
                movement = market["derived_movement"]
                writer.writerow({
                    "pair_id": pair["pair_id"],
                    "side": candidate["side"],
                    "competition_id": candidate["competition_id"],
                    "season": candidate["season"],
                    "match_date": candidate["match_date"],
                    "home_team": candidate["home_team"],
                    "away_team": candidate["away_team"],
                    "open_draw_probability": opening["de_vig"]["draw"] if opening else None,
                    "close_draw_probability": closing["de_vig"]["draw"] if closing else None,
                    "draw_probability_change": movement["draw_probability_change"],
                    "total_line": market["over_under"]["line"],
                    "open_under_probability": open_ou["de_vig"]["second"] if open_ou else None,
                    "close_under_probability": close_ou["de_vig"]["second"] if close_ou else None,
                    "under_probability_change": movement["under_probability_change"],
                    "open_asian_line": market["asian_handicap"]["opening_line"],
                    "close_asian_line": market["asian_handicap"]["closing_line"],
                    "asian_abs_line_change": movement["asian_abs_line_change"],
                    "source_file": candidate["source_file"],
                    "source_row_number": candidate["source_row_number"],
                })


def build_lane(
    lane_id: str, lane_config: dict[str, Any], rows: list[dict[str, Any]], excluded: set[str],
    public_dir: Path, hidden_dir: Path,
) -> dict[str, Any]:
    target_scores = {tuple(item) for item in lane_config["target_scores"]}
    control_scores = {tuple(item) for item in lane_config["control_scores"]}
    eligible = [row for row in rows if row["identity"] not in excluded]
    targets = [row for row in eligible if score(row) in target_scores]
    controls = [row for row in eligible if score(row) in control_scores]
    chosen = choose_targets(
        targets,
        int(lane_config["pairs"]),
        int(lane_config["seed"]),
        int(lane_config["max_targets_per_competition"]),
    )
    matched = match_controls(chosen, controls, lane_id)

    public_pairs: list[dict[str, Any]] = []
    hidden_rows: list[dict[str, Any]] = []
    for index, (target, control, cost) in enumerate(matched, start=1):
        pair_id = f"{lane_id}-{index:02d}"
        pair_key = sha256_text(f"{lane_config['seed']}|{target['identity']}|{control['identity']}")
        target_is_a = int(pair_key[-1], 16) % 2 == 0
        ordering = (
            [("A", target, "target"), ("B", control, "control")]
            if target_is_a else [("A", control, "control"), ("B", target, "target")]
        )
        candidates: list[dict[str, Any]] = []
        hidden: dict[str, Any] = {"pair_id": pair_id, "matching_cost": cost}
        for side, row, role in ordering:
            packet = candidate_packet(row)
            packet["side"] = side
            candidates.append(packet)
            hidden[f"side_{side}_role"] = role
            hidden[f"side_{side}_final_score"] = f"{row['home_goals_90']}-{row['away_goals_90']}"
            hidden[f"side_{side}_identity"] = row["identity"]
        if forbidden_candidate_key(candidates):
            raise R30Error(f"{lane_id}: public candidate leaked outcome key")
        public_pairs.append({
            "pair_id": pair_id,
            "pair_identity_sha256": pair_key,
            "candidates": candidates,
        })
        hidden_rows.append(hidden)

    hidden_payload = {
        "schema_version": f"v511_{lane_id.casefold()}_hidden_labels.1",
        "lane": lane_id,
        "labels": hidden_rows,
    }
    hidden_seal = sha256_text(canonical(hidden_payload))
    public_payload = {
        "schema_version": f"v511_{lane_id.casefold()}_public_blind_packet.1",
        "classification": "VIEWED_HISTORICAL_SPLIT_DRAW_BLIND_RECONSTRUCTION_RESEARCH",
        "formal_weight": 0,
        "lane": lane_id,
        "lane_name": lane_config["name"],
        "label_seal_sha256": hidden_seal,
        "sample_contract": {
            "seed": lane_config["seed"],
            "pairs": lane_config["pairs"],
            "pass_pairs": lane_config["pass_pairs"],
            "r29_identities_excluded": True,
            "no_resampling": True,
            "ties_count_as_failure": True,
        },
        "rubric": lane_config["rubric"],
        "pairs": public_pairs,
    }

    public_json = public_dir / f"v511_{lane_id.casefold()}_public.json"
    public_csv = public_dir / f"v511_{lane_id.casefold()}_public.csv"
    hidden_json = hidden_dir / f"v511_{lane_id.casefold()}_hidden_labels.json"
    public_dir.mkdir(parents=True, exist_ok=True)
    hidden_dir.mkdir(parents=True, exist_ok=True)
    public_json.write_text(json.dumps(public_payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    write_public_csv(public_csv, public_pairs)
    hidden_json.write_text(json.dumps(hidden_payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return {
        "lane": lane_id,
        "pairs": len(public_pairs),
        "competitions": len({c["competition_id"] for p in public_pairs for c in p["candidates"]}),
        "public_packet_sha256": sha256_text(public_json.read_text(encoding="utf-8")),
        "hidden_label_seal_sha256": hidden_seal,
        "public_json": str(public_json.relative_to(ROOT)),
        "public_csv": str(public_csv.relative_to(ROOT)),
        "hidden_json": str(hidden_json.relative_to(ROOT)),
    }


def run(config: dict[str, Any], out_path: Path, public_dir: Path, hidden_dir: Path) -> dict[str, Any]:
    rows, errors = r22.extract_rows()
    if errors:
        raise R30Error(f"source extraction errors: {errors}")
    excluded = r29_excluded_identities(rows)
    lanes = [
        build_lane(lane_id, lane_config, rows, excluded, public_dir, hidden_dir)
        for lane_id, lane_config in config["lanes"].items()
    ]
    status = {
        "schema_version": "v511_split_draw_mechanisms_screen10_r30_status.1",
        "status": "PASS_R30A_R30B_NEW_BLIND_PACKETS_BUILT",
        "classification": config["classification"],
        "formal_weight": 0,
        "r29_excluded_identity_count": len(excluded),
        "lanes": lanes,
        "ruling": {
            "public_and_hidden_artifacts_separated": True,
            "hidden_labels_must_not_be_downloaded_before_predictions_frozen": True,
            "resampling_allowed": False,
            "training_performed": False,
            "formal_promotion_allowed": False,
        },
        "hard_limits": config["hard_limits"],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(status, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return status


def self_test() -> None:
    candidate = {"fixture": {"home_team": "A", "away_team": "B"}, "market": {"p": 0.3}}
    assert forbidden_candidate_key(candidate) is False
    assert forbidden_candidate_key({"final_score": "1-1"}) is True
    assert sha256_text(canonical({"a": 1})) == sha256_text(canonical({"a": 1}))
    print(json.dumps({"self_test": "PASS"}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--public-dir", type=Path, default=DEFAULT_PUBLIC_DIR)
    parser.add_argument("--hidden-dir", type=Path, default=DEFAULT_HIDDEN_DIR)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    print(json.dumps(run(load_json(args.config), args.out, args.public_dir, args.hidden_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
