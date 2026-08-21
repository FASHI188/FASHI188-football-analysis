from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path, PurePath, PurePosixPath

FOOTBALL3_ZERO_LABEL_AUDIT_SURFACE = "HDA_ZERO_LABEL_ARTIFACT_AUDIT_ONLY"

STATUS = "GPT_REMEDIATED_R5_PENDING_CODEX_RECHECK"
K2_MARKER = "K2_PER_ROW_HDA_RECOMPUTATION_NOT_AUTHORIZED"
R2_FAILED_ANCESTOR = "bc43db3c7f4f7d76ca46387d0c9cca94f49f8611"
PR_BASE_HEAD = "8de610c22d26ddeb00adcee2d0078b1cd909e60b"
FROZEN_SCIENCE_ENGINE_HEAD = PR_BASE_HEAD
GOVERNANCE_REFERENCE_HEAD = "bb24896b29a649ecabe4da71a134b0e3014165d5"
EXPECTED_TEST_COUNT = 93
EXPECTED_FAIL_CLOSED_COUNT = 62
EXPECTED_CONTRACT_COUNT = 1
EXPECTED_BEHAVIOR_COUNT = 30
MODULE = Path("football-data/research/football3_hda.py")
SCORING = Path("football-data/research/football3_hda_scoring.py")
TEST = Path("football-data/research/test_football3_hda.py")
SUPPORT_REGISTRY = Path("football-data/research/football3_hda_score_support_registry_v1.json")
AUDIT = Path("football-data/research/run_football3_hda_zero_label_audit.py")
WORKFLOW = Path(".github/workflows/football3-hda-aggregation-engineering-v1.yml")
GUARD = Path("football-data/research/audit_football3_changed_scientific_files.py")
SOURCE_FILES = (MODULE, SCORING, TEST, SUPPORT_REGISTRY, AUDIT, WORKFLOW, GUARD)


def repo_path(path: PurePath) -> str:
    """Canonical repository identity path, independent of host path separator."""
    return path.as_posix()


EXPECTED_CHANGED_FILES = {repo_path(path) for path in SOURCE_FILES}


def scope_differences(actual_paths: list[str], expected_paths: set[str]) -> tuple[list[str], list[str]]:
    # git diff --name-only emits repository POSIX paths. PurePosixPath normalizes only
    # redundant POSIX separators/dots; it does not reinterpret backslashes or hide names.
    actual = {PurePosixPath(path).as_posix() for path in actual_paths}
    return sorted(actual - expected_paths), sorted(expected_paths - actual)


