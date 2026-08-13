#!/usr/bin/env python3
"""Validate the R45B independent OOS preregistration without reading labels.

This validator checks governance/design completeness only. It cannot create an
authorization and it never reads any target outcome, fits a model or scores a
prediction.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PREREG = ROOT / "research" / "r45b_independent_oos_preregistration.json"
PROMOTION = ROOT / "governance" / "model_promotion_gate_v520.json"
OUT = ROOT / "research" / "r45b_independent_oos_prereg_validation_status.json"


def load(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError(f"not_object:{path}")
    return obj


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def nonempty(obj: dict[str, Any], key: str) -> bool:
    return key in obj and obj[key] not in (None, "", [], {})


def main() -> int:
    errors: list[str] = []
    prereg = load(PREREG)
    promotion = load(PROMOTION)

    if prereg.get("schema_version") != "R45B-INDEPENDENT-OOS-PREREG-R1":
        errors.append("schema_mismatch")
    if prereg.get("research_only") is not True:
        errors.append("research_only_not_true")
    if int(prereg.get("formal_weight") or 0) != 0:
        errors.append("formal_weight_nonzero")

    mandatory = promotion.get("mandatory_zero_label_preregistration") or []
    if not isinstance(mandatory, list) or not mandatory:
        errors.append("promotion_gate_mandatory_fields_missing")
    else:
        for field in mandatory:
            if not nonempty(prereg, str(field)):
                errors.append(f"missing_mandatory_prereg_field:{field}")

    governance = prereg.get("governance") if isinstance(prereg.get("governance"), dict) else {}
    if governance.get("current_rule_version") != "V5.2.0":
        errors.append("current_rule_version_mismatch")
    if governance.get("automatic_oos_authorization") is not False:
        errors.append("automatic_oos_authorization_not_false")
    if governance.get("independent_oos_authorized") is not False:
        errors.append("governance_independent_oos_authorized")

    candidates = prereg.get("candidate_catalog") if isinstance(prereg.get("candidate_catalog"), list) else []
    if len(candidates) != 1:
        errors.append(f"candidate_catalog_size:{len(candidates)}")
    elif candidates[0].get("candidate_id") != "R45B_DRAW_MASS_LOGIT_OFFSET_R1":
        errors.append("candidate_id_mismatch")

    split = prereg.get("train_policy_test_split") if isinstance(prereg.get("train_policy_test_split"), dict) else {}
    if int(split.get("minimum_fully_eligible_fixture_count") or 0) < 300:
        errors.append("minimum_sample_below_300")
    if int(split.get("test_fold_count") or 0) < 3:
        errors.append("chronological_test_folds_below_3")
    if "same" not in str(split.get("same_day_rule") or "").lower():
        errors.append("same_day_rule_missing")

    primary = prereg.get("primary_metric") if isinstance(prereg.get("primary_metric"), dict) else {}
    if primary.get("name") != "MULTICLASS_HDA_LOGLOSS":
        errors.append("primary_metric_mismatch")
    if int(primary.get("bootstrap_resamples") or 0) != 2000:
        errors.append("bootstrap_resamples_mismatch")
    if "upper bound must be < 0" not in str(primary.get("pooled_success_rule") or ""):
        errors.append("primary_uncertainty_gate_missing")

    coverage = prereg.get("coverage_gate") if isinstance(prereg.get("coverage_gate"), dict) else {}
    if coverage.get("required_before_any_target_label_access") is not True:
        errors.append("coverage_not_required_before_labels")
    if int(coverage.get("minimum_fully_eligible_fixture_count") or 0) < 300:
        errors.append("coverage_minimum_below_300")
    if coverage.get("complete_same_freeze_1x2_baseline_required") is not True:
        errors.append("same_freeze_1x2_baseline_not_required")
    if int(coverage.get("invalid_record_tolerance") or -1) != 0:
        errors.append("invalid_record_tolerance_nonzero")

    sample = prereg.get("sample_identity_policy") if isinstance(prereg.get("sample_identity_policy"), dict) else {}
    if sample.get("identity_manifest_required_before_authorization") is not True:
        errors.append("identity_manifest_not_required")
    if sample.get("identity_manifest_sha256_required") is not True:
        errors.append("identity_manifest_sha_not_required")
    if sample.get("sample_manifest_status") != "NOT_YET_FROZEN":
        errors.append("sample_manifest_status_unexpected")
    if sample.get("target_labels_in_manifest") != "FORBIDDEN":
        errors.append("target_labels_not_forbidden_in_manifest")

    auth = prereg.get("authorization_gate") if isinstance(prereg.get("authorization_gate"), dict) else {}
    if auth.get("current_state") != "NOT_AUTHORIZED":
        errors.append("authorization_state_not_locked")
    if auth.get("generic_continue_is_authorization") is not False:
        errors.append("generic_continue_authorization_not_false")

    zero = prereg.get("zero_label_invariants") if isinstance(prereg.get("zero_label_invariants"), dict) else {}
    expected_zero = (
        "target_match_labels_read",
        "training_runs",
        "scoring_runs",
        "tuning_runs",
        "provider_requests",
        "paid_provider_requests",
        "formal_weight",
    )
    for field in expected_zero:
        if int(zero.get(field) or 0) != 0:
            errors.append(f"zero_label_invariant_nonzero:{field}")
    if zero.get("independent_oos_authorized") is not False:
        errors.append("zero_label_independent_oos_authorized")

    status = {
        "schema_version": "R45B-INDEPENDENT-OOS-PREREG-VALIDATION-R1",
        "status": "PASS_PREREGISTRATION_ZERO_LABEL_WAITING_SAMPLE_MANIFEST" if not errors else "FAIL_PREREGISTRATION",
        "preregistration_sha256": sha256(PREREG),
        "promotion_gate_sha256": sha256(PROMOTION),
        "mandatory_field_count": len(mandatory) if isinstance(mandatory, list) else 0,
        "candidate_count": len(candidates),
        "minimum_fully_eligible_fixture_count": int(split.get("minimum_fully_eligible_fixture_count") or 0),
        "chronological_test_fold_count": int(split.get("test_fold_count") or 0),
        "errors": errors,
        "sample_manifest_frozen": False,
        "target_match_labels_read": 0,
        "training_runs": 0,
        "scoring_runs": 0,
        "tuning_runs": 0,
        "provider_requests": 0,
        "paid_provider_requests": 0,
        "formal_weight": 0,
        "independent_oos_authorized": False,
        "ruling": "Preregistration completeness does not authorize target labels, model fitting, scoring, or OOS execution. A >=300 fully eligible zero-label sample identity manifest must be frozen and explicitly authorized separately."
    }
    OUT.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
