from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "audit" / "draw_signal_claim_contract_v503.py"
spec = importlib.util.spec_from_file_location("draw_signal_claim_contract_v503", MODULE_PATH)
contract = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(contract)


def base_record(**overrides):
    row = contract.claim_record(
        "c1", "GENERIC", "subject", "text", "UNPROVEN", "REPOSITORY", ["repo"],
        "a" * 40, ["artifact.json"], execution_status="NOT_EXECUTED",
        pit_status="UNPROVEN", holdout_status="NOT_YET_PROVEN_UNTOUCHED",
        authorization_status="NOT_AUTHORIZED", limitations=["missing evidence"],
    )
    row.update(overrides)
    return row


def sample_audit(head: str = "a" * 40):
    return {
        "head": head,
        "audit_sha256": "b" * 64,
        "decision": "PRE_REGISTRATION_REQUIRED_NO_TRAINING_YET",
        "preregistration": {"holdout_status": "NOT_YET_PROVEN_UNTOUCHED"},
        "research_asset_coverage": {
            "expected_path_count": 1648, "actual_path_count": 1648, "missing": [], "extra": []
        },
        "experiment_route_closure": {
            "unresolved": list(range(14)), "missing_result_evidence": list(range(4))
        },
        "feature_difference": {
            "DOMAIN_SPECIFIC_RECONSTRUCTED_RESEARCH_CANDIDATES": [{
                "field": "round",
                "eligible_domain_specific_scopes": [{"competition": "KOR_KLeague1"}],
            }]
        },
        "execution_boundary": {
            "measurement_scope": "AUDIT_BUSINESS_CODE_ONLY",
            "actual_steps": ["audit-object-built"],
            "not_yet_executed_at_measurement": ["workflow-artifact-upload"],
            "provider_request_attempts": 0,
            "audit_code_external_request_attempts": 0,
            "workflow_infrastructure_network_used": "OUT_OF_SCOPE_NOT_MEASURED_BY_AUDIT_CODE",
        },
    }


