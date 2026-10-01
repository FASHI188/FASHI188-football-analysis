from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "validation" / "gpt_fact_gate_v1.py"
SPEC = importlib.util.spec_from_file_location("gpt_fact_gate_v1", MODULE_PATH)
gate = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(gate)

NOW = datetime(2026, 8, 11, 4, 0, tzinfo=timezone.utc)
OBSERVED = "2026-08-11T03:30:00Z"
HEAD = "a" * 40
ARTIFACT_SHA = "b" * 64


def evidence() -> dict:
    common = {"observed_at_utc": OBSERVED, "provenance": "LIVE_API", "raw_payload_sha256": "c" * 64}
    return {
        "schema_version": gate.SCHEMA_VERSION,
        "observed_at_utc": OBSERVED,
        "repository": {"name": "owner/repo", "default_branch": "main", "exact_head": HEAD, **common},
        "pull_request": {"number": 123, "state": "open", "draft": True, "merged": False, "head_sha": HEAD, **common},
        "workflow": {"run_id": 99, "event": "workflow_dispatch", "status": "completed", "conclusion": "success", "head_sha": HEAD, **common},
        "jobs": [
            {"job_id": 101, "name": "preflight", "run_id": 99, "head_sha": HEAD, "status": "completed", "conclusion": "success", **common},
            {"job_id": 102, "name": "research", "run_id": 99, "head_sha": HEAD, "status": "completed", "conclusion": "skipped", **common},
        ],
        "artifacts": [
            {"artifact_id": 501, "name": "evidence", "run_id": 99, "head_sha": HEAD, "sha256": ARTIFACT_SHA, **common},
        ],
        "research": {
            "evaluation_status": "COMPLETE",
            "scientific_gate": "FAIL",
            "metrics": [
                {"metric_id": "draw_f1", "name": "Draw F1 delta", "scope": "holdout", "value": 0.0, "source_artifact_id": 501, "provenance": "SIGNED_ARTIFACT"},
            ],
            "holdout": {"status": "ACCESSED", "labels_accessed_count": 100, "access_authorized": True, "exact_first_access_utc": "UNPROVEN_NOT_MEASURED"},
            "source_artifact_id": 501,
            "source_artifact_sha256": ARTIFACT_SHA,
            "provenance": "SIGNED_ARTIFACT",
        },
        "formal_boundary": {"formal_weight": 0, "model_diff": 0, "formal_data_diff": 0, "config_diff": 0, "current_diff": 0, "provenance": "LOCAL_RECOMPUTATION"},
        "claims": [
            {"claim_id": "head", "claim_type": "EXACT_HEAD", "classification": "VERIFIED", "evidence_refs": ["repository", "pull_request"]},
            {"claim_id": "pr", "claim_type": "PR_STATE", "classification": "VERIFIED", "evidence_refs": ["pull_request"]},
            {"claim_id": "workflow", "claim_type": "WORKFLOW_EXECUTION", "classification": "VERIFIED", "evidence_refs": ["workflow"]},
            {"claim_id": "job", "claim_type": "JOB_EXECUTION", "classification": "VERIFIED", "job_id": 102, "evidence_refs": ["job:102"]},
            {"claim_id": "artifact", "claim_type": "ARTIFACT_IDENTITY", "classification": "VERIFIED", "artifact_id": 501, "evidence_refs": ["artifact:501"]},
            {"claim_id": "gate", "claim_type": "SCIENTIFIC_GATE", "classification": "VERIFIED", "evidence_refs": ["research", "artifact:501"]},
            {"claim_id": "metric", "claim_type": "METRIC_VALUE", "classification": "VERIFIED", "metric_id": "draw_f1", "evidence_refs": ["metric:draw_f1", "artifact:501"]},
            {"claim_id": "holdout", "claim_type": "HOLDOUT_ACCESS", "classification": "VERIFIED", "evidence_refs": ["research", "artifact:501"]},
            {"claim_id": "formal", "claim_type": "FORMAL_BOUNDARY", "classification": "VERIFIED", "evidence_refs": ["formal_boundary"]},
            {"claim_id": "reported", "claim_type": "REPORTED_TEXT", "classification": "REPORTED_NOT_VERIFIED", "text": "另一个报告称队列正在运行。", "evidence_refs": ["research"], "limitations": ["尚未实时核验"]},
            {"claim_id": "inference", "claim_type": "INFERENCE", "classification": "INFERRED", "text": "该方向可能不稳定。", "basis": "连续时间窗结果方向变化", "evidence_refs": ["metric:draw_f1"]},
            {"claim_id": "unknown", "claim_type": "UNKNOWN_FACT", "classification": "UNKNOWN", "question": "下一次运行是否会改善"},
        ],
    }


