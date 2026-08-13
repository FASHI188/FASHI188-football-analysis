#!/usr/bin/env python3
"""Validate the accumulating R45B OOS sample manifest without target labels."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FOOTBALL_DATA = Path(__file__).resolve().parents[1]
MANIFEST = FOOTBALL_DATA / "research" / "r45b_oos_sample_manifest.json"
PREREG = FOOTBALL_DATA / "research" / "r45b_independent_oos_preregistration.json"
OUT = FOOTBALL_DATA / "research" / "r45b_oos_sample_manifest_validation_status.json"
EXPECTED_EVIDENCE_COUNTS = {
    "task_utility": 2,
    "availability_and_replacement": 1,
    "process_capability": 2,
    "expected_xi_roles": 2,
}
FORBIDDEN_LABEL_KEYS = {
    "result",
    "outcome",
    "fthg",
    "ftag",
    "ftr",
    "home_goals",
    "away_goals",
    "target_result",
    "target_outcome",
    "label",
}


def load(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError(f"not_object:{path}")
    return obj


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_utc(value: Any, field: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"missing_timestamp:{field}")
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"timezone_required:{field}")
    return dt.astimezone(timezone.utc)


def scan_forbidden_labels(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() in FORBIDDEN_LABEL_KEYS:
                errors.append(f"forbidden_label_key:{path}.{key}")
            scan_forbidden_labels(child, f"{path}.{key}", errors)
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            scan_forbidden_labels(child, f"{path}[{idx}]", errors)


def american_to_decimal(price: float) -> float:
    if price > 0:
        return 1.0 + price / 100.0
    if price < 0:
        return 1.0 + 100.0 / abs(price)
    raise ValueError("american_price_zero")


def main() -> int:
    errors: list[str] = []
    manifest = load(MANIFEST)
    prereg = load(PREREG)

    if manifest.get("schema_version") != "R45B-OOS-SAMPLE-MANIFEST-R1":
        errors.append("manifest_schema_mismatch")
    if manifest.get("status") != "OPEN_ZERO_LABEL_ACCUMULATING":
        errors.append("manifest_status_not_open_accumulating")
    if manifest.get("sample_frozen") is not False:
        errors.append("sample_must_not_be_frozen_while_accumulating")
    if manifest.get("authorization_ready") is not False:
        errors.append("authorization_ready_must_be_false_while_accumulating")
    if manifest.get("preregistration_sha256") != sha256(PREREG):
        errors.append("preregistration_sha256_mismatch")
    if prereg.get("schema_version") != "R45B-INDEPENDENT-OOS-PREREG-R1":
        errors.append("prereg_schema_mismatch")

    scan_forbidden_labels(manifest.get("fixtures") or [], "fixtures", errors)

    fixtures = manifest.get("fixtures") if isinstance(manifest.get("fixtures"), list) else []
    registered = int(manifest.get("registered_fixture_count") or 0)
    minimum = int(manifest.get("minimum_fully_eligible_fixture_count") or 0)
    if registered != len(fixtures):
        errors.append(f"registered_fixture_count_mismatch:{registered}:{len(fixtures)}")
    if minimum != 300:
        errors.append(f"minimum_fixture_count_not_300:{minimum}")

    seen: set[str] = set()
    competitions: set[str] = set()
    fully_eligible = 0
    for idx, fixture in enumerate(fixtures):
        if not isinstance(fixture, dict):
            errors.append(f"fixture_not_object:{idx}")
            continue
        key = str(fixture.get("fixture_key") or "").strip()
        if not key:
            errors.append(f"missing_fixture_key:{idx}")
            continue
        if key in seen:
            errors.append(f"duplicate_fixture:{key}")
            continue
        seen.add(key)
        competition = str(fixture.get("competition_id") or "").strip()
        if competition:
            competitions.add(competition)
        freeze = parse_utc(fixture.get("bundle_freeze_at_utc"), f"fixture[{idx}].bundle_freeze")
        kickoff = parse_utc(fixture.get("kickoff_at_utc"), f"fixture[{idx}].kickoff")
        if not freeze < kickoff:
            errors.append(f"freeze_not_before_kickoff:{key}")
        if fixture.get("target_label_status") != "UNREAD_FORBIDDEN":
            errors.append(f"target_label_status_not_locked:{key}")

        evidence_paths = fixture.get("evidence_paths") if isinstance(fixture.get("evidence_paths"), list) else []
        type_counts = {k: 0 for k in EXPECTED_EVIDENCE_COUNTS}
        xi_freezes: dict[str, datetime] = {}
        fixture_error_start = len(errors)
        for rel in evidence_paths:
            path = FOOTBALL_DATA / str(rel)
            if not path.exists():
                errors.append(f"missing_evidence:{key}:{rel}")
                continue
            record = load(path)
            if record.get("fixture_key") != key:
                errors.append(f"evidence_fixture_mismatch:{key}:{rel}")
            etype = str(record.get("evidence_type") or "")
            if etype in type_counts:
                type_counts[etype] += 1
            observed = parse_utc(
                record.get("collector_first_observed_at_utc") or record.get("accessed_at_utc"),
                f"evidence_observed:{rel}",
            )
            if observed > freeze:
                errors.append(f"evidence_observed_after_bundle_freeze:{key}:{rel}")
            ev_freeze = parse_utc(record.get("freeze_at_utc"), f"evidence_freeze:{rel}")
            if ev_freeze > freeze:
                errors.append(f"evidence_freeze_after_bundle_freeze:{key}:{rel}")
            if etype == "expected_xi_roles":
                team = str((record.get("payload") or {}).get("team") or "").strip()
                xi_freezes[team] = ev_freeze

        for etype, required in EXPECTED_EVIDENCE_COUNTS.items():
            if type_counts.get(etype, 0) < required:
                errors.append(f"evidence_type_count_low:{key}:{etype}:{type_counts.get(etype,0)}<{required}")
        if len(xi_freezes) != 2 or len(set(xi_freezes.values())) != 1:
            errors.append(f"expected_xi_not_both_teams_same_freeze:{key}")
        else:
            declared = parse_utc(fixture.get("expected_xi_pair_same_freeze_at_utc"), f"declared_xi_freeze:{key}")
            actual = next(iter(xi_freezes.values()))
            if declared != actual:
                errors.append(f"declared_xi_freeze_mismatch:{key}")

        baseline_path = FOOTBALL_DATA / str(fixture.get("baseline_path") or "")
        if not baseline_path.exists():
            errors.append(f"missing_baseline:{key}")
        else:
            baseline = load(baseline_path)
            if baseline.get("fixture_key") != key:
                errors.append(f"baseline_fixture_mismatch:{key}")
            if baseline.get("market") != "90_MINUTE_1X2":
                errors.append(f"baseline_market_mismatch:{key}")
            if baseline.get("research_baseline_only") is not True:
                errors.append(f"baseline_not_research_only:{key}")
            if int(baseline.get("target_match_labels_read") or 0) != 0:
                errors.append(f"baseline_label_invariant_nonzero:{key}")
            observed = parse_utc(baseline.get("collector_first_observed_at_utc"), f"baseline_observed:{key}")
            if observed != freeze:
                errors.append(f"baseline_not_same_bundle_freeze:{key}")
            american = baseline.get("american_prices") if isinstance(baseline.get("american_prices"), dict) else {}
            decimal = baseline.get("decimal_prices") if isinstance(baseline.get("decimal_prices"), dict) else {}
            probs = baseline.get("no_vig_probabilities") if isinstance(baseline.get("no_vig_probabilities"), dict) else {}
            for side in ("home", "draw", "away"):
                if side not in american or side not in decimal or side not in probs:
                    errors.append(f"baseline_incomplete:{key}:{side}")
                    continue
                expected_dec = american_to_decimal(float(american[side]))
                if abs(float(decimal[side]) - expected_dec) > 1e-9:
                    errors.append(f"baseline_decimal_conversion_error:{key}:{side}")
            if len(probs) == 3 and abs(sum(float(probs[s]) for s in ("home","draw","away")) - 1.0) > 1e-9:
                errors.append(f"baseline_no_vig_probability_sum_error:{key}")
            if len(decimal) == 3:
                raw_sum = sum(1.0 / float(decimal[s]) for s in ("home","draw","away"))
                if abs(float(baseline.get("raw_implied_probability_sum") or 0.0) - raw_sum) > 1e-9:
                    errors.append(f"baseline_raw_probability_sum_error:{key}")
                if len(probs) == 3:
                    for side in ("home", "draw", "away"):
                        expected_prob = (1.0 / float(decimal[side])) / raw_sum
                        if abs(float(probs[side]) - expected_prob) > 1e-9:
                            errors.append(f"baseline_no_vig_probability_error:{key}:{side}")
        if len(errors) == fixture_error_start:
            fully_eligible += 1

    coverage = manifest.get("coverage") if isinstance(manifest.get("coverage"), dict) else {}
    if int(coverage.get("fully_eligible_fixture_count") or 0) != fully_eligible:
        errors.append(f"coverage_fully_eligible_mismatch:{coverage.get('fully_eligible_fixture_count')}:{fully_eligible}")
    expected_remaining = max(0, minimum - fully_eligible)
    if int(coverage.get("remaining_to_minimum") or 0) != expected_remaining:
        errors.append(f"coverage_remaining_mismatch:{coverage.get('remaining_to_minimum')}:{expected_remaining}")
    if int(coverage.get("competition_count") or 0) != len(competitions):
        errors.append(f"coverage_competition_count_mismatch:{coverage.get('competition_count')}:{len(competitions)}")
    expected_gate = fully_eligible >= minimum
    if bool(coverage.get("coverage_gate_pass")) != expected_gate:
        errors.append("coverage_gate_boolean_mismatch")

    zero = manifest.get("zero_label_invariants") if isinstance(manifest.get("zero_label_invariants"), dict) else {}
    for field in (
        "target_match_labels_read",
        "training_runs",
        "scoring_runs",
        "tuning_runs",
        "provider_requests",
        "paid_provider_requests",
        "formal_weight",
    ):
        if int(zero.get(field) or 0) != 0:
            errors.append(f"zero_label_invariant_nonzero:{field}")
    if zero.get("independent_oos_authorized") is not False:
        errors.append("independent_oos_authorized_while_accumulating")

    if errors:
        status_name = "FAIL_SAMPLE_MANIFEST"
    elif fully_eligible >= minimum:
        status_name = "PASS_COVERAGE_READY_TO_FREEZE_IDENTITY_NOT_AUTHORIZED"
    else:
        status_name = "PASS_ACCUMULATING_ZERO_LABEL_NOT_AUTHORIZABLE"

    status = {
        "schema_version": "R45B-OOS-SAMPLE-MANIFEST-VALIDATION-R1",
        "status": status_name,
        "manifest_sha256_current_open_state": sha256(MANIFEST),
        "preregistration_sha256": sha256(PREREG),
        "registered_fixture_count": registered,
        "fully_eligible_fixture_count": fully_eligible,
        "minimum_fully_eligible_fixture_count": minimum,
        "remaining_to_minimum": expected_remaining,
        "competition_count": len(competitions),
        "coverage_gate_pass": expected_gate,
        "sample_frozen": False,
        "authorization_ready": False,
        "target_match_labels_read": 0,
        "training_runs": 0,
        "scoring_runs": 0,
        "tuning_runs": 0,
        "provider_requests": 0,
        "paid_provider_requests": 0,
        "formal_weight": 0,
        "independent_oos_authorized": False,
        "errors": errors,
        "ruling": "Accumulating manifest validation never authorizes labels. Coverage must reach the preregistered >=300 fully eligible fixtures, then the identity manifest must be frozen and explicitly authorized separately."
    }
    OUT.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