class RecordingResult(unittest.TextTestResult):
    def __init__(self, stream, descriptions, verbosity):
        super().__init__(stream, descriptions, verbosity)
        self.records: dict[str, dict[str, object]] = {}

    @staticmethod
    def _name(test: unittest.case.TestCase) -> str:
        return test.id().split(".")[-1]

    def startTest(self, test):
        name = self._name(test)
        if "fails_closed" in name:
            expectation = "FAIL_CLOSED"
        elif name.startswith("test_r5_contract_"):
            expectation = "CONTRACT"
        else:
            expectation = "BEHAVIOR"
        self.records[name] = {
            "name": name,
            "expectation": expectation,
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
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.check_output(["git", *args], text=True, stderr=subprocess.STDOUT, cwd=cwd).strip()


def changed_files(base: str, head: str, *, cwd: Path | None = None) -> list[str]:
    raw = git("diff", "--name-only", f"{base}..{head}", cwd=cwd)
    return sorted(PurePosixPath(line.strip()).as_posix() for line in raw.splitlines() if line.strip())


def classify_asset_diff(paths: list[str]) -> dict[str, list[str]]:
    model_suffixes = (".pkl", ".pickle", ".joblib", ".onnx", ".pt", ".pth", ".ckpt", ".bin")
    data_suffixes = (".csv", ".parquet", ".feather", ".arrow", ".h5", ".hdf5", ".sqlite", ".db")
    model = [p for p in paths if p.startswith(("football-data/models/", "football-data/model/", "models/")) or p.lower().endswith(model_suffixes)]
    formal_data = [p for p in paths if p.startswith(("football-data/data/", "football-data/datasets/", "data/", "datasets/")) or p.lower().endswith(data_suffixes)]
    config = [p for p in paths if p.startswith("football-data/config/")]
    current = [p for p in paths if PurePosixPath(p).name.upper() == "CURRENT" or "CURRENT." in PurePosixPath(p).name.upper() or "_CURRENT" in PurePosixPath(p).name.upper()]
    return {
        "model_diff_paths": model,
        "formal_data_diff_paths": formal_data,
        "config_diff_paths": config,
        "CURRENT_diff_paths": current,
    }


def validate_lineage(
    *,
    expected_exact_head: str,
    r2_failed_ancestor: str,
    pr_base_head: str,
    expected_changed_files: set[str],
    cwd: Path | None = None,
    claimed_direct_parent: str | None = None,
) -> dict[str, object]:
    """Fail closed on non-linear or scope-expanding R3 history.

    The failed R2 commit is a historical ancestor, not a permanent direct parent.
    The current direct parent is always derived from the checked-out exact HEAD.
    Every commit after R2 is audited so an extra file cannot be added and later
    removed to disappear from the final PR net diff.
    """

    exact_head = git("rev-parse", "HEAD", cwd=cwd)
    if exact_head != expected_exact_head:
        raise RuntimeError(f"exact HEAD mismatch: git={exact_head} workflow={expected_exact_head}")

    try:
        direct_parent = git("rev-parse", "HEAD^", cwd=cwd)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"exact HEAD has no resolvable direct parent: {exact_head}") from exc

    if claimed_direct_parent is not None and claimed_direct_parent != direct_parent:
        raise RuntimeError(
            f"direct parent claim mismatch: claimed={claimed_direct_parent} git_HEAD^={direct_parent}"
        )

    ancestor_check = subprocess.run(
        ["git", "merge-base", "--is-ancestor", r2_failed_ancestor, exact_head],
        cwd=cwd,
        text=True,
        capture_output=True,
    )
    if ancestor_check.returncode != 0:
        raise RuntimeError(
            f"R2 failed ancestor is not an ancestor of exact HEAD: "
            f"ancestor={r2_failed_ancestor} exact_head={exact_head}"
        )

    lineage_raw = git(
        "rev-list",
        "--reverse",
        "--parents",
        f"{r2_failed_ancestor}..{exact_head}",
        cwd=cwd,
    )
    lineage_rows = [line.split() for line in lineage_raw.splitlines() if line.strip()]
    if not lineage_rows:
        raise RuntimeError("R3 lineage must contain at least one commit after the R2 failed ancestor")

    for fields in lineage_rows:
        if len(fields[1:]) != 1:
            raise RuntimeError(f"merge commit detected in R3 lineage: commit={fields[0]} parents={fields[1:]}")

    lineage_commits: list[dict[str, object]] = []
    touched_paths: set[str] = set()
    previous = r2_failed_ancestor
    for fields in lineage_rows:
        commit = fields[0]
        parents = fields[1:]
        parent = parents[0]
        if parent != previous:
            raise RuntimeError(
                f"non-linear R3 ancestry: commit={commit} expected_parent={previous} actual_parent={parent}"
            )
        commit_paths = changed_files(parent, commit, cwd=cwd)
        unexpected_commit_paths = sorted(set(commit_paths) - expected_changed_files)
        if unexpected_commit_paths:
            raise RuntimeError(
                f"unexpected file touched in R3 lineage commit {commit}: {unexpected_commit_paths}"
            )
        commit_asset_diff = classify_asset_diff(commit_paths)
        if any(commit_asset_diff.values()):
            raise RuntimeError(
                f"forbidden formal/model/data/config/CURRENT diff in R3 lineage commit {commit}: {commit_asset_diff}"
            )
        touched_paths.update(commit_paths)
        lineage_commits.append(
            {
                "commit": commit,
                "direct_parent": parent,
                "changed_files": commit_paths,
            }
        )
        previous = commit

    if previous != exact_head:
        raise RuntimeError(f"linear R3 lineage does not terminate at exact HEAD: terminal={previous} head={exact_head}")
    if lineage_commits[-1]["direct_parent"] != direct_parent:
        raise RuntimeError(
            f"direct parent mismatch against lineage terminal commit: "
            f"git_HEAD^={direct_parent} lineage_parent={lineage_commits[-1]['direct_parent']}"
        )

    r2_direct_parent = git("rev-parse", f"{r2_failed_ancestor}^", cwd=cwd)
    r2_failed_commit_paths = changed_files(r2_direct_parent, r2_failed_ancestor, cwd=cwd)
    r2_lineage_scope = sorted(set(r2_failed_commit_paths) | touched_paths)
    unexpected_r2_scope, missing_r2_scope = scope_differences(r2_lineage_scope, expected_changed_files)
    if unexpected_r2_scope:
        raise RuntimeError(f"unexpected file in R2-failed-plus-R3 lineage scope: {unexpected_r2_scope}")
    if missing_r2_scope:
        raise RuntimeError(f"expected HDA file missing from R2-failed-plus-R3 lineage scope: {missing_r2_scope}")
    r2_asset_diff = classify_asset_diff(r2_failed_commit_paths)
    if any(r2_asset_diff.values()):
        raise RuntimeError(f"forbidden formal/model/data/config/CURRENT diff in R2 failed commit: {r2_asset_diff}")

    pr_net_paths = changed_files(pr_base_head, exact_head, cwd=cwd)
    unexpected_pr, missing_pr = scope_differences(pr_net_paths, expected_changed_files)
    if unexpected_pr:
        raise RuntimeError(f"unexpected final PR-scope files for HDA engineering remediation: {unexpected_pr}")
    if missing_pr:
        raise RuntimeError(f"expected HDA remediation files missing from final PR diff: {missing_pr}")
    pr_asset_diff = classify_asset_diff(pr_net_paths)
    if any(pr_asset_diff.values()):
        raise RuntimeError(f"forbidden formal/model/data/config/CURRENT final PR diff: {pr_asset_diff}")

    r2_incremental_net_paths = changed_files(r2_failed_ancestor, exact_head, cwd=cwd)
    unexpected_r2_net = sorted(set(r2_incremental_net_paths) - expected_changed_files)
    if unexpected_r2_net:
        raise RuntimeError(f"unexpected R2-to-exact net files: {unexpected_r2_net}")

    return {
        "exact_head": exact_head,
        "direct_parent": direct_parent,
        "r2_failed_ancestor": r2_failed_ancestor,
        "pr_base_head": pr_base_head,
        "lineage_commit_count": len(lineage_commits),
        "lineage_commits": lineage_commits,
        "lineage_touched_files": sorted(touched_paths),
        "r2_failed_commit_direct_parent": r2_direct_parent,
        "r2_failed_commit_changed_files": r2_failed_commit_paths,
        "r2_failed_plus_r3_scope_files": r2_lineage_scope,
        "r2_incremental_net_changed_files": r2_incremental_net_paths,
        "final_pr_net_changed_files": pr_net_paths,
    }


