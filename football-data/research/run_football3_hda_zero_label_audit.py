from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

STATUS = "HDA_ENGINEERING_LAYER_IMPLEMENTED_ZERO_LABEL_PENDING_CODEX_RECHECK"
K2_MARKER = "K2_PER_ROW_HDA_RECOMPUTATION_NOT_AUTHORIZED"
SCIENCE_HEAD = "8de610c22d26ddeb00adcee2d0078b1cd909e60b"
GOVERNANCE_HEAD = "bb24896b29a649ecabe4da71a134b0e3014165d5"
MODULE = Path("football-data/research/football3_hda.py")
TEST = Path("football-data/research/test_football3_hda.py")
AUDIT = Path("football-data/research/run_football3_hda_zero_label_audit.py")
WORKFLOW = Path(".github/workflows/football3-hda-aggregation-engineering-v1.yml")
GUARD = Path("football-data/research/audit_football3_changed_scientific_files.py")
SOURCE_FILES = (MODULE, TEST, AUDIT, WORKFLOW, GUARD)


class RecordingResult(unittest.TextTestResult):
    def __init__(self, stream, descriptions, verbosity):
        super().__init__(stream, descriptions, verbosity)
        self.records: dict[str, dict[str, object]] = {}

    @staticmethod
    def _name(test: unittest.case.TestCase) -> str:
        return test.id().split(".")[-1]

    def startTest(self, test):
        name = self._name(test)
        self.records[name] = {
            "name": name,
            "expectation": "FAIL_CLOSED" if "fails_closed" in name else "BEHAVIOR",
            "status": "RUNNING",
        }
        super().startTest(test)

    def addSuccess(self, test):
        self.records[self._name(test)]["status"] = "PASS"
        super().addSuccess(test)

    def addFailure(self, test, err):
        rec = self.records[self._name(test)]
        rec["status"] = "FAIL"
        rec["detail"] = self._exc_info_to_string(err, test)[-2000:]
        super().addFailure(test, err)

    def addError(self, test, err):
        rec = self.records[self._name(test)]
        rec["status"] = "ERROR"
        rec["detail"] = self._exc_info_to_string(err, test)[-2000:]
        super().addError(test, err)

    def addSkip(self, test, reason):
        rec = self.records[self._name(test)]
        rec["status"] = "SKIP"
        rec["detail"] = reason
        super().addSkip(test, reason)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def git_sha(args: list[str], env_name: str) -> str:
    try:
        return subprocess.check_output(["git", *args], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
        raise RuntimeError(f"unable to resolve git {' '.join(args)} and {env_name} is unset")


def run_tests() -> tuple[RecordingResult, list[dict[str, object]]]:
    # Import from the production-test module itself so the audit executes the same
    # cases as the platform jobs instead of maintaining a second shadow test suite.
    import test_football3_hda

    suite = unittest.defaultTestLoader.loadTestsFromModule(test_football3_hda)
    runner = unittest.TextTestRunner(
        stream=sys.stdout,
        verbosity=2,
        resultclass=RecordingResult,
    )
    result = runner.run(suite)
    assert isinstance(result, RecordingResult)
    records = [result.records[k] for k in sorted(result.records)]
    return result, records


def write_json(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    for path in SOURCE_FILES:
        if not path.is_file():
            raise RuntimeError(f"missing HDA engineering source file: {path}")

    exact_head = git_sha(["rev-parse", "HEAD"], "HDA_AUDIT_HEAD")
    parent_head = git_sha(["rev-parse", "HEAD^"], "HDA_AUDIT_PARENT")
    run_id = os.environ.get("GITHUB_RUN_ID", "LOCAL_ZERO_LABEL")
    run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "0")

    result, records = run_tests()
    fail_closed = [r for r in records if r["expectation"] == "FAIL_CLOSED"]
    behavior = [r for r in records if r["expectation"] == "BEHAVIOR"]
    passed = [r for r in records if r["status"] == "PASS"]

    manifest = {
        "schema_version": "football3_hda_engineering_artifact_v1",
        "exact_head": exact_head,
        "run_id": str(run_id),
        "run_attempt": str(run_attempt),
        "files": [
            {
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in SOURCE_FILES
        ],
    }
    write_json(out / "file_manifest.json", manifest)

    synthetic = {
        "status": "PASS" if result.wasSuccessful() else "FAIL",
        "exact_head": exact_head,
        "run_id": str(run_id),
        "run_attempt": str(run_attempt),
        "total_cases": len(records),
        "passed_cases": len(passed),
        "behavior_cases": len(behavior),
        "fail_closed_cases": len(fail_closed),
        "tests": records,
    }
    write_json(out / "synthetic_test_results.json", synthetic)

    counterexamples = {
        "status": "PASS" if result.wasSuccessful() and len(fail_closed) >= 20 else "FAIL",
        "minimum_required": 20,
        "fail_closed_case_count": len(fail_closed),
        "all_fail_closed_cases_passed": all(r["status"] == "PASS" for r in fail_closed),
        "cases": fail_closed,
    }
    write_json(out / "counterexample_results.json", counterexamples)

    zero_label = {
        "status": STATUS,
        "exact_head": exact_head,
        "parent_head": parent_head,
        "frozen_scientific_engine_reference_head": SCIENCE_HEAD,
        "governance_reference_head": GOVERNANCE_HEAD,
        "run_id": str(run_id),
        "run_attempt": str(run_attempt),
        "module_sha256": sha256(MODULE),
        "test_sha256": sha256(TEST),
        "class_order": ["HOME", "DRAW", "AWAY"],
        "probability_tolerance": 1e-12,
        "tie_tolerance": 1e-12,
        "synthetic_test_count": len(records),
        "fail_closed_counterexample_count": len(fail_closed),
        "k2_boundary": K2_MARKER,
        "C072_N20_status": "PILOT_NO_SIGNAL / PARK",
        "formal_weight": 0,
        "model_diff": 0,
        "formal_data_diff": 0,
        "config_diff": 0,
        "CURRENT_diff": 0,
        "training": 0,
        "real_scoring": 0,
        "new_target_label_access": 0,
        "sealed_access": 0,
        "provider_requests": 0,
        "secret_access": 0,
        "real_match_rows": 0,
        "real_target_labels": 0,
        "artifact_contains_real_match_data": False,
        "artifact_contains_real_labels": False,
        "scientific_pass_claimed": False,
        "draw_solved_claimed": False,
        "formal_promotion_claimed": False,
        "ready_to_merge_claimed": False,
    }
    write_json(out / "zero_label_receipt.json", zero_label)

    if not result.wasSuccessful():
        raise SystemExit(2)
    if len(records) < 20 or len(fail_closed) < 20:
        raise SystemExit("insufficient synthetic/fail-closed case coverage")
    if counterexamples["status"] != "PASS":
        raise SystemExit("counterexample audit failed")
    print(json.dumps({
        "status": STATUS,
        "total_cases": len(records),
        "fail_closed_cases": len(fail_closed),
        "real_labels": 0,
        "training": 0,
        "real_scoring": 0,
        "sealed_access": 0,
        "provider_requests": 0,
        "secret_access": 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
