#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "football-data/research/r41r2_pit_availability_transition_prereg_20260814.json"
SEAL = ROOT / "football-data/research/r41_legacy_reproducibility_seal_20260814.md"

EXPECTED_FEATURES = [
    "X1=S1_current-S1_previous",
    "X2=S2_current-S2_previous",
    "X3=S3_current-S3_previous",
    "X4=S4_current-S4_previous",
    "X5=S5_current-S5_previous",
    "X6=S6_current-S6_previous",
]
EXPECTED_MANIFEST_SHA256 = "4e22607eee2f0f4729f26cfee826bedac74a3ce5012160a1643c46429a69860e"
LEGACY_SHA256 = "eea489b77ca4c89a9ab44681599c636f5dbc8d4df60040825798670b860fede1"


def main() -> int:
    obj = json.loads(PREREG.read_text(encoding="utf-8"))
    seal = SEAL.read_text(encoding="utf-8")

    checks = {
        "candidate_id": obj["candidate"]["id"] == "R41R2_PIT_AVAILABILITY_TRANSITION_RESIDUAL_R1",
        "new_not_reconstruction": obj["candidate"]["relationship_to_legacy_r41"] == "NEW_CANDIDATE_SAME_HYPOTHESIS_FAMILY_NOT_A_RECONSTRUCTION",
        "features_exact": obj["candidate"]["features"] == EXPECTED_FEATURES,
        "l2_lambda": obj["candidate"]["model"]["l2_lambda"] == 1.0,
        "max_iter": obj["candidate"]["model"]["max_iter"] == 50,
        "tolerance": obj["candidate"]["model"]["tolerance"] == 1e-8,
        "natural_argmax_only": obj["candidate"]["model"]["top1_rule"] == "argmax(qH,qD,qA); no threshold, no forced draw selector, no Top-k override",
        "confirmation_labels_closed": obj["development_and_confirmation"]["confirmation_labels_currently_open"] is False,
        "minimum_rows_150": obj["development_and_confirmation"]["minimum_confirmation_rows"] == 150,
        "zero_label_rows_380": obj["development_and_confirmation"]["confirmed_zero_label_eligible_rows"] == 380,
        "identity_manifest_bound": obj["development_and_confirmation"]["confirmation_identity_manifest_sha256"] == EXPECTED_MANIFEST_SHA256,
        "legacy_sha_bound": obj["legacy_r41"]["original_prereg_sha256_recorded"] == LEGACY_SHA256,
        "legacy_not_inherited": obj["legacy_r41"]["inherit_old_effect_as_prior_or_gate"] is False,
        "legacy_sealed": obj["legacy_r41"]["status"] == "SEALED_REPRODUCIBILITY_FAILURE",
        "formal_weight_zero": obj["formal_weight"] == 0,
        "label_open_not_authorized": obj["authorization"]["this_file_authorizes_label_opening"] is False,
        "training_not_authorized": obj["authorization"]["this_file_authorizes_training"] is False,
        "scoring_not_authorized": obj["authorization"]["this_file_authorizes_scoring"] is False,
        "promotion_not_authorized": obj["authorization"]["this_file_authorizes_formal_promotion"] is False,
        "legacy_seal_contains_fail_gate": "FAIL_ORIGINAL_PREREG_NOT_RECOVERED" in seal,
        "legacy_seal_forbids_same_candidate_replication": "legacy_r41_same_candidate_replication_allowed = false" in seal,
    }
    failed = sorted(k for k, v in checks.items() if not v)
    if failed:
        raise SystemExit("R41R2 reproducibility prereg FAIL: " + ", ".join(failed))

    canonical = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    canonical_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    receipt = {
        "status": "PASS_R41R2_ZERO_LABEL_REPRODUCIBILITY_PREREG",
        "candidate_id": obj["candidate"]["id"],
        "canonical_prereg_sha256": canonical_sha256,
        "confirmation_labels_open": False,
        "training_runs": 0,
        "scoring_runs": 0,
        "tuning_runs": 0,
        "formal_weight": 0,
        "checks": checks,
    }
    out = ROOT / "r41r2-prereg-validation"
    out.mkdir(exist_ok=True)
    (out / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