def run_tests() -> tuple[RecordingResult, list[dict[str, object]]]:
    import test_football3_hda

    suite = unittest.defaultTestLoader.loadTestsFromModule(test_football3_hda)
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2, resultclass=RecordingResult)
    result = runner.run(suite)
    assert isinstance(result, RecordingResult)
    return result, [result.records[key] for key in sorted(result.records)]


def run_production_guard(
    base: str,
    head: str,
    *,
    expected_guard_canonical_ast_sha256: str,
    expected_audit_canonical_ast_sha256: str,
) -> dict[str, object]:
    completed = subprocess.run(
        [
            sys.executable,
            repo_path(GUARD),
            "--base",
            base,
            "--head",
            head,
            "--expected-guard-canonical-ast-sha256",
            expected_guard_canonical_ast_sha256,
            "--expected-audit-canonical-ast-sha256",
            expected_audit_canonical_ast_sha256,
        ],
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"football3 production guard failed: {(completed.stdout + completed.stderr)[-3000:]}")
    return {"returncode": completed.returncode, "stdout_tail": completed.stdout[-2000:]}


def validate_support_registry() -> dict[str, object]:
    from football3_hda import load_score_support_registry

    registry = load_score_support_registry(SUPPORT_REGISTRY)
    if len(registry) != 2:
        raise RuntimeError(f"unexpected frozen support registry entry count: {len(registry)}")
    return {"entry_count": len(registry), "support_ids": sorted(registry)}


