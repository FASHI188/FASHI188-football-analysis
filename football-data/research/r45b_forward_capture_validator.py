#!/usr/bin/env python3
"""Validate R45B forward-capture records without touching target labels or providers."""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "research" / "r45b_forward_capture_contract.json"
INTERACTION_SPEC_PATH = ROOT / "research" / "r45b_matchup_interaction_preregistration.json"
TASK_SPEC_PATH = ROOT / "research" / "r45b_task_state_extractor_preregistration.json"
OUT = ROOT / "research" / "r45b_forward_capture_validation_status.json"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_ts(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def available_at(record: dict[str, Any]) -> datetime | None:
    published = parse_ts(record.get("source_published_at_utc"))
    observed = parse_ts(record.get("collector_first_observed_at_utc"))
    return published or observed


def canonical_payload_sha(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def normalize_role(value: Any) -> str:
    text = str(value or "").strip().upper().replace("_", " ").replace("-", " ")
    return " ".join(text.split())


def validate_interaction_spec(obj: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(obj, dict):
        return ["interaction_spec_not_object"]
    if obj.get("schema_version") != "R45B-MATCHUP-INTERACTION-PREREG-R1":
        errors.append("interaction_spec_schema_mismatch")
    if obj.get("research_only") is not True or int(obj.get("formal_weight") or 0) != 0:
        errors.append("interaction_spec_not_research_only")
    if int(obj.get("target_result_labels_used") or 0) != 0:
        errors.append("interaction_spec_target_labels_nonzero")
    if obj.get("manual_matchup_scores_forbidden") is not True:
        errors.append("interaction_spec_manual_scores_not_forbidden")
    role_map = obj.get("role_normalization")
    zones = obj.get("fixed_zone_mapping")
    pairs = obj.get("interaction_pairs")
    executable = obj.get("executable_representation")
    if not isinstance(role_map, dict) or not role_map:
        errors.append("interaction_spec_role_map_missing")
    if not isinstance(zones, dict) or not zones:
        errors.append("interaction_spec_zone_map_missing")
    if not isinstance(pairs, list) or not pairs:
        errors.append("interaction_spec_pairs_missing")
    else:
        for i, row in enumerate(pairs):
            if not isinstance(row, dict):
                errors.append(f"interaction_pair_{i}:not_object")
                continue
            for field in ("interaction_id", "home_zone", "away_zone"):
                if not str(row.get(field) or "").strip():
                    errors.append(f"interaction_pair_{i}:missing:{field}")
            if isinstance(zones, dict):
                if str(row.get("home_zone") or "") not in zones:
                    errors.append(f"interaction_pair_{i}:unknown_home_zone")
                if str(row.get("away_zone") or "") not in zones:
                    errors.append(f"interaction_pair_{i}:unknown_away_zone")
    if not isinstance(executable, dict) or not str(executable.get("algorithm") or "").strip():
        errors.append("interaction_spec_executable_algorithm_missing")
    elif executable.get("manual_weighting") is not False or executable.get("probability_override") is not False or executable.get("goal_adjustment") is not False:
        errors.append("interaction_spec_forbidden_adjustment_enabled")
    return errors


def validate_task_spec(obj: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(obj, dict):
        return ["task_spec_not_object"]
    if obj.get("schema_version") != "R45B-TASK-STATE-EXTRACTOR-R1":
        errors.append("task_spec_schema_mismatch")
    if obj.get("research_only") is not True or int(obj.get("formal_weight") or 0) != 0:
        errors.append("task_spec_not_research_only")
    if int(obj.get("target_result_labels_used") or 0) != 0:
        errors.append("task_spec_target_labels_nonzero")
    supported = obj.get("supported_task_state_types")
    rules = obj.get("fixed_rules")
    if not isinstance(supported, list) or not supported:
        errors.append("task_spec_supported_types_missing")
    if not isinstance(rules, dict) or not rules:
        errors.append("task_spec_fixed_rules_missing")
    elif isinstance(supported, list):
        for state in supported:
            row = rules.get(state)
            if not isinstance(row, dict) or not str(row.get("rule") or "").strip():
                errors.append(f"task_spec_rule_missing:{state}")
    return errors


def validate_payload(
    kind: str,
    payload: Any,
    spec: dict[str, Any],
    freeze: datetime | None,
    interaction_spec: dict[str, Any],
    task_spec: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["payload_not_object"]
    for field in spec.get("required_payload_fields") or []:
        if field not in payload or payload[field] in (None, "", []):
            errors.append(f"payload_missing:{field}")

    role_map = interaction_spec.get("role_normalization") if isinstance(interaction_spec, dict) else {}
    if not isinstance(role_map, dict):
        role_map = {}

    if kind == "expected_xi_roles":
        players = payload.get("players") if isinstance(payload.get("players"), list) else []
        minimum = int(spec.get("minimum_players_per_team") or 11)
        if len(players) < minimum:
            errors.append("expected_xi_players_below_minimum")
        for i, player in enumerate(players):
            if not isinstance(player, dict):
                errors.append(f"player_{i}:not_object")
                continue
            for field in spec.get("player_required_fields") or []:
                if not str(player.get(field) or "").strip():
                    errors.append(f"player_{i}:missing:{field}")
            role = normalize_role(player.get("role_or_position_slot"))
            if role and role not in role_map:
                errors.append(f"player_{i}:unmapped_role:{role}")

    elif kind == "availability_and_replacement":
        unavailable = payload.get("unavailable_or_doubtful_players") if isinstance(payload.get("unavailable_or_doubtful_players"), list) else []
        replacements = payload.get("replacement_candidates") if isinstance(payload.get("replacement_candidates"), list) else []
        for label, rows, fields in (
            ("unavailable", unavailable, spec.get("unavailable_player_required_fields") or []),
            ("replacement", replacements, spec.get("replacement_candidate_required_fields") or []),
        ):
            for i, row in enumerate(rows):
                if not isinstance(row, dict):
                    errors.append(f"{label}_{i}:not_object")
                    continue
                for field in fields:
                    if row.get(field) in (None, "", []):
                        errors.append(f"{label}_{i}:missing:{field}")
                role = normalize_role(row.get("role_or_position_slot"))
                if role and role_map and role not in role_map:
                    errors.append(f"{label}_{i}:unmapped_role:{role}")

    elif kind == "process_capability":
        semantics = str(payload.get("metric_semantics") or "")
        if semantics not in set(spec.get("allowed_metric_semantics") or []):
            errors.append("invalid_metric_semantics")
        cutoff = parse_ts(payload.get("strict_prior_history_cutoff"))
        if cutoff is None or freeze is None:
            errors.append("invalid_strict_prior_history_cutoff")
        elif cutoff >= freeze:
            errors.append("strict_prior_history_cutoff_not_before_freeze")

    elif kind == "task_utility":
        state = str(payload.get("task_state_type") or "")
        if state not in set(spec.get("allowed_task_state_types") or []):
            errors.append("invalid_task_state_type")
        task_version = str(task_spec.get("schema_version") or "")
        if str(payload.get("extractor_version") or "") != task_version:
            errors.append("unregistered_extractor_version")
        supported = set(task_spec.get("supported_task_state_types") or []) if isinstance(task_spec, dict) else set()
        if state and state not in supported:
            errors.append("task_state_not_supported_by_registered_extractor")
    return errors


def main() -> int:
    contract = load(CONTRACT_PATH)
    record_root = ROOT.parent / str(contract["record_root"])
    specs = contract["evidence_types"]
    allowed_provenance = set(contract.get("provenance_classes_allowed") or [])
    common = list(contract.get("common_required_fields") or [])

    interaction_spec = load(INTERACTION_SPEC_PATH) if INTERACTION_SPEC_PATH.exists() else {}
    task_spec = load(TASK_SPEC_PATH) if TASK_SPEC_PATH.exists() else {}
    interaction_spec_errors = validate_interaction_spec(interaction_spec)
    task_spec_errors = validate_task_spec(task_spec)
    preregistration_ready = not interaction_spec_errors and not task_spec_errors

    records: list[tuple[Path, dict[str, Any]]] = []
    if record_root.exists():
        for path in sorted(record_root.rglob("*.json")):
            try:
                obj = load(path)
            except Exception:
                continue
            if isinstance(obj, dict):
                records.append((path, obj))

    seen_ids = Counter(str(obj.get("evidence_id") or "") for _, obj in records if obj.get("evidence_id"))
    errors: list[dict[str, Any]] = []
    valid_by_type = Counter()
    fixtures_by_type: dict[str, set[str]] = defaultdict(set)
    role_teams_by_fixture_freeze: dict[tuple[str, str], set[str]] = defaultdict(set)
    fixture_identity_seen: dict[str, tuple[str, str, str]] = {}

    identity_nonempty = (
        "evidence_id", "competition_id", "fixture_key", "home_team", "away_team",
        "kickoff_at_utc", "freeze_at_utc", "evidence_type", "source_name", "source_url",
        "source_domain", "source_tier", "accessed_at_utc", "provenance_class",
    )

    for path, obj in records:
        row_errors: list[str] = []
        for field in common:
            if field not in obj:
                row_errors.append(f"missing:{field}")
        for field in identity_nonempty:
            if not str(obj.get(field) or "").strip():
                row_errors.append(f"empty:{field}")

        eid = str(obj.get("evidence_id") or "")
        if eid and seen_ids[eid] > 1:
            row_errors.append("duplicate_evidence_id")

        kind = str(obj.get("evidence_type") or "")
        if kind not in specs:
            row_errors.append("unsupported_evidence_type")

        freeze = parse_ts(obj.get("freeze_at_utc"))
        kickoff = parse_ts(obj.get("kickoff_at_utc"))
        avail = available_at(obj)
        accessed = parse_ts(obj.get("accessed_at_utc"))
        if freeze is None or kickoff is None or avail is None:
            row_errors.append("invalid_or_missing_timestamp")
        elif not (avail <= freeze < kickoff):
            row_errors.append("timestamp_order_fail")
        if accessed is None:
            row_errors.append("invalid_accessed_at_utc")

        provenance = str(obj.get("provenance_class") or "")
        if provenance not in allowed_provenance:
            row_errors.append("provenance_not_allowed")

        payload = obj.get("payload")
        claimed_sha = str(obj.get("payload_sha256") or "").lower()
        if not claimed_sha:
            row_errors.append("missing_payload_sha256")
        elif payload is not None and claimed_sha != canonical_payload_sha(payload):
            row_errors.append("payload_sha256_mismatch")

        fixture = str(obj.get("fixture_key") or "")
        home = str(obj.get("home_team") or "").strip()
        away = str(obj.get("away_team") or "").strip()
        kickoff_text = kickoff.isoformat() if kickoff else ""
        if fixture and home and away and kickoff_text:
            identity = (home, away, kickoff_text)
            previous = fixture_identity_seen.get(fixture)
            if previous is None:
                fixture_identity_seen[fixture] = identity
            elif previous != identity:
                row_errors.append("fixture_identity_conflict")

        if isinstance(payload, dict):
            payload_team = str(payload.get("team") or "").strip()
            if payload_team and payload_team not in {home, away}:
                row_errors.append("payload_team_not_fixture_team")

        if kind in specs:
            row_errors.extend(validate_payload(kind, payload, specs[kind], freeze, interaction_spec, task_spec))

        if row_errors:
            errors.append({"file": str(path.relative_to(ROOT.parent)), "errors": sorted(set(row_errors))})
            continue

        valid_by_type[kind] += 1
        fixtures_by_type[kind].add(fixture)
        if kind == "expected_xi_roles" and isinstance(payload, dict) and freeze is not None:
            role_teams_by_fixture_freeze[(fixture, freeze.isoformat())].add(str(payload.get("team") or "").strip())

    fixtures_with_both_roles: set[str] = set()
    for (fixture, _freeze), teams in role_teams_by_fixture_freeze.items():
        identity = fixture_identity_seen.get(fixture)
        if not identity:
            continue
        expected = {identity[0], identity[1]}
        if expected and expected.issubset({t for t in teams if t}):
            fixtures_with_both_roles.add(fixture)
    role_both = len(fixtures_with_both_roles)

    axis_ready = {
        "expected_xi_roles": role_both > 0,
        "availability_and_replacement": valid_by_type["availability_and_replacement"] > 0,
        "process_capability": valid_by_type["process_capability"] > 0,
        "task_utility": valid_by_type["task_utility"] > 0,
        "matchup_interaction_gate": role_both > 0 and not interaction_spec_errors,
    }
    blocking = [k for k, v in axis_ready.items() if not v]
    if task_spec_errors and "task_utility" not in blocking:
        blocking.append("task_utility")
    status = (
        "READY_FOR_SEPARATE_OOS_PREREGISTRATION"
        if records and not errors and preregistration_ready and not blocking
        else "STOP_FORWARD_CAPTURE_NOT_READY"
    )

    payload = {
        "schema_version": "R45B-FORWARD-CAPTURE-VALIDATION-R2",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": status,
        "record_root": str(record_root.relative_to(ROOT.parent)),
        "record_count": len(records),
        "valid_record_count": len(records) - len(errors),
        "invalid_record_count": len(errors),
        "valid_by_type": dict(valid_by_type),
        "fixtures_by_type": {k: len(v) for k, v in fixtures_by_type.items()},
        "fixtures_with_both_team_role_xi_same_freeze": role_both,
        "preregistration": {
            "interaction_spec_path": str(INTERACTION_SPEC_PATH.relative_to(ROOT.parent)),
            "interaction_spec_errors": interaction_spec_errors,
            "task_spec_path": str(TASK_SPEC_PATH.relative_to(ROOT.parent)),
            "task_spec_errors": task_spec_errors,
            "ready": preregistration_ready,
        },
        "axis_ready": axis_ready,
        "blocking_axes": sorted(set(blocking)),
        "validation_errors": errors,
        "target_match_labels_read": 0,
        "training_runs": 0,
        "scoring_runs": 0,
        "tuning_runs": 0,
        "provider_requests": 0,
        "paid_provider_requests": 0,
        "formal_weight": 0,
        "automatic_oos_authorization": False,
        "independent_oos_authorized": False,
        "ruling": "A data-readiness PASS still requires a separate preregistration and explicit independent OOS authorization before any target labels/training/scoring are accessed."
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if status.startswith("READY") else 2


if __name__ == "__main__":
    raise SystemExit(main())
