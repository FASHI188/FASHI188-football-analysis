from __future__ import annotations

from pathlib import Path
import ast
import re

import pytest

from current_v2_retrospective_candidate_head_v1 import CandidateHeadError, resolve_candidate_exact_head

FOOTBALL3_GOVERNED_RESEARCH_REPLAY = "football3-current-formal-retrospective-research-replay-v1"
MODE = "CURRENT_V2_RETROSPECTIVE_REPLAY"

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "football3-current-v2-retrospective-replay-acceptance.yml"
ACCEPTANCE = ROOT / "governance" / "football3" / "current_v2_retrospective_replay_acceptance_v1.py"
RUNNER_V1 = ROOT / "governance" / "football3" / "current_v2_retrospective_replay_acceptance_runner_v1.py"

PR_MERGE_SHA = "604ad71736fb59a8a6fec0219c471132688d341a"
CANDIDATE_SHA = "f07e21bf6ed79a4768f284e1a8802915b5e06dde"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _job_block(name: str) -> str:
    text = _text()
    marker = f"  {name}:\n"
    start = text.index(marker)
    match = re.search(r"(?m)^  [A-Za-z0-9_-]+:\n", text[start + len(marker):])
    if match is None:
        return text[start:]
    end = start + len(marker) + match.start()
    return text[start:end]


def test_candidate_exact_head_prefers_explicit_pr_head_over_merge_sha():
    env = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_SHA": PR_MERGE_SHA,
        "FOOTBALL3_CANDIDATE_EXACT_HEAD": CANDIDATE_SHA,
    }
    assert resolve_candidate_exact_head(env) == CANDIDATE_SHA


def test_candidate_exact_head_uses_github_sha_when_explicit_head_absent():
    assert resolve_candidate_exact_head({"GITHUB_SHA": CANDIDATE_SHA}) == CANDIDATE_SHA


def test_candidate_exact_head_local_semantics_require_no_github_candidate_environment():
    assert resolve_candidate_exact_head({}) == "LOCAL"
    with pytest.raises(CandidateHeadError, match="CANDIDATE_EXACT_HEAD_MISSING"):
        resolve_candidate_exact_head({"GITHUB_ACTIONS": "true"})


def test_blank_explicit_candidate_does_not_override_valid_github_sha():
    env = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_SHA": CANDIDATE_SHA,
        "FOOTBALL3_CANDIDATE_EXACT_HEAD": "   ",
    }
    assert resolve_candidate_exact_head(env) == CANDIDATE_SHA


@pytest.mark.parametrize("bad", ["abc", "F" * 40, "g" * 40, "0" * 39, "0" * 41])
def test_invalid_explicit_candidate_sha_fails_closed_in_github_candidate_environment(bad: str):
    env = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_SHA": PR_MERGE_SHA,
        "FOOTBALL3_CANDIDATE_EXACT_HEAD": bad,
    }
    with pytest.raises(CandidateHeadError, match="FOOTBALL3_CANDIDATE_EXACT_HEAD_INVALID"):
        resolve_candidate_exact_head(env)


def test_acceptance_summary_candidate_head_is_bound_through_explicit_resolver():
    tree = ast.parse(ACCEPTANCE.read_text(encoding="utf-8"))
    matches = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value == "candidate_exact_head":
                matches.append(value)
    assert any(
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "resolve_candidate_exact_head"
        and not value.args and not value.keywords
        for value in matches
    )


def test_failure_diagnostics_candidate_head_is_bound_to_explicit_candidate_environment():
    tree = ast.parse(RUNNER_V1.read_text(encoding="utf-8"))
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_write_diagnostics")
    exact_assign = next(
        node for node in fn.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "exact_head" for target in node.targets)
    )
    assert isinstance(exact_assign.value, ast.Call) and isinstance(exact_assign.value.func, ast.Attribute)
    assert exact_assign.value.func.attr == "strip"
    inner = exact_assign.value.func.value
    assert isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute) and inner.func.attr == "get"
    assert inner.args and isinstance(inner.args[0], ast.Constant)
    assert inner.args[0].value == "FOOTBALL3_CANDIDATE_EXACT_HEAD"
    diagnostics = next(
        node.value for node in fn.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "diagnostics" for target in node.targets)
    )
    assert isinstance(diagnostics, ast.Dict)
    pairs = {key.value: value for key, value in zip(diagnostics.keys, diagnostics.values) if isinstance(key, ast.Constant)}
    assert isinstance(pairs["candidate_exact_head"], ast.Name) and pairs["candidate_exact_head"].id == "exact_head"


def test_targeted_command_is_captured_before_failure_enforcement():
    block = _job_block("targeted-adapter-contract")
    assert "id: execute-targeted" in block
    assert "set +e" in block
    assert "Upload targeted contract evidence\n        if: ${{ always() }}" in block
    assert block.index("Upload targeted contract evidence") < block.index("Enforce targeted contract result")


