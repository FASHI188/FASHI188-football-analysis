#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import re

FOOTBALL3_GOVERNED_RESEARCH_REPLAY = "football3-current-formal-retrospective-research-replay-v1"

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


def test_targeted_failure_and_skipped_batch_still_schedule_evidence_job():
    block = _job_block("acceptance-failure-evidence")
    assert "needs: [targeted-adapter-contract, four-fixture-eight-domain]" in block
    assert "if: ${{ always() }}" in block
    assert "TARGETED_RESULT: ${{ needs.targeted-adapter-contract.result }}" in block
    assert "BATCH_RESULT: ${{ needs.four-fixture-eight-domain.result }}" in block


def test_batch_dependency_may_skip_but_evidence_job_is_independent_of_internal_batch_steps():
    batch = _job_block("four-fixture-eight-domain")
    evidence = _job_block("acceptance-failure-evidence")
    assert "needs: targeted-adapter-contract" in batch
    assert "needs: [targeted-adapter-contract, four-fixture-eight-domain]" in evidence
    assert "if: ${{ always() }}" in evidence
    assert ".retrospective_acceptance" not in evidence or "actions/artifacts" in evidence


def test_batch_failure_and_evidence_collection_both_use_always_uploads():
    batch = _job_block("four-fixture-eight-domain")
    evidence = _job_block("acceptance-failure-evidence")
    assert "- name: Upload replay acceptance evidence\n        if: always()" in batch
    assert "- name: Upload failure resilience diagnostics\n        if: always()" in batch
    assert "- name: Upload always-on acceptance evidence\n        if: ${{ always() }}" in evidence
    assert "actions/upload-artifact@v4" in evidence


def test_success_path_and_aggregate_gate_are_not_relaxed():
    batch = _job_block("four-fixture-eight-domain")
    evidence = _job_block("acceptance-failure-evidence")
    assert "assert g['fixture_resolved_percent']==100.0" in batch
    assert "assert g['valid_receipt_percent']==100.0" in batch
    assert "assert g['nonempty_prediction_sha_percent']==100.0" in batch
    assert "assert g['result_target_post_kickoff_exclusion_percent']==100.0" in batch
    assert "identity_anomaly_count']==0" in batch
    assert "'status': 'PASS' if gate_pass else 'FAIL'" in evidence
    assert "'failure_artifact_is_gate_pass': False" in evidence


def test_evidence_job_is_read_only_and_cannot_dispatch_or_execute_model():
    block = _job_block("acceptance-failure-evidence")
    assert "permissions:\n      contents: read\n      actions: read" in block
    assert "actions/checkout" not in block
    assert "gh workflow" not in block
    assert "curl -X POST" not in block
    assert "current_v2_retrospective_replay_acceptance_runner_v1.py" not in block
    assert "football-data/formal_gpt_gateway_v1/entry.py" not in block
    assert "'production_dispatch_performed': False" in block
    assert "'pr_341_modified': False" in block
    assert "'model_current_weights_changed': False" in block
