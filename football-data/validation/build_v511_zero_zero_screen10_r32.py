#!/usr/bin/env python3
"""Build the fixed R32 0-0 versus 1-0/0-1 blind packet.

All identities used by R29 and R30 are deterministically reconstructed and excluded.
Public output contains fixture and market inputs only. Scores and target/control roles are
written to a separate SHA-256 sealed artifact.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import evaluate_v511_market_draw_screen10_r22 as r22
import build_v511_split_draw_mechanisms_screen10_r30 as r30

ROOT = r22.ROOT
DEFAULT_CONFIG = ROOT / "config" / "v511_zero_zero_screen10_r32.json"
DEFAULT_R30_CONFIG = ROOT / "config" / "v511_split_draw_mechanisms_screen10_r30.json"
DEFAULT_STATUS = ROOT / "manifests" / "v511_zero_zero_screen10_r32_status.json"
DEFAULT_PUBLIC = ROOT / "manifests" / "v511_zero_zero_screen10_r32_public.json"
DEFAULT_PUBLIC_CSV = ROOT / "manifests" / "v511_zero_zero_screen10_r32_public.csv"
DEFAULT_HIDDEN = ROOT / "manifests" / "v511_zero_zero_screen10_r32_hidden_labels.json"


class R32Error(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise R32Error(f"JSON root must be object: {path}")
    return value


def score(row: dict[str, Any]) -> tuple[int, int]:
    return int(row["home_goals_90"]), int(row["away_goals_90"])


def previous_research_exclusions(
    rows: list[dict[str, Any]], r30_config: dict[str, Any]
) -> tuple[set[str], int, int]:
    excluded = r30.r29_excluded_identities(rows)
    r29_count = len(excluded)
    eligible = [row for row in rows if row["identity"] not in excluded]
    r30_ids: set[str] = set()
    for lane_id, lane_config in r30_config["lanes"].items():
        target_scores = {tuple(item) for item in lane_config["target_scores"]}
        control_scores = {tuple(item) for item in lane_config["control_scores"]}
        targets = [row for row in eligible if score(row) in target_scores]
        controls = [row for row in eligible if score(row) in control_scores]
        chosen = r30.choose_targets(
            targets,
            int(lane_config["pairs"]),
            int(lane_config["seed"]),
            int(lane_config["max_targets_per_competition"]),
        )
        matched = r30.match_controls(chosen, controls, lane_id)
        for target, control, _ in matched:
            r30_ids.add(str(target["identity"]))
            r30_ids.add(str(control["identity"]))
    excluded |= r30_ids
    return excluded, r29_count, len(r30_ids)


def build(
    config: dict[str, Any], r30_config: dict[str, Any], status_path: Path,
    public_path: Path, public_csv_path: Path, hidden_path: Path,
) -> dict[str, Any]:
    rows, errors = r22.extract_rows()
    if errors:
        raise R32Error(f"source extraction errors: {errors}")
    excluded, r29_count, r30_count = previous_research_exclusions(rows, r30_config)
    eligible = [row for row in rows if row["identity"] not in excluded]
    target_scores = {tuple(item) for item in config["target_scores"]}
    control_scores = {tuple(item) for item in config["control_scores"]}
    targets = [row for row in eligible if score(row) in target_scores]
    controls = [row for row in eligible if score(row) in control_scores]
    chosen = r30.choose_targets(
        targets,
        int(config["pairs"]),
        int(config["seed"]),
        int(config["max_targets_per_competition"]),
    )
    matched = r30.match_controls(chosen, controls, "R30A")

    public_pairs: list[dict[str, Any]] = []
    hidden_rows: list[dict[str, Any]] = []
    for index, (target, control, cost) in enumerate(matched, start=1):
        pair_id = f"R32-{index:02d}"
        pair_key = r30.sha256_text(f"{config['seed']}|{target['identity']}|{control['identity']}")
        target_is_a = int(pair_key[-1], 16) % 2 == 0
        ordering = (
            [("A", target, "target_zero_zero"), ("B", control, "control_one_goal")]
            if target_is_a
            else [("A", control, "control_one_goal"), ("B", target, "target_zero_zero")]
        )
        candidates: list[dict[str, Any]] = []
        hidden: dict[str, Any] = {"pair_id": pair_id, "matching_cost": cost}
        for side, row, role in ordering:
            packet = r30.candidate_packet(row)
            packet["side"] = side
            candidates.append(packet)
            hidden[f"side_{side}_role"] = role
            hidden[f"side_{side}_final_score"] = f"{row['home_goals_90']}-{row['away_goals_90']}"
            hidden[f"side_{side}_identity"] = row["identity"]
        if r30.forbidden_candidate_key(candidates):
            raise R32Error(f"{pair_id}: public candidate leaked outcome key")
        public_pairs.append({
            "pair_id": pair_id,
            "pair_identity_sha256": pair_key,
            "candidates": candidates,
        })
        hidden_rows.append(hidden)

    hidden_payload = {
        "schema_version": "v511_zero_zero_screen10_r32_hidden_labels.1",
        "labels": hidden_rows,
    }
    hidden_seal = r30.sha256_text(r30.canonical(hidden_payload))
    public_payload = {
        "schema_version": "v511_zero_zero_screen10_r32_public.1",
        "classification": config["classification"],
        "formal_weight": 0,
        "label_seal_sha256": hidden_seal,
        "sample_contract": config["sample_contract"],
        "rubric": config["rubric"],
        "pairs": public_pairs,
    }

    public_path.parent.mkdir(parents=True, exist_ok=True)
    hidden_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.write_text(json.dumps(public_payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    r30.write_public_csv(public_csv_path, public_pairs)
    hidden_path.write_text(json.dumps(hidden_payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")

    status = {
        "schema_version": "v511_zero_zero_screen10_r32_status.1",
        "status": "PASS_R32_NEW_ZERO_ZERO_BLIND_PACKET_BUILT",
        "classification": config["classification"],
        "formal_weight": 0,
        "pairs": len(public_pairs),
        "competitions": len({c["competition_id"] for p in public_pairs for c in p["candidates"]}),
        "r29_excluded_identity_count": r29_count,
        "r30_excluded_identity_count": r30_count,
        "total_excluded_identity_count": len(excluded),
        "public_packet_sha256": r30.sha256_text(public_path.read_text(encoding="utf-8")),
        "hidden_label_seal_sha256": hidden_seal,
        "ruling": {
            "public_and_hidden_artifacts_separated": True,
            "hidden_labels_must_not_be_read_before_prediction_freeze": True,
            "resampling_allowed": False,
            "training_performed": False,
            "formal_promotion_allowed": False,
        },
        "hard_limits": config["hard_limits"],
    }
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return status


def self_test() -> None:
    assert score({"home_goals_90": 0, "away_goals_90": 0}) == (0, 0)
    assert r30.forbidden_candidate_key({"fixture": {"home_team": "A"}}) is False
    assert r30.forbidden_candidate_key({"final_score": "0-0"}) is True
    print(json.dumps({"self_test": "PASS"}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--r30-config", type=Path, default=DEFAULT_R30_CONFIG)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--public", type=Path, default=DEFAULT_PUBLIC)
    parser.add_argument("--public-csv", type=Path, default=DEFAULT_PUBLIC_CSV)
    parser.add_argument("--hidden", type=Path, default=DEFAULT_HIDDEN)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    print(json.dumps(build(
        load_json(args.config), load_json(args.r30_config), args.status,
        args.public, args.public_csv, args.hidden,
    ), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