def test_batch_command_and_aggregate_result_are_captured_before_enforcement():
    block = _job_block("four-fixture-eight-domain")
    assert "id: execute-batch" in block and "id: summarize-batch" in block
    assert "echo \"batch_rc=$rc\" >> \"$GITHUB_OUTPUT\"" in block
    assert "aggregate_result" in block
    assert block.index("Upload replay acceptance evidence") < block.index("Enforce batch and aggregate result after evidence upload")


def test_candidate_sha_is_checkout_and_provenance_source_across_acceptance_evidence():
    batch = _job_block("four-fixture-eight-domain")
    assert "CANDIDATE_SHA: ${{ github.event.pull_request.head.sha || github.sha }}" in batch
    assert "ref: ${{ env.CANDIDATE_SHA }}" in batch
    assert 'test "$(git rev-parse HEAD)" = "$CANDIDATE_SHA"' in batch
    assert "FOOTBALL3_CANDIDATE_EXACT_HEAD: ${{ env.CANDIDATE_SHA }}" in batch
    assert "FOOTBALL3_CANDIDATE_EXACT_HEAD: ${{ github.sha }}" not in batch
    assert "summary['candidate_exact_head']==os.environ['CANDIDATE_SHA']" in batch
    assert "candidate_exact_head.txt" in batch

    aggregate = _job_block("aggregate-acceptance-gate")
    assert "CANDIDATE_SHA: ${{ github.event.pull_request.head.sha || github.sha }}" in aggregate
    assert '\"candidate_exact_head\":\"%s\"' in aggregate

    failure = _job_block("acceptance-failure-evidence")
    assert "CANDIDATE_SHA: ${{ github.event.pull_request.head.sha || github.sha }}" in failure
    assert "'candidate_exact_head':os.environ['CANDIDATE_SHA']" in failure
    assert "(out/'candidate_exact_head.txt').write_text(os.environ['CANDIDATE_SHA']+'\\n'" in failure


def test_always_evidence_needs_targeted_batch_and_aggregate():
    block = _job_block("acceptance-failure-evidence")
    assert "needs: [targeted-adapter-contract, four-fixture-eight-domain, aggregate-acceptance-gate]" in block
    assert "if: ${{ always() }}" in block
    assert "TARGETED_RESULT: ${{ needs.targeted-adapter-contract.result }}" in block
    assert "BATCH_RESULT: ${{ needs.four-fixture-eight-domain.result }}" in block
    assert "AGGREGATE_RESULT: ${{ needs.aggregate-acceptance-gate.result }}" in block


def test_always_evidence_uses_visible_directory_and_uploads_before_enforce():
    block = _job_block("acceptance-failure-evidence")
    assert "acceptance_always_evidence/" in block
    assert ".acceptance_always_evidence" not in block
    assert "- name: Upload always-on acceptance evidence\n        if: ${{ always() }}" in block
    assert block.index("Upload always-on acceptance evidence") < block.index("Enforce upstream acceptance status after evidence upload")


def test_always_evidence_does_not_depend_on_artifact_download_or_model_execution():
    block = _job_block("acceptance-failure-evidence")
    assert "actions/artifacts/" not in block
    assert "urllib.request" not in block
    assert "actions/checkout" not in block
    assert "current_v2_retrospective_replay_acceptance_runner" not in block
    assert "'production_dispatch_performed':False" in block
    assert "'pr_341_modified':False" in block
    assert "'model_current_weights_changed':False" in block


def test_failure_combinations_still_have_always_build_and_upload_path():
    block = _job_block("acceptance-failure-evidence")
    for _targeted, _batch, _aggregate in [
        ("failure", "skipped", "failure"),
        ("success", "failure", "failure"),
        ("success", "success", "failure"),
        ("success", "success", "success"),
    ]:
        assert "Build minimum always-on acceptance evidence\n        if: ${{ always() }}" in block
        assert "Upload always-on acceptance evidence\n        if: ${{ always() }}" in block
        assert block.index("Build minimum always-on acceptance evidence") < block.index("Upload always-on acceptance evidence") < block.index("Enforce upstream acceptance status after evidence upload")


def test_success_gates_are_not_lowered():
    block = _job_block("four-fixture-eight-domain")
    for token in [
        "g['fixture_resolved_percent']==100.0",
        "g['valid_receipt_percent']==100.0",
        "g['nonempty_prediction_sha_percent']==100.0",
        "g['result_target_post_kickoff_exclusion_percent']==100.0",
        "g['identity_anomaly_count']==0",
        "g['state_anomaly_count']==0",
        "g['matrix_inconsistency_count']==0",
    ]:
        assert token in block
