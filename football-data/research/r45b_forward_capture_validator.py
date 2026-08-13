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


def validate_payload(kind: str, payload: Any, spec: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["payload_not_object"]
    for field in spec.get("required_payload_fields") or []:
        if field not in payload or payload[field] in (None, "", []):
            errors.append(f"payload_missing:{field}")
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
    elif kind == "process_capability":
        semantics = str(payload.get("metric_semantics") or "")
        if semantics not in set(spec.get("allowed_metric_semantics") or []):
            errors.append("invalid_metric_semantics")
    elif kind == "task_utility":
        state = str(payload.get("task_state_type") or "")
        if state not in set(spec.get("allowed_task_state_types") or []):
            errors.append("invalid_task_state_type")
        if not str(payload.get("extractor_version") or "").strip():
            errors.append("missing_extractor_version")
    return errors


def main() -> int:
    contract = load(CONTRACT_PATH)
    record_root = ROOT.parent / str(contract["record_root"])
    specs = contract["evidence_types"]
    allowed_provenance = set(contract.get("provenance_classes_allowed") or [])
    common = list(contract.get("common_required_fields") or [])

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
    role_teams_by_fixture: dict[str, set[str]] = defaultdict(set)

    for path, obj in records:
        row_errors: list[str] = []
        for field in common:
            if field not in obj:
                row_errors.append(f"missing:{field}")
        eid = str(obj.get("evidence_id") or "")
        if eid and seen_ids[eid] > 1:
            row_errors.append("duplicate_evidence_id")
        kind = str(obj.get("evidence_type") or "")
        if kind not in specs:
            row_errors.append("unsupported_evidence_type")
        freeze = parse_ts(obj.get("freeze_at_utc"))
        kickoff = parse_ts(obj.get("kickoff_at_utc"))
        avail = available_at(obj)
        if freeze is None or kickoff is None or avail is None:
            row_errors.append("invalid_or_missing_timestamp")
        elif not (avail <= freeze < kickoff):
            row_errors.append("timestamp_order_fail")
        provenance = str(obj.get("provenance_class") or "")
        if provenance not in allowed_provenance:
            row_errors.append("provenance_not_allowed")
        payload = obj.get("payload")
        claimed_sha = str(obj.get("payload_sha256") or "").lower()
        if not claimed_sha:
            row_errors.append("missing_payload_sha256")
        elif payload is not None and claimed_sha != canonical_payload_sha(payload):
            row_errors.append("payload_sha256_mismatch")
        if kind in specs:
            row_errors.extend(validate_payload(kind, payload, specs[kind]))
        if row_errors:
            errors.append({"file": str(path.relative_to(ROOT.parent)), "errors": sorted(set(row_errors))})
            continue
        valid_by_type[kind] += 1
        fixture = str(obj.get("fixture_key") or "")
        fixtures_by_type[kind].add(fixture)
        if kind == "expected_xi_roles" and isinstance(payload, dict):
            role_teams_by_fixture[fixture].add(str(payload.get("team") or ""))

    role_both = sum(1 for teams in role_teams_by_fixture.values() if len({t for t in teams if t}) >= 2)
    axis_ready = {
        "expected_xi_roles": role_both > 0,
        "availability_and_replacement": valid_by_type["availability_and_replacement"] > 0,
        "process_capability": valid_by_type["process_capability"] > 0,
        "task_utility": valid_by_type["task_utility"] > 0,
        "matchup_interaction_gate": role_both > 0,
    }
    blocking = [k for k, v in axis_ready.items() if not v]
    status = "READY_FOR_SEPARATE_OOS_PREREGISTRATION" if records and not errors and not blocking else "STOP_FORWARD_CAPTURE_NOT_READY"
    payload = {
        "schema_version": "R45B-FORWARD-CAPTURE-VALIDATION-R1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": status,
        "record_root": str(record_root.relative_to(ROOT.parent)),
        "record_count": len(records),
        "valid_record_count": len(records) - len(errors),
        "invalid_record_count": len(errors),
        "valid_by_type": dict(valid_by_type),
        "fixtures_by_type": {k: len(v) for k, v in fixtures_by_type.items()},
        "fixtures_with_both_team_role_xi": role_both,
        "axis_ready": axis_ready,
        "blocking_axes": blocking,
        "validation_errors": errors,
        "target_match_labels_read": 0,
        "training_runs": 0,
        "scoring_runs": 0,
        "tuning_runs": 0,
        "provider_requests": 0,
        "paid_provider_requests": 0,
        "formal_weight": 0,
        "automatic_oos_authorization": false,
        "ruling": "A data-readiness PASS still requires a separate preregistration and explicit independent OOS authorization before any target labels/training/scoring are accessed."
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if status.startswith("READY") else 2


if __name__ == "__main__":
    raise SystemExit(main())
