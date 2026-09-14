#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

OLD_ZIP_SHA256 = "01b655b2748b1e601d82c61e12d763ed50c2db40c3b3b8802aec807c6ac6acba"
TEST_ZIP_SHA256 = "1358636754b33c70a82995b83943cb58a447e38650fef53ed308ff44c4087f1e"
DB_SHA256 = "f102eae39b4036a4c24e5b75b9cee551064cf1e7d4fd028966cd62a5784d8681"
TEST_IDENTITY_SHA256 = "c861032ea523cb921eebc7455941e2a71ecb2f3ecc3a8df6858eef0b7aaf8d95"
TEST_FIXTURE_SET_SHA256 = "0f156c22df1976b929d0db24135830e8b3fac8546130e7bf5e08673918f22bc6"
TEST_VAULT_SHA256 = "444edf8005d6dce3367701cd665947c3e6f852a27a4b6fb15ce0db430f476990"

class PrelabelGateError(RuntimeError):
    pass

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def read_json_member(z: zipfile.ZipFile, name: str) -> dict[str, Any]:
    try:
        raw = z.read(name)
    except KeyError as exc:
        raise PrelabelGateError(f"missing safe metadata member: {name}") from exc
    obj = json.loads(raw.decode("utf-8"))
    if not isinstance(obj, dict):
        raise PrelabelGateError(f"metadata root must be object: {name}")
    return obj

def validate_contract(path: Path) -> dict[str, Any]:
    c = json.loads(path.read_text(encoding="utf-8"))
    if c.get("status") != "DESIGN_LOCKED_PRELABEL":
        raise PrelabelGateError("N1 design must be locked before data inspection")
    if c["exact_base"]["head"] != "86308a4918aa0d7578a11af99a36aacf5781fb1e":
        raise PrelabelGateError("N1 exact base drift")
    if c["data_lock"]["licensed_feature_and_development_source"]["license"] != "MIT":
        raise PrelabelGateError("N1 licensed source mismatch")
    if c["chronology"]["fit_season_keys"] != [2014, 2015, 2016, 2017]:
        raise PrelabelGateError("fit seasons drift")
    if c["chronology"]["development_selection_season_keys"] != [2018, 2019]:
        raise PrelabelGateError("development seasons drift")
    if c["chronology"]["independent_test_season_key"] != 2024:
        raise PrelabelGateError("independent test season drift")
    if c["max_candidate_configurations"] != 12:
        raise PrelabelGateError("candidate search budget drift")
    if c["legacy_signal"]["inherit_old_half_life_16"] is not False:
        raise PrelabelGateError("legacy half-life inheritance forbidden")
    return c

def audit_old_artifact(path: Path) -> dict[str, Any]:
    if sha256_file(path) != OLD_ZIP_SHA256:
        raise PrelabelGateError("old universe artifact digest mismatch")
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        required = {"understat_freeze_receipt.json", "understat_frozen.db", "base_freeze_manifest.json"}
        if not required <= names:
            raise PrelabelGateError("old universe artifact members missing")
        receipt = read_json_member(z, "understat_freeze_receipt.json")
        if receipt.get("database_sha256") != DB_SHA256:
            raise PrelabelGateError("frozen database SHA mismatch")
        if receipt.get("labels_read") != 0 or receipt.get("score_or_result_columns_read") is not False:
            raise PrelabelGateError("old source freeze was not label-blind")
        if receipt.get("provider") != "Cody Tipton player stats per game - Understat":
            raise PrelabelGateError("old source provider mismatch")
    return {"zip_sha256": OLD_ZIP_SHA256, "database_sha256": DB_SHA256, "safe_metadata_only": True, "database_rows_read": False, "target_labels_read": False}

def audit_test_artifact(path: Path) -> dict[str, Any]:
    if sha256_file(path) != TEST_ZIP_SHA256:
        raise PrelabelGateError("independent test source artifact digest mismatch")
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        required = {"artifact_manifest.json", "source_freeze_receipt.json", "confirmation_identity.jsonl", "confirmation_xg_result_vault.jsonl"}
        if not required <= names:
            raise PrelabelGateError("test artifact members missing")
        manifest = read_json_member(z, "artifact_manifest.json")
        receipt = read_json_member(z, "source_freeze_receipt.json")
        if manifest.get("identity_sha256") != TEST_IDENTITY_SHA256:
            raise PrelabelGateError("test identity SHA mismatch")
        if manifest.get("vault_sha256") != TEST_VAULT_SHA256:
            raise PrelabelGateError("test result vault SHA mismatch")
        if receipt.get("fixture_identity_set_sha256") != TEST_FIXTURE_SET_SHA256:
            raise PrelabelGateError("test fixture set SHA mismatch")
        if receipt.get("historical_completed_only") is not True:
            raise PrelabelGateError("test source not completed-only")
        if receipt.get("prospective_queue") is not False:
            raise PrelabelGateError("prospective queue forbidden")
        if receipt.get("identity_contains_result_or_xg") is not False:
            raise PrelabelGateError("test identity must remain label-free")
    return {"zip_sha256": TEST_ZIP_SHA256, "identity_sha256": TEST_IDENTITY_SHA256, "fixture_identity_set_sha256": TEST_FIXTURE_SET_SHA256, "vault_sha256_declared_only": TEST_VAULT_SHA256, "vault_member_opened": False, "raw_pages_opened": False, "target_labels_read": False}

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--contract", required=True, type=Path)
    p.add_argument("--old-artifact", type=Path)
    p.add_argument("--test-artifact", type=Path)
    p.add_argument("--receipt-out", type=Path)
    args = p.parse_args()
    validate_contract(args.contract)
    receipt: dict[str, Any] = {"schema_version": "football3-nova-n1-prelabel-audit-v1", "status": "PASS_PRELABEL_ONLY", "target_labels_read": False, "database_rows_read": False, "test_vault_opened": False}
    if args.old_artifact:
        receipt["development_source"] = audit_old_artifact(args.old_artifact)
    if args.test_artifact:
        receipt["independent_test_source"] = audit_test_artifact(args.test_artifact)
    text = json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.receipt_out:
        args.receipt_out.parent.mkdir(parents=True, exist_ok=True)
        args.receipt_out.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
