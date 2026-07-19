#!/usr/bin/env python3
"""Repository-wide integrity and completeness validation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from platform_core import ROOT, PlatformError, atomic_write_json, load_json, load_registry, sha256_file

STATUS_PATH = ROOT / "manifests" / "platform_status.json"
REQUIRED_SCHEMAS = [
    "match_input.schema.json",
    "market_snapshot.schema.json",
    "calculation_output.schema.json",
    "prediction_freeze.schema.json",
    "postmatch_audit.schema.json",
]
REQUIRED_ENGINES = [
    "platform_core.py",
    "build_team_strengths.py",
    "build_training_dataset.py",
    "match_pipeline.py",
    "validate_platform.py",
    "evaluate_audits.py",
]


def _profile_path(competition_id: str) -> Path:
    return ROOT / "league_profiles" / competition_id / "profile.json"


def run(write: bool = True, require_generated: bool = True) -> dict[str, Any]:
    registry = load_registry()
    errors: list[str] = []
    warnings: list[str] = []
    competitions: dict[str, Any] = {}

    declared_count = registry.get("competition_count")
    actual_count = len(registry["competitions"])
    if declared_count != actual_count or actual_count <= 0:
        errors.append(
            f"registry competition_count mismatch: declared={declared_count} actual={actual_count}"
        )

    current_files = [
        str(path.relative_to(ROOT))
        for path in ROOT.rglob("*")
        if path.is_file() and "CURRENT_唯一正式规则" in path.name
    ]
    if current_files:
        errors.append(f"formal CURRENT file must not be stored in GitHub: {current_files}")

    for name in REQUIRED_SCHEMAS:
        path = ROOT / "schemas" / name
        if not path.exists():
            errors.append(f"missing schema: {path.relative_to(ROOT)}")
            continue
        try:
            load_json(path)
        except PlatformError as exc:
            errors.append(str(exc))

    for name in REQUIRED_ENGINES:
        path = ROOT / "engine" / name
        if not path.exists():
            errors.append(f"missing engine: {path.relative_to(ROOT)}")

    for competition in registry["competitions"]:
        competition_id = competition["competition_id"]
        profile_path = _profile_path(competition_id)
        processed_path = ROOT / "processed" / competition_id
        team_path = ROOT / "team_strengths" / competition_id / "latest.json"
        training_path = ROOT / "training_datasets" / competition_id / "point_in_time.csv"
        item = {
            "competition_id": competition_id,
            "name_zh": competition["name_zh"],
            "profile": "missing",
            "processed_data": "missing",
            "team_features": "missing",
            "training_dataset": "missing",
            "known_stage_status": competition.get("stage_status"),
            "historical_market_status": competition.get("historical_market_status"),
        }
        if profile_path.exists():
            profile = load_json(profile_path)
            item["profile"] = "available"
            item["profile_matches"] = profile.get("matches")
            result_sum = sum(float(value) for value in profile.get("result_distribution", {}).values())
            total_sum = sum(float(value) for value in profile.get("total_goals_0_7plus", {}).values())
            if abs(result_sum - 1.0) > 1e-6:
                errors.append(f"{competition_id} profile result probabilities do not sum to one")
            if abs(total_sum - 1.0) > 1e-6:
                errors.append(f"{competition_id} profile total-goal probabilities do not sum to one")
        else:
            errors.append(f"missing competition profile: {competition_id}")
        if processed_path.exists() and list(processed_path.glob("*.csv")):
            item["processed_data"] = "available"
        else:
            errors.append(f"missing processed data: {competition_id}")
        if team_path.exists():
            item["team_features"] = "available_descriptive_only"
            item["team_features_sha256"] = sha256_file(team_path)
        elif require_generated:
            errors.append(f"missing generated team features: {competition_id}")
        else:
            warnings.append(f"team features not generated yet: {competition_id}")
        if training_path.exists():
            item["training_dataset"] = "available_train_ready_only"
            item["training_dataset_sha256"] = sha256_file(training_path)
        elif require_generated:
            errors.append(f"missing training dataset: {competition_id}")
        else:
            warnings.append(f"training dataset not generated yet: {competition_id}")
        competitions[competition_id] = item

    known_gaps = registry.get("known_unfilled_evidence", [])
    formal_engine_path = ROOT / "engine" / "football_v460_engine.py"
    formal_manifest_path = ROOT / "manifests" / "formal_core_v460_status.json"
    formal_manifest = load_json(formal_manifest_path) if formal_manifest_path.exists() else None
    formal_core_status = (
        "条件通过"
        if formal_engine_path.exists()
        and isinstance(formal_manifest, dict)
        and formal_manifest.get("competition_count_failed") == 0
        and formal_manifest.get("formal_core_available_count", 0) > 0
        else "未启用"
    )
    report = {
        "schema_version": "1.1",
        "status": "通过" if not errors else "失败",
        "competition_count": actual_count,
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "competitions": competitions,
        "capability_status": {
            "competition_results_and_profiles": "通过" if not any("profile" in error or "processed" in error for error in errors) else "失败",
            "team_dynamic_descriptive_features": "通过" if all(item["team_features"].startswith("available") for item in competitions.values()) else "未启用",
            "time_ordered_training_dataset": "通过" if all(item["training_dataset"].startswith("available") for item in competitions.values()) else "未启用",
            "market_snapshot_contract": "通过" if (ROOT / "schemas/market_snapshot.schema.json").exists() else "失败",
            "single_match_preflight": "通过" if (ROOT / "engine/match_pipeline.py").exists() else "失败",
            "formal_joint_probability_core": formal_core_status,
            "score_matrix_audit": "通过" if (ROOT / "engine/match_pipeline.py").exists() else "失败",
            "prediction_freeze_and_postmatch_audit": "通过" if (ROOT / "engine/match_pipeline.py").exists() else "失败",
            "domain_a_grade_receipts": formal_manifest.get("a_grade_receipt_count", 0) if formal_manifest else 0,
            "historical_timestamped_synchronized_market": "不要求用于单场；A等级验证缺口",
            "historical_point_in_time_lineup_injury": "不可用",
        },
        "known_external_evidence_gaps": known_gaps,
        "formal_readiness_statement": (
            "Data preparation, current-season formal-core execution, validation, freezing and auditing are separately gated. "
            "The joint core may produce B/C/D center probabilities only after its domain artifact is validated; no A grade or high-confidence EXACT is implied."
        ),
    }
    if write:
        atomic_write_json(STATUS_PATH, report)
    if errors:
        raise PlatformError(f"platform validation failed with {len(errors)} errors")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-missing-generated", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    try:
        report = run(write=not args.check_only, require_generated=not args.allow_missing_generated)
    except PlatformError as exc:
        print(f"ERROR: {exc}")
        return 2
    if args.print_summary:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
