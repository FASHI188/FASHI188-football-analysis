from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

FOOTBALL3_ZERO_LABEL_AUDIT_SURFACE = "HDA_ZERO_LABEL_ARTIFACT_AUDIT_ONLY"

STATUS = "GPT_REMEDIATED_PENDING_CODEX_RECHECK"
K2_MARKER = "K2_PER_ROW_HDA_RECOMPUTATION_NOT_AUTHORIZED"
EXPECTED_PARENT_HEAD = "4995168386f17208b0c176e15814bc010bdc5802"
PR_BASE_HEAD = "8de610c22d26ddeb00adcee2d0078b1cd909e60b"
FROZEN_SCIENCE_ENGINE_HEAD = PR_BASE_HEAD
GOVERNANCE_REFERENCE_HEAD = "bb24896b29a649ecabe4da71a134b0e3014165d5"
EXPECTED_TEST_COUNT = 46
EXPECTED_FAIL_CLOSED_COUNT = 31
MODULE = Path("football-data/research/football3_hda.py")
TEST = Path("football-data/research/test_football3_hda.py")
SUPPORT_REGISTRY = Path("football-data/research/football3_hda_score_support_registry_v1.json")
AUDIT = Path("football-data/research/run_football3_hda_zero_label_audit.py")
WORKFLOW = Path(".github/workflows/football3-hda-aggregation-engineering-v1.yml")
GUARD = Path("football-data/research/audit_football3_changed_scientific_files.py")
SOURCE_FILES = (MODULE, TEST, SUPPORT_REGISTRY, AUDIT, WORKFLOW, GUARD)
EXPECTED_CHANGED_FILES = {str(p) for p in SOURCE_FILES}


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


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True, stderr=subprocess.STDOUT).strip()


def changed_files(base: str, head: str) -> list[str]:
    raw = git("diff", "--name-only", f"{base}..{head}")
    return sorted(line.strip() for line in raw.splitlines() if line.strip())


def classify_asset_diff(paths: list[str]) -> dict[str, list[str]]:
    model_suffixes = (".pkl", ".pickle", ".joblib", ".onnx", ".pt", ".pth", ".ckpt", ".bin")
    data_suffixes = (".csv", ".parquet", ".feather", ".arrow", ".h5", ".hdf5", ".sqlite", ".db")
    model = [p for p in paths if p.startswith(("football-data/models/", "football-data/model/", "models/")) or p.lower().endswith(model_suffixes)]
    formal_data = [p for p in paths if p.startswith(("football-data/data/", "football-data/datasets/", "data/", "datasets/")) or p.lower().endswith(data_suffixes)]
    config = [p for p in paths if p.startswith("football-data/config/")]
    current = [p for p in paths if Path(p).name.upper() == "CURRENT" or "CURRENT." in Path(p).name.upper() or "_CURRENT" in Path(p).name.upper()]
    return {
        "model_diff_paths": model,
        "formal_data_diff_paths": formal_data,
        "config_diff_paths": config,
        "CURRENT_diff_paths": current,
    }


def run_tests() -> tuple[RecordingResult, list[dict[str, object]]]:
    import test_football3_hda

    suite = unittest.defaultTestLoader.loadTestsFromModule(test_football3_hda)
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2, resultclass=RecordingResult)
    result = runner.run(suite)
    assert isinstance(result, RecordingResult)
    return result, [result.records[k] for k in sorted(result.records)]