class ClaimContractV503Tests(unittest.TestCase):
    def _fixture(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        rule = root / contract.RULE_PATH
        rule.parent.mkdir(parents=True)
        rule.write_text(f"# {contract.RULE_VERSION}\nCURRENT_AUTHORITATIVE_RULE\n", encoding="utf-8")
        (root / "AGENTS.md").write_text(f"Read `{contract.RULE_PATH}`.\n", encoding="utf-8")
        closure = root / "closure"
        closure.mkdir()
        audit = sample_audit()
        (closure / "closure_audit.json").write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")
        contract.write_claim_outputs(closure, audit, root)
        return temporary, root, closure

    def test_rule_and_agents_exact_path(self):
        temporary, root, _ = self._fixture()
        try:
            self.assertTrue(contract.validate_rule_sources(root)["valid"])
        finally:
            temporary.cleanup()

    def test_evidence_status_enum_is_closed(self):
        with self.assertRaises(ValueError):
            contract.validate_claim(base_record(evidence_status="CERTAIN"))

    def test_workflow_success_with_skipped_live_job_does_not_prove_production(self):
        row = base_record(claim_type="PRODUCTION_PATH_VALIDATED", evidence_status="PROVEN", evidence_metadata={
            "evidence_generation_method": "workflow", "time_boundary": "head", "production_path_executed": False,
            "skipped_paths": ["live"], "mock_used": False,
        })
        with self.assertRaises(ValueError):
            contract.validate_claim(row, {"workflow_conclusion": "success", "live_job_conclusion": "skipped", "production_path_executed": False})

    def test_coverage_100_and_preregistered_not_run_does_not_prove_signal(self):
        row = base_record(claim_type="SIGNAL_EFFECTIVE", evidence_status="PROVEN", evidence_metadata={
            "evidence_generation_method": "coverage", "time_boundary": "head", "production_path_executed": True,
            "skipped_paths": [], "mock_used": False,
        })
        with self.assertRaises(ValueError):
            contract.validate_claim(row, {"coverage": 1.0, "experiment_status": "PRE_REGISTERED_NOT_RUN", "preregistered_experiment_executed": False, "frozen_gates_passed": False, "independent_result_evidence": False})

    def test_no_training_authorization_is_not_authorized(self):
        with self.assertRaises(ValueError):
            contract.validate_claim(base_record(claim_type="ACTION_AUTHORIZATION", evidence_status="UNPROVEN", action="MODEL_TRAINING"), {"user_authorized": False})
        allowed = base_record(claim_type="ACTION_AUTHORIZATION", evidence_status="NOT_AUTHORIZED", action="MODEL_TRAINING", authorization_status="NOT_AUTHORIZED")
        self.assertTrue(contract.validate_claim(allowed, {"user_authorized": False})["valid"])

    def test_unresolved_routes_prohibit_exhausted(self):
        row = base_record(claim_type="EXHAUSTED", evidence_status="PROVEN", evidence_metadata={
            "evidence_generation_method": "audit", "time_boundary": "head", "production_path_executed": True,
            "skipped_paths": [], "mock_used": False,
        })
        context = {
            "asset_universes_independent_complete": True, "all_candidate_fields_def_use_reviewed": True,
            "pit_available_at_proven": True, "all_canonical_routes_final": False, "unresolved_routes": 14,
            "missing_result_evidence": 4, "global_candidates": 0, "strict_domain_candidates": 0,
            "reconstructed_domain_candidates": 1, "all_registered_experiments_have_results": False,
            "unverified_aliases": 0, "omitted_tracked_assets": 0, "required_production_paths_executed": True,
        }
        with self.assertRaises(ValueError):
            contract.validate_claim(row, context)

    def test_empty_dataflow_contracts_prohibit_all_fields_tested(self):
        row = base_record(claim_type="ALL_FIELDS_TESTED", evidence_status="PROVEN", evidence_metadata={
            "evidence_generation_method": "audit", "time_boundary": "head", "production_path_executed": True,
            "skipped_paths": [], "mock_used": False,
        })
        with self.assertRaises(ValueError):
            contract.validate_claim(row, {"field_dataflow_contracts_nonempty": False})

    def test_mock_network_cannot_prove_real_network(self):
        row = base_record(claim_type="REAL_NETWORK_VALIDATED", evidence_status="PROVEN", evidence_metadata={
            "evidence_generation_method": "mock test", "time_boundary": "head", "production_path_executed": False,
            "skipped_paths": ["real network"], "mock_used": True,
        })
        with self.assertRaises(ValueError):
            contract.validate_claim(row, {"network_mocked": True, "real_request_attempts": 0})

    def test_artifact_exists_does_not_prove_business_conclusion(self):
        row = base_record(claim_type="BUSINESS_CONCLUSION_VALIDATED", evidence_status="PROVEN", evidence_metadata={
            "evidence_generation_method": "artifact upload", "time_boundary": "head", "production_path_executed": True,
            "skipped_paths": [], "mock_used": False,
        })
        with self.assertRaises(ValueError):
            contract.validate_claim(row, {"artifact_uploaded": True, "business_evidence_independent": False})

    def test_unproved_pit_prohibits_training_holdout_and_formal_use(self):
        for claim_type in ("TRAINING_AVAILABLE", "FORMALLY_USABLE", "HOLDOUT_RELEASE_AVAILABLE"):
            row = base_record(claim_type=claim_type, evidence_status="PROVEN", evidence_metadata={
                "evidence_generation_method": "audit", "time_boundary": "head", "production_path_executed": True,
                "skipped_paths": [], "mock_used": False,
            })
            with self.assertRaises(ValueError, msg=claim_type):
                contract.validate_claim(row, {"pit_status": "UNPROVEN"})

    def test_historical_withdrawal_requires_all_fields(self):
        row = base_record(claim_type="HISTORICAL_CLAIM_WITHDRAWAL", evidence_status="PROVEN", evidence_metadata={
            "evidence_generation_method": "governance", "time_boundary": "head", "production_path_executed": True,
            "skipped_paths": [], "mock_used": False,
        })
        with self.assertRaises(ValueError):
            contract.validate_claim(row)
        row.update({
            "old_claim": "OLD", "withdrawal_statement": "撤销 OLD", "withdrawal_reason": "new evidence",
            "replacement_claim": "NEW", "replacement_evidence_status": "COMPUTED",
        })
        self.assertTrue(contract.validate_claim(row)["valid"])

    def test_kor_scope_cannot_be_global(self):
        with self.assertRaises(ValueError):
            contract.validate_claim(base_record(scope={"level": "GLOBAL", "values": ["KOR_KLeague1"]}))

    def test_report_status_sections_are_pure(self):
        records = [
            base_record(claim_id="p", evidence_status="PROVEN", evidence_metadata={
                "evidence_generation_method": "direct", "time_boundary": "head", "production_path_executed": True,
                "skipped_paths": [], "mock_used": False,
            }),
            base_record(claim_id="c", evidence_status="COMPUTED", evidence_metadata={
                "input_identity": "x", "input_scope": "repo", "algorithm_identity": "a", "algorithm_binding": "head",
                "output_identity": "y", "reproducible": True, "business_validity_not_implied": True,
            }),
            base_record(claim_id="i", evidence_status="INFERRED", inference_qualifier="候选"),
            base_record(claim_id="u", evidence_status="UNPROVEN"),
            base_record(claim_id="n", evidence_status="NOT_AUTHORIZED", authorization_status="NOT_AUTHORIZED"),
        ]
        report = contract.build_report(records, {"head": "a" * 40, "execution_boundary": {}})
        contract._validate_report_section_purity(report)

    def test_tampered_cold_conclusion_fails_existing_verification(self):
        temporary, root, closure = self._fixture()
        try:
            report_path = closure / "claim_report.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["cold_conclusion"] = "平局信号已经正式有效，可以立即训练、修改正式权重并合并PR。"
            report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ValueError):
                contract.verify_existing(closure, root)
        finally:
            temporary.cleanup()

    def test_tampered_inferred_claim_text_and_stale_sha_fails(self):
        temporary, root, closure = self._fixture()
        try:
            path = closure / "claim_records.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            row = next(item for item in payload["records"] if item["claim_id"] == "round-candidate")
            row["claim_text"] = "round已经确认是有效平局信号，可以立即训练并进入正式模型。"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ValueError):
                contract.verify_existing(closure, root)
        finally:
            temporary.cleanup()

    def test_record_moved_to_wrong_report_section_fails(self):
        temporary, root, closure = self._fixture()
        try:
            path = closure / "claim_report.json"
            report = json.loads(path.read_text(encoding="utf-8"))
            report["directly_proven"].append(report["inferences_and_candidates"].pop(0))
            path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ValueError):
                contract.verify_existing(closure, root)
        finally:
            temporary.cleanup()

    def test_tampered_records_sha_fails(self):
        temporary, root, closure = self._fixture()
        try:
            path = closure / "claim_records.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["records_sha256"] = "0" * 64
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ValueError):
                contract.verify_existing(closure, root)
        finally:
            temporary.cleanup()

    def test_tampered_markdown_fails(self):
        temporary, root, closure = self._fixture()
        try:
            path = closure / "claim_report.md"
            path.write_text(path.read_text(encoding="utf-8") + "\n扩大结论", encoding="utf-8")
            with self.assertRaises(ValueError):
                contract.verify_existing(closure, root)
        finally:
            temporary.cleanup()

    def test_clean_outputs_verify_and_bind_all_hashes(self):
        temporary, root, closure = self._fixture()
        try:
            result = contract.verify_existing(closure, root)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(
                set(result["bindings"]),
                {
                    "audit_head", "audit_sha256", "claim_records_sha256",
                    "claim_records_object_sha256", "claim_report_json_sha256",
                    "claim_report_markdown_sha256", "deterministic_renderer",
                },
            )
        finally:
            temporary.cleanup()

    def test_utf8_git_output_decodes_windows_chinese_path(self):
        expected = r"F:\足球项目维护\repo-pr77-aa03-audit"
        self.assertEqual(contract.decode_git_stdout((expected + "\r\n").encode("utf-8")), expected)


if __name__ == "__main__":
    unittest.main()