class GPTFactGateV1Tests(unittest.TestCase):
    def test_valid_bundle_builds_and_keeps_execution_separate_from_science(self):
        report = gate.build_report(evidence(), now=NOW)
        self.assertEqual(report["claim_counts"]["VERIFIED"], 9)
        workflow_text = next(row["statement"] for row in report["claims"] if row["claim_id"] == "workflow")
        gate_text = next(row["statement"] for row in report["claims"] if row["claim_id"] == "gate")
        job_text = next(row["statement"] for row in report["claims"] if row["claim_id"] == "job")
        self.assertIn("success", workflow_text)
        self.assertIn("FAIL", gate_text)
        self.assertIn("skipped", job_text)
        self.assertFalse(report["automatic_authorization_granted"])

    def test_head_mismatch_fails_closed(self):
        bundle = evidence()
        bundle["workflow"]["head_sha"] = "d" * 40
        with self.assertRaises(gate.GateError):
            gate.build_report(bundle, now=NOW)

    def test_artifact_head_mismatch_fails_closed(self):
        bundle = evidence()
        bundle["artifacts"][0]["head_sha"] = "d" * 40
        with self.assertRaises(gate.GateError):
            gate.build_report(bundle, now=NOW)

    def test_artifact_digest_mismatch_fails_closed(self):
        bundle = evidence()
        bundle["research"]["source_artifact_sha256"] = "d" * 64
        with self.assertRaises(gate.GateError):
            gate.build_report(bundle, now=NOW)

    def test_stale_dynamic_status_fails_closed(self):
        with self.assertRaises(gate.GateError):
            gate.build_report(evidence(), now=datetime(2026, 8, 13, tzinfo=timezone.utc))

    def test_workflow_success_does_not_allow_unfinished_scientific_pass(self):
        bundle = evidence()
        bundle["research"]["evaluation_status"] = "RUNNING"
        bundle["research"]["scientific_gate"] = "PASS"
        bundle["research"]["metrics"] = []
        with self.assertRaises(gate.GateError):
            gate.build_report(bundle, now=NOW)

    def test_reported_text_cannot_be_verified(self):
        bundle = evidence()
        bundle["claims"][-3]["classification"] = "VERIFIED"
        with self.assertRaises(gate.GateError):
            gate.build_report(bundle, now=NOW)

    def test_inference_cannot_use_certainty_language(self):
        bundle = evidence()
        bundle["claims"][-2]["text"] = "平局问题已解决。"
        with self.assertRaises(gate.GateError):
            gate.build_report(bundle, now=NOW)

    def test_holdout_access_without_authorization_fails_closed(self):
        bundle = evidence()
        bundle["research"]["holdout"]["access_authorized"] = False
        with self.assertRaises(gate.GateError):
            gate.build_report(bundle, now=NOW)

    def test_unknown_must_stay_unknown(self):
        bundle = evidence()
        bundle["claims"][-1]["classification"] = "VERIFIED"
        with self.assertRaises(gate.GateError):
            gate.build_report(bundle, now=NOW)

    def test_deterministic_round_trip_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)
            gate.write_outputs(evidence(), out, now=NOW)
            self.assertEqual(gate.verify_outputs(evidence(), out, now=NOW)["status"], "PASS")
            report = json.loads((out / "gpt_fact_report.json").read_text(encoding="utf-8"))
            report["cold_conclusion"] = "模型已经正式通过。"
            (out / "gpt_fact_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            with self.assertRaises(gate.GateError):
                gate.verify_outputs(evidence(), out, now=NOW)

    def test_markdown_tamper_detection(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)
            gate.write_outputs(evidence(), out, now=NOW)
            with (out / "gpt_fact_report.md").open("a", encoding="utf-8") as handle:
                handle.write("\n虚假结论\n")
            with self.assertRaises(gate.GateError):
                gate.verify_outputs(evidence(), out, now=NOW)

    def test_extra_artifact_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)
            gate.write_outputs(evidence(), out, now=NOW)
            (out / "extra.txt").write_text("unexpected", encoding="utf-8")
            with self.assertRaises(gate.GateError):
                gate.verify_outputs(evidence(), out, now=NOW)

    def test_empty_jobs_and_artifacts_are_valid_for_queued_identity_only_report(self):
        bundle = evidence()
        bundle["workflow"]["status"] = "queued"
        bundle["workflow"]["conclusion"] = None
        bundle["jobs"] = []
        bundle["artifacts"] = []
        bundle["research"] = {
            "evaluation_status": "UNPROVEN",
            "scientific_gate": "UNPROVEN",
            "metrics": [],
            "holdout": {"status": "UNPROVEN", "labels_accessed_count": "UNKNOWN", "access_authorized": "UNKNOWN", "exact_first_access_utc": "UNPROVEN_NOT_MEASURED"},
            "provenance": "REPORTED_TEXT",
        }
        bundle["formal_boundary"] = {
            "formal_weight": "UNKNOWN", "model_diff": "UNKNOWN", "formal_data_diff": "UNKNOWN",
            "config_diff": "UNKNOWN", "current_diff": "UNKNOWN", "provenance": "REPORTED_TEXT",
        }
        bundle["claims"] = [
            {"claim_id": "head", "claim_type": "EXACT_HEAD", "classification": "VERIFIED", "evidence_refs": ["repository", "pull_request"]},
            {"claim_id": "workflow", "claim_type": "WORKFLOW_EXECUTION", "classification": "VERIFIED", "evidence_refs": ["workflow"]},
            {"claim_id": "science", "claim_type": "UNKNOWN_FACT", "classification": "UNKNOWN", "question": "研究是否已执行"},
        ]
        report = gate.build_report(bundle, now=NOW)
        self.assertIn("queued", next(row["statement"] for row in report["claims"] if row["claim_id"] == "workflow"))


if __name__ == "__main__":
    unittest.main()