def write_json(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    for path in SOURCE_FILES:
        if not path.is_file():
            raise RuntimeError(f"missing HDA engineering source file: {path}")

    exact_head = git("rev-parse", "HEAD")
    parent_head = git("rev-parse", "HEAD^")
    expected_head = os.environ.get("HDA_EXPECTED_HEAD", "").strip()
    if not expected_head:
        raise RuntimeError("HDA_EXPECTED_HEAD must be explicitly supplied by the exact-head workflow")
    if exact_head != expected_head:
        raise RuntimeError(f"exact HEAD mismatch: git={exact_head} workflow={expected_head}")
    if parent_head != EXPECTED_PARENT_HEAD:
        raise RuntimeError(f"parent HEAD mismatch: expected {EXPECTED_PARENT_HEAD}, got {parent_head}")
    if git("merge-base", exact_head, PR_BASE_HEAD) != PR_BASE_HEAD:
        raise RuntimeError(f"PR base {PR_BASE_HEAD} is not an ancestor of exact HEAD {exact_head}")

    run_id = os.environ.get("GITHUB_RUN_ID", "").strip()
    run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "").strip()
    if not run_id or not run_id.isdigit():
        raise RuntimeError("GITHUB_RUN_ID missing or invalid")
    if not run_attempt or not run_attempt.isdigit():
        raise RuntimeError("GITHUB_RUN_ATTEMPT missing or invalid")

    paths = changed_files(PR_BASE_HEAD, exact_head)
    unexpected = sorted(set(paths) - EXPECTED_CHANGED_FILES)
    missing_expected = sorted(EXPECTED_CHANGED_FILES - set(paths))
    if unexpected:
        raise RuntimeError(f"unexpected PR-scope files for HDA engineering remediation: {unexpected}")
    if missing_expected:
        raise RuntimeError(f"expected HDA remediation files missing from PR diff: {missing_expected}")
    asset_diff = classify_asset_diff(paths)
    if any(asset_diff.values()):
        raise RuntimeError(f"forbidden formal/model/data/config/CURRENT diff: {asset_diff}")

    result, records = run_tests()
    fail_closed = [r for r in records if r["expectation"] == "FAIL_CLOSED"]
    behavior = [r for r in records if r["expectation"] == "BEHAVIOR"]
    passed = [r for r in records if r["status"] == "PASS"]

    if len(records) != EXPECTED_TEST_COUNT:
        raise RuntimeError(f"expected exactly {EXPECTED_TEST_COUNT} zero-label tests, got {len(records)}")
    if len(fail_closed) != EXPECTED_FAIL_CLOSED_COUNT:
        raise RuntimeError(f"expected exactly {EXPECTED_FAIL_CLOSED_COUNT} fail-closed counterexamples, got {len(fail_closed)}")

    manifest = {
        "schema_version": "football3_hda_engineering_artifact_v2",
        "exact_head": exact_head,
        "parent_head": parent_head,
        "run_id": run_id,
        "run_attempt": run_attempt,
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
        "run_id": run_id,
        "run_attempt": run_attempt,
        "total_cases": len(records),
        "passed_cases": len(passed),
        "behavior_cases": len(behavior),
        "fail_closed_cases": len(fail_closed),
        "tests": records,
    }
    write_json(out / "synthetic_test_results.json", synthetic)

    counterexamples = {
        "status": "PASS" if result.wasSuccessful() and all(r["status"] == "PASS" for r in fail_closed) else "FAIL",
        "expected_fail_closed_cases": EXPECTED_FAIL_CLOSED_COUNT,
        "fail_closed_case_count": len(fail_closed),
        "all_fail_closed_cases_passed": all(r["status"] == "PASS" for r in fail_closed),
        "cases": fail_closed,
    }
    write_json(out / "counterexample_results.json", counterexamples)

    zero_label = {
        "status": STATUS,
        "exact_head": exact_head,
        "parent_head": parent_head,
        "pr_base_head": PR_BASE_HEAD,
        "frozen_scientific_engine_reference_head": FROZEN_SCIENCE_ENGINE_HEAD,
        "governance_reference_head": GOVERNANCE_REFERENCE_HEAD,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "module_sha256": sha256(MODULE),
        "test_sha256": sha256(TEST),
        "support_registry_sha256": sha256(SUPPORT_REGISTRY),
        "changed_files": paths,
        **asset_diff,
        "class_order": ["HOME", "DRAW", "AWAY"],
        "probability_tolerance": 1e-12,
        "tie_tolerance": 1e-12,
        "synthetic_test_count": len(records),
        "fail_closed_counterexample_count": len(fail_closed),
        "k2_boundary": K2_MARKER,
        "C072_N20_status": "PILOT_NO_SIGNAL / PARK",
        "formal_weight": 0,
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