def write_json(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--expected-guard-canonical-ast-sha256", required=True)
    parser.add_argument("--expected-audit-canonical-ast-sha256", required=True)
    args = parser.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    for path in SOURCE_FILES:
        if not path.is_file():
            raise RuntimeError(f"missing HDA engineering source file: {repo_path(path)}")

    expected_head = os.environ.get("HDA_EXPECTED_HEAD", "").strip()
    if not expected_head:
        raise RuntimeError("HDA_EXPECTED_HEAD must be explicitly supplied by the exact-head workflow")

    lineage_receipt = validate_lineage(
        expected_exact_head=expected_head,
        r2_failed_ancestor=R2_FAILED_ANCESTOR,
        pr_base_head=PR_BASE_HEAD,
        expected_changed_files=EXPECTED_CHANGED_FILES,
    )
    exact_head = str(lineage_receipt["exact_head"])
    direct_parent = str(lineage_receipt["direct_parent"])

    run_id = os.environ.get("GITHUB_RUN_ID", "").strip()
    run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "").strip()
    if not run_id or not run_id.isdigit():
        raise RuntimeError("GITHUB_RUN_ID missing or invalid")
    if not run_attempt or not run_attempt.isdigit():
        raise RuntimeError("GITHUB_RUN_ATTEMPT missing or invalid")

    paths = list(lineage_receipt["final_pr_net_changed_files"])

    asset_diff = classify_asset_diff(paths)
    if any(asset_diff.values()):
        raise RuntimeError(f"forbidden formal/model/data/config/CURRENT diff: {asset_diff}")

    support_receipt = validate_support_registry()
    guard_receipt = run_production_guard(
        PR_BASE_HEAD,
        exact_head,
        expected_guard_canonical_ast_sha256=args.expected_guard_canonical_ast_sha256,
        expected_audit_canonical_ast_sha256=args.expected_audit_canonical_ast_sha256,
    )
    result, records = run_tests()
    fail_closed = [record for record in records if record["expectation"] == "FAIL_CLOSED"]
    contract = [record for record in records if record["expectation"] == "CONTRACT"]
    behavior = [record for record in records if record["expectation"] == "BEHAVIOR"]
    passed = [record for record in records if record["status"] == "PASS"]

    if len(records) != EXPECTED_TEST_COUNT:
        raise RuntimeError(f"expected exactly {EXPECTED_TEST_COUNT} synthetic/guard tests, got {len(records)}")
    if len(fail_closed) != EXPECTED_FAIL_CLOSED_COUNT:
        raise RuntimeError(f"expected exactly {EXPECTED_FAIL_CLOSED_COUNT} fail-closed counterexamples, got {len(fail_closed)}")
    if len(contract) != EXPECTED_CONTRACT_COUNT:
        raise RuntimeError(f"expected exactly {EXPECTED_CONTRACT_COUNT} contract tests, got {len(contract)}")
    if len(behavior) != EXPECTED_BEHAVIOR_COUNT:
        raise RuntimeError(f"expected exactly {EXPECTED_BEHAVIOR_COUNT} behavior tests, got {len(behavior)}")

    manifest = {
        "schema_version": "football3_hda_engineering_artifact_v4",
        "exact_head": exact_head,
        "direct_parent": direct_parent,
        "r2_failed_ancestor": R2_FAILED_ANCESTOR,
        "pr_base_head": PR_BASE_HEAD,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "files": [
            {
                "path": repo_path(path),
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
        "direct_parent": direct_parent,
        "r2_failed_ancestor": R2_FAILED_ANCESTOR,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "total_cases": len(records),
        "passed_cases": len(passed),
        "behavior_cases": len(behavior),
        "contract_cases": len(contract),
        "fail_closed_cases": len(fail_closed),
        "tests": records,
    }
    write_json(out / "synthetic_test_results.json", synthetic)

    counterexamples = {
        "status": "PASS" if result.wasSuccessful() and all(record["status"] == "PASS" for record in fail_closed) else "FAIL",
        "exact_head": exact_head,
        "direct_parent": direct_parent,
        "r2_failed_ancestor": R2_FAILED_ANCESTOR,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "expected_fail_closed_cases": EXPECTED_FAIL_CLOSED_COUNT,
        "fail_closed_case_count": len(fail_closed),
        "all_fail_closed_cases_passed": all(record["status"] == "PASS" for record in fail_closed),
        "cases": fail_closed,
    }
    write_json(out / "counterexample_results.json", counterexamples)

    zero_label = {
        "status": STATUS,
        "exact_head": exact_head,
        "direct_parent": direct_parent,
        "r2_failed_ancestor": R2_FAILED_ANCESTOR,
        "pr_base_head": PR_BASE_HEAD,
        "frozen_scientific_engine_reference_head": FROZEN_SCIENCE_ENGINE_HEAD,
        "governance_reference_head": GOVERNANCE_REFERENCE_HEAD,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "module_sha256": sha256(MODULE),
        "scoring_module_sha256": sha256(SCORING),
        "test_sha256": sha256(TEST),
        "support_registry_sha256": sha256(SUPPORT_REGISTRY),
        "support_registry_validation": support_receipt,
        "production_guard": guard_receipt,
        "changed_files": paths,
        "lineage_validation": lineage_receipt,
        **asset_diff,
        "class_order": ["HOME", "DRAW", "AWAY"],
        "probability_tolerance": 1e-12,
        "tie_tolerance": 1e-12,
        "proper_scores_primary": True,
        "diagnostic_metrics_secondary": True,
        "synthetic_test_count": len(records),
        "behavior_test_count": len(behavior),
        "contract_test_count": len(contract),
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
        "model_improved_claimed": False,
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
        "behavior_cases": len(behavior),
        "contract_cases": len(contract),
        "fail_closed_cases": len(fail_closed),
        "support_registry_entries": support_receipt["entry_count"],
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
