#!/usr/bin/env python3
"""Football3 Nova reusable historical-match governance.

This module encodes the permanent-history, candidate-role, nested chronological
validation, OOF prediction, confirmation demotion, and OOF-only ensemble rules.
It does not train a model and does not read any production/CURRENT state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROLES = (
    "TRAIN",
    "DEVELOPMENT",
    "REUSABLE_BENCHMARK",
    "CANDIDATE_CONFIRMATION",
)
PREDICTION_ORIGINS = ("OOF", "CONFIRMATION_OOS")
ALLOWED_TRANSITIONS = {
    "TRAIN": (),
    "DEVELOPMENT": (),
    "REUSABLE_BENCHMARK": (),
    "CANDIDATE_CONFIRMATION": ("REUSABLE_BENCHMARK",),
}


class GovernanceError(ValueError):
    pass


def _dt(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise GovernanceError("timestamp must be non-empty ISO-8601 string")
    v = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(v)
    except ValueError as exc:
        raise GovernanceError(f"invalid timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise GovernanceError(f"timestamp must be timezone-aware: {value}")
    return parsed.astimezone(timezone.utc)


def _sha256ish(value: str, field: str) -> None:
    if not isinstance(value, str) or len(value) != 64:
        raise GovernanceError(f"{field} must be 64-char sha256 hex")
    try:
        int(value, 16)
    except ValueError as exc:
        raise GovernanceError(f"{field} must be hexadecimal") from exc


def validate_match_record(row: Mapping[str, Any]) -> None:
    required = (
        "match_id",
        "competition_id",
        "season",
        "kickoff",
        "home_team_id",
        "away_team_id",
        "source_id",
        "source_revision",
        "input_sha256",
    )
    missing = [k for k in required if k not in row]
    if missing:
        raise GovernanceError(f"match record missing fields: {missing}")
    if str(row["home_team_id"]) == str(row["away_team_id"]):
        raise GovernanceError("home and away team ids must differ")
    _dt(str(row["kickoff"]))
    _sha256ish(str(row["input_sha256"]), "input_sha256")
    if not str(row["source_id"]).strip() or not str(row["source_revision"]).strip():
        raise GovernanceError("source_id/source_revision must be non-empty")


def validate_role_event(event: Mapping[str, Any]) -> None:
    required = (
        "candidate_id",
        "match_id",
        "role",
        "assigned_at",
        "event_seq",
        "research_label_exposed_before_assignment",
    )
    missing = [k for k in required if k not in event]
    if missing:
        raise GovernanceError(f"role event missing fields: {missing}")
    role = str(event["role"])
    if role not in ROLES:
        raise GovernanceError(f"unsupported role: {role}")
    if not str(event["candidate_id"]).strip() or not str(event["match_id"]).strip():
        raise GovernanceError("candidate_id/match_id must be non-empty")
    _dt(str(event["assigned_at"]))
    if not isinstance(event["event_seq"], int) or event["event_seq"] < 1:
        raise GovernanceError("event_seq must be positive integer")
    if not isinstance(event["research_label_exposed_before_assignment"], bool):
        raise GovernanceError("research_label_exposed_before_assignment must be bool")
    if role == "CANDIDATE_CONFIRMATION" and event["research_label_exposed_before_assignment"]:
        raise GovernanceError(
            "already-exposed match cannot be assigned as fresh candidate confirmation"
        )


def validate_candidate_role_history(events: Sequence[Mapping[str, Any]]) -> str:
    if not events:
        raise GovernanceError("candidate role history cannot be empty")
    for e in events:
        validate_role_event(e)
    candidate_ids = {str(e["candidate_id"]) for e in events}
    match_ids = {str(e["match_id"]) for e in events}
    if len(candidate_ids) != 1 or len(match_ids) != 1:
        raise GovernanceError("role history must refer to exactly one candidate-match pair")
    ordered = sorted(events, key=lambda e: int(e["event_seq"]))
    seqs = [int(e["event_seq"]) for e in ordered]
    if seqs != list(range(1, len(seqs) + 1)):
        raise GovernanceError("role event_seq must be contiguous from 1")
    assigned = [_dt(str(e["assigned_at"])) for e in ordered]
    if assigned != sorted(assigned):
        raise GovernanceError("role assigned_at must be monotone")
    roles = [str(e["role"]) for e in ordered]
    for before, after in zip(roles, roles[1:]):
        if after not in ALLOWED_TRANSITIONS[before]:
            raise GovernanceError(f"forbidden role transition: {before}->{after}")
    if roles.count("CANDIDATE_CONFIRMATION") > 1:
        raise GovernanceError("candidate confirmation is one-time for a candidate-match pair")
    return roles[-1]


def validate_confirmation_scoring(
    role_history: Sequence[Mapping[str, Any]],
    confirmation_scored_at: str | None,
) -> None:
    final_role = validate_candidate_role_history(role_history)
    roles = [str(e["role"]) for e in sorted(role_history, key=lambda e: int(e["event_seq"]))]
    if confirmation_scored_at is None:
        return
    _dt(confirmation_scored_at)
    if "CANDIDATE_CONFIRMATION" not in roles:
        raise GovernanceError("confirmation score requires prior CANDIDATE_CONFIRMATION role")
    if final_role != "REUSABLE_BENCHMARK":
        raise GovernanceError(
            "scored candidate confirmation must be demoted to REUSABLE_BENCHMARK"
        )


def validate_inner_fold(fold: Mapping[str, Any], outer_train_end: str) -> None:
    required = ("inner_train_end", "inner_score_start", "inner_score_end")
    missing = [k for k in required if k not in fold]
    if missing:
        raise GovernanceError(f"inner fold missing fields: {missing}")
    train_end = _dt(str(fold["inner_train_end"]))
    score_start = _dt(str(fold["inner_score_start"]))
    score_end = _dt(str(fold["inner_score_end"]))
    outer_end = _dt(outer_train_end)
    if not (train_end < score_start <= score_end <= outer_end):
        raise GovernanceError("inner fold violates chronological nesting")


def validate_outer_fold(fold: Mapping[str, Any]) -> None:
    required = ("fold_id", "outer_train_end", "outer_score_start", "outer_score_end", "inner_folds")
    missing = [k for k in required if k not in fold]
    if missing:
        raise GovernanceError(f"outer fold missing fields: {missing}")
    train_end = _dt(str(fold["outer_train_end"]))
    score_start = _dt(str(fold["outer_score_start"]))
    score_end = _dt(str(fold["outer_score_end"]))
    if not (train_end < score_start <= score_end):
        raise GovernanceError("outer fold violates chronological ordering")
    inner = fold["inner_folds"]
    if not isinstance(inner, list) or not inner:
        raise GovernanceError("outer fold must contain at least one inner fold")
    for item in inner:
        validate_inner_fold(item, str(fold["outer_train_end"]))


def validate_outer_fold_sequence(folds: Sequence[Mapping[str, Any]]) -> None:
    if not folds:
        raise GovernanceError("at least one outer fold required")
    for fold in folds:
        validate_outer_fold(fold)
    starts = [_dt(str(f["outer_score_start"])) for f in folds]
    if starts != sorted(starts):
        raise GovernanceError("outer folds must be ordered by score start")
    fold_ids = [str(f["fold_id"]) for f in folds]
    if len(set(fold_ids)) != len(fold_ids):
        raise GovernanceError("outer fold ids must be unique")


def validate_prediction_record(pred: Mapping[str, Any], role: str, reported: bool = True) -> None:
    required = (
        "candidate_id",
        "match_id",
        "model_sha",
        "fold_id",
        "prediction_origin",
        "prediction_sha256",
        "outer_train_end",
        "outer_score_start",
    )
    missing = [k for k in required if k not in pred]
    if missing:
        raise GovernanceError(f"prediction record missing fields: {missing}")
    if role not in ROLES:
        raise GovernanceError(f"unsupported role: {role}")
    origin = str(pred["prediction_origin"])
    if origin not in PREDICTION_ORIGINS:
        raise GovernanceError(f"unsupported prediction_origin: {origin}")
    _sha256ish(str(pred["prediction_sha256"]), "prediction_sha256")
    if not str(pred["model_sha"]).strip():
        raise GovernanceError("model_sha must be non-empty")
    train_end = _dt(str(pred["outer_train_end"]))
    score_start = _dt(str(pred["outer_score_start"]))
    if not train_end < score_start:
        raise GovernanceError("prediction train window must end before score start")
    if reported:
        if role == "REUSABLE_BENCHMARK" and origin != "OOF":
            raise GovernanceError("reported reusable benchmark score requires OOF prediction")
        if role == "CANDIDATE_CONFIRMATION" and origin != "CONFIRMATION_OOS":
            raise GovernanceError("reported confirmation score requires CONFIRMATION_OOS prediction")
        if role in {"TRAIN", "DEVELOPMENT"}:
            raise GovernanceError(f"{role} rows cannot supply primary reported score")


def validate_unique_reported_predictions(preds: Sequence[Mapping[str, Any]]) -> None:
    seen: set[tuple[str, str, str]] = set()
    for p in preds:
        key = (str(p["candidate_id"]), str(p["match_id"]), str(p["model_sha"]))
        if key in seen:
            raise GovernanceError(f"duplicate reported prediction: {key}")
        seen.add(key)


def validate_combination_record(combo: Mapping[str, Any]) -> None:
    components = combo.get("components")
    if not isinstance(components, list) or len(components) < 2:
        raise GovernanceError("combined expert requires at least two component predictions")
    target_match = str(combo.get("match_id", ""))
    if not target_match:
        raise GovernanceError("combined expert requires match_id")
    for c in components:
        if str(c.get("match_id", "")) != target_match:
            raise GovernanceError("component prediction match_id mismatch")
        origin = str(c.get("prediction_origin", ""))
        if origin not in PREDICTION_ORIGINS:
            raise GovernanceError(
                "combined expert may use only OOF or CONFIRMATION_OOS component predictions"
            )
        if not str(c.get("model_sha", "")).strip() or not str(c.get("fold_id", "")).strip():
            raise GovernanceError("combined expert components require model_sha and fold_id")
    if str(combo.get("meta_training_origin", "")) != "OOF":
        raise GovernanceError("ensemble/meta model training inputs must be OOF only")


def validate_result_library_entry(entry: Mapping[str, Any]) -> None:
    required = (
        "candidate_id",
        "evidence_role",
        "prediction_origin",
        "stable_across_folds",
        "primary_metric",
        "delta",
        "classification",
    )
    missing = [k for k in required if k not in entry]
    if missing:
        raise GovernanceError(f"result library entry missing fields: {missing}")
    if entry["evidence_role"] != "REUSABLE_BENCHMARK":
        raise GovernanceError("research result library evidence must come from REUSABLE_BENCHMARK")
    if entry["prediction_origin"] != "OOF":
        raise GovernanceError("research result library requires OOF evidence")
    if entry["stable_across_folds"] is not True:
        raise GovernanceError("positive result library entries require fold stability evidence")
    delta = float(entry["delta"])
    if entry["classification"] in {"POSITIVE_SIGNAL", "RESEARCH_CANDIDATE"} and not delta < 0:
        raise GovernanceError("positive classification requires improvement (negative loss delta)")
    if entry["classification"] == "PROMOTION_CANDIDATE":
        raise GovernanceError(
            "formal promotion cannot be granted from reusable benchmark alone; confirmation required"
        )


def canonical_json_sha256(obj: Any) -> str:
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(payload).hexdigest()


def self_check_receipt(contract_path: Path) -> dict[str, Any]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if contract.get("match_roles") != list(ROLES):
        raise GovernanceError("contract roles do not match executable roles")
    invariants = contract.get("invariants", {})
    required_true = (
        "historical_rows_are_permanent",
        "candidate_roles_are_candidate_specific",
        "confirmation_after_scoring_demotes_to_reusable_benchmark",
        "all_reported_candidate_scores_require_oof_or_confirmation_oos_predictions",
        "combined_experts_must_use_component_oof_predictions",
    )
    if any(invariants.get(k) is not True for k in required_true):
        raise GovernanceError("contract invariant mismatch")
    return {
        "schema_version": "football3-nova-reusable-history-governance-receipt-v1",
        "status": "PASS",
        "contract_sha256": canonical_json_sha256(contract),
        "roles": list(ROLES),
        "historical_rows_are_permanent": True,
        "confirmation_demotes_to_reusable_benchmark": True,
        "reported_benchmark_requires_oof": True,
        "ensemble_requires_component_oof": True,
        "formal_v2_changed": False,
        "current_changed": False,
        "production_changed": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    receipt = self_check_receipt(args.contract)
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
