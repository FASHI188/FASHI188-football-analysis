#!/usr/bin/env python3
"""Validate the frozen identity set of viewed historical 7+ score labels.

This is a governance check, not a model evaluation. It proves that every
historical tail label loaded by the R1/R2 research chain is explicitly marked
VIEWED and cannot later be presented as untouched confirmation evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "manifests" / "v510_viewed_tail_label_freeze_r2.json"


class FreezeError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_label_id(row: pd.Series | dict[str, Any]) -> str:
    fields = (
        "source_file",
        "row_number",
        "competition_id",
        "season",
        "date_key",
        "home_team",
        "away_team",
        "home_goals_90",
        "away_goals_90",
        "total_goals",
    )
    value = {name: row[name] for name in fields}
    for name in ("row_number", "home_goals_90", "away_goals_90", "total_goals"):
        value[name] = int(value[name])
    for name in (
        "source_file",
        "competition_id",
        "season",
        "date_key",
        "home_team",
        "away_team",
    ):
        value[name] = str(value[name])
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def role_classes(
    sequence: list[str], season: str, test_positions: list[int]
) -> tuple[list[str], list[str]]:
    roles: list[str] = []
    fold_roles: list[str] = []
    for test_position in test_positions:
        split = "excluded"
        if season in sequence[: test_position - 1]:
            split = "train"
        elif season == sequence[test_position - 1]:
            split = "policy"
        elif season == sequence[test_position]:
            split = "test"
        if split != "excluded":
            roles.append(split)
            fold_roles.append(
                f"window_{test_position - 1}_to_{test_position}:{split}"
            )
    if not roles:
        roles = ["identity_or_feature_history_only"]
    return sorted(set(roles)), fold_roles


def freeze_records(
    raw: pd.DataFrame, manifest: dict[str, Any]
) -> list[dict[str, Any]]:
    sequences = {
        str(key): [str(value) for value in values]
        for key, values in manifest["split_contract"][
            "complete_season_sequences"
        ].items()
    }
    test_positions = [
        int(value)
        for value in manifest["split_contract"][
            "rolling_test_positions_zero_based"
        ]
    ]
    tail = raw[raw["total_goals"].astype(int) >= 7].copy()
    output: list[dict[str, Any]] = []
    for _, row in tail.iterrows():
        competition = str(row["competition_id"])
        season = str(row["season"])
        if competition not in sequences:
            raise FreezeError(f"missing season sequence for {competition}")
        roles, folds = role_classes(sequences[competition], season, test_positions)
        identity = {
            "source_file": str(row["source_file"]),
            "row_number": int(row["row_number"]),
            "competition_id": competition,
            "season": season,
            "date_key": str(row["date_key"]),
            "home_team": str(row["home_team"]),
            "away_team": str(row["away_team"]),
            "home_goals_90": int(row["home_goals_90"]),
            "away_goals_90": int(row["away_goals_90"]),
            "total_goals": int(row["total_goals"]),
        }
        output.append(
            {
                "label_id_sha256": canonical_label_id(identity),
                **identity,
                "tail_excess": identity["total_goals"] - 7,
                "r2_role_classes": "|".join(roles),
                "r2_fold_roles": "|".join(folds),
                "r2_confirmation_test_viewed": "test" in roles,
                "r2_candidate_policy_viewed": "policy" in roles,
                "r2_fit_or_training_viewed": "train" in roles,
                "r2_identity_or_feature_history_only": roles
                == ["identity_or_feature_history_only"],
                "view_status": "VIEWED_NO_LONGER_UNTOUCHED",
                "future_independent_confirmation_eligible": False,
            }
        )
    return sorted(
        output,
        key=lambda row: (
            row["competition_id"],
            row["date_key"],
            row["home_team"],
            row["away_team"],
            row["source_file"],
            row["row_number"],
        ),
    )


def canonical_freeze_sha256(records: list[dict[str, Any]]) -> str:
    encoded = "".join(
        json.dumps(
            row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        + "\n"
        for row in records
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def run(manifest_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    root = manifest_path.resolve().parents[1]
    source_path = root / str(manifest["source_ledger"]["path"])
    if not source_path.exists():
        raise FreezeError(f"source ledger missing: {source_path}")

    source_sha = sha256_file(source_path)
    expected_source_sha = str(manifest["source_ledger"]["sha256"])
    if source_sha != expected_source_sha:
        raise FreezeError(
            f"source ledger sha mismatch: expected={expected_source_sha} "
            f"actual={source_sha}"
        )

    raw = pd.read_csv(source_path, dtype={"season": str})
    records = freeze_records(raw, manifest)
    freeze_sha = canonical_freeze_sha256(records)
    expected_freeze_sha = str(manifest["freeze_set"]["canonical_sha256"])
    if freeze_sha != expected_freeze_sha:
        raise FreezeError(
            f"freeze set sha mismatch: expected={expected_freeze_sha} "
            f"actual={freeze_sha}"
        )

    label_ids = [row["label_id_sha256"] for row in records]
    if len(set(label_ids)) != len(label_ids):
        raise FreezeError("duplicate label_id_sha256 values")
    if any(row["future_independent_confirmation_eligible"] for row in records):
        raise FreezeError("a viewed label is still marked confirmation-eligible")
    if any(
        row["view_status"] != "VIEWED_NO_LONGER_UNTOUCHED"
        for row in records
    ):
        raise FreezeError("invalid view_status")

    exact_counts: dict[str, int] = {}
    for row in records:
        key = str(row["total_goals"])
        exact_counts[key] = exact_counts.get(key, 0) + 1
    expected_counts = {
        str(key): int(value)
        for key, value in manifest["freeze_set"]["exact_total_counts"].items()
    }
    if exact_counts != expected_counts:
        raise FreezeError(
            f"exact total counts mismatch: expected={expected_counts} "
            f"actual={exact_counts}"
        )

    role_counts = {
        "r2_confirmation_test_viewed": sum(
            bool(row["r2_confirmation_test_viewed"]) for row in records
        ),
        "r2_candidate_policy_viewed": sum(
            bool(row["r2_candidate_policy_viewed"]) for row in records
        ),
        "r2_fit_or_training_viewed": sum(
            bool(row["r2_fit_or_training_viewed"]) for row in records
        ),
        "r2_identity_or_feature_history_only": sum(
            bool(row["r2_identity_or_feature_history_only"])
            for row in records
        ),
    }
    expected_role_counts = {
        key: int(value)
        for key, value in manifest["freeze_set"]["role_counts"].items()
    }
    if role_counts != expected_role_counts:
        raise FreezeError(
            f"role counts mismatch: expected={expected_role_counts} "
            f"actual={role_counts}"
        )

    role_class_counts: dict[str, int] = {}
    for row in records:
        key = str(row["r2_role_classes"])
        role_class_counts[key] = role_class_counts.get(key, 0) + 1
    expected_role_class_counts = {
        str(key): int(value)
        for key, value in manifest["freeze_set"][
            "role_class_counts"
        ].items()
    }
    if dict(sorted(role_class_counts.items())) != dict(
        sorted(expected_role_class_counts.items())
    ):
        raise FreezeError("role class counts mismatch")

    if len(records) != int(manifest["freeze_set"]["rows"]):
        raise FreezeError("freeze row count mismatch")
    if len(set(label_ids)) != int(
        manifest["freeze_set"]["unique_label_ids"]
    ):
        raise FreezeError("unique label count mismatch")

    result = {
        "status": manifest["status"],
        "source_ledger_sha256": source_sha,
        "freeze_set_canonical_sha256": freeze_sha,
        "viewed_tail_rows": len(records),
        "unique_label_ids": len(set(label_ids)),
        "exact_total_counts": exact_counts,
        "role_counts": role_counts,
        "future_independent_confirmation_eligible_rows": sum(
            bool(row["future_independent_confirmation_eligible"])
            for row in records
        ),
        "formal_weight": int(manifest["governance"]["formal_weight"]),
        "unified_score_matrix_allowed": bool(
            manifest["governance"]["unified_score_matrix_allowed"]
        ),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def self_test() -> None:
    sequence = ["s0", "s1", "s2", "s3", "s4"]
    positions = [2, 3, 4]
    expected = {
        "s0": ["train"],
        "s1": ["policy", "train"],
        "s2": ["policy", "test", "train"],
        "s3": ["policy", "test"],
        "s4": ["test"],
        "s5": ["identity_or_feature_history_only"],
    }
    for season, wanted in expected.items():
        roles, _ = role_classes(sequence, season, positions)
        if roles != wanted:
            raise FreezeError(
                f"self-test role mismatch for {season}: "
                f"expected={wanted} actual={roles}"
            )
    sample = {
        "source_file": "processed/X.csv",
        "row_number": 2,
        "competition_id": "X",
        "season": "s2",
        "date_key": "2025-01-01",
        "home_team": "A",
        "away_team": "B",
        "home_goals_90": 4,
        "away_goals_90": 3,
        "total_goals": 7,
    }
    first = canonical_label_id(sample)
    second = canonical_label_id(dict(sample))
    changed = canonical_label_id(
        {**sample, "away_goals_90": 4, "total_goals": 8}
    )
    if first != second or first == changed:
        raise FreezeError("self-test canonical label hash failure")
    record = {
        "label_id_sha256": first,
        **sample,
        "tail_excess": 0,
        "r2_role_classes": "policy|test|train",
        "r2_fold_roles": (
            "window_1_to_2:test|window_2_to_3:policy|"
            "window_3_to_4:train"
        ),
        "r2_confirmation_test_viewed": True,
        "r2_candidate_policy_viewed": True,
        "r2_fit_or_training_viewed": True,
        "r2_identity_or_feature_history_only": False,
        "view_status": "VIEWED_NO_LONGER_UNTOUCHED",
        "future_independent_confirmation_eligible": False,
    }
    digest_a = canonical_freeze_sha256([record])
    digest_b = canonical_freeze_sha256([dict(record)])
    if digest_a != digest_b:
        raise FreezeError("self-test canonical freeze digest failure")
    print("SELF_TEST_PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    else:
        run(args.manifest)


if __name__ == "__main__":
    main()
