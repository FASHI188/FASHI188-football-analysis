from __future__ import annotations

from pathlib import Path
import re

FOOTBALL3_GOVERNED_RESEARCH_REPLAY = "football3-current-formal-retrospective-research-replay-v1"
MODE = "CURRENT_V2_RETROSPECTIVE_REPLAY"

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "football3-current-v2-retrospective-replay-acceptance.yml"


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
