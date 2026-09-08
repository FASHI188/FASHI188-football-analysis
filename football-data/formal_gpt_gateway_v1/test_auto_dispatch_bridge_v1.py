#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

BRIDGE_PATH = HERE / "auto_dispatch_bridge_v1.py"
REPO_ROOT = HERE.parents[1]
BRIDGE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "football3-gpt-auto-dispatch-bridge-v1.yml"
FORMAL_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "football3-formal-gpt-runner-integration-v1.yml"
BINDING_HELPER = HERE / "request_sha_binding_v1.py"

spec = importlib.util.spec_from_file_location("auto_dispatch_bridge_v1", BRIDGE_PATH)
bridge = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(bridge)
contract = bridge.request_contract


class BridgeContractSecurityTest(unittest.TestCase):
    def request(self, request_id: str = "prospective-formal-prediction-ita-udinese-lazio-test") -> dict:
        return {
            "schema_version": contract.SCHEMA,
            "mode": "predict",
            "request_id": request_id,
            "match": {
                "competition_id": "ITA_SerieA",
                "season": "2026/27",
                "kickoff": "2026-09-07T18:45:00+00:00",
                "cutoff": "2026-09-07T17:45:00+00:00",
                "home_team_name": "Udinese",
                "away_team_name": "Lazio",
            },
        }

    def body(self, request: dict) -> str:
        return "Permanent Draft PR request carrier\n\n" + bridge.BEGIN_MARKER + "\n" + json.dumps(request, separators=(",", ":")) + "\n" + bridge.END_MARKER + "\n"

    def assert_contract_error(self, request: dict, code: str) -> None:
        with self.assertRaisesRegex(bridge.BridgeError, code):
            bridge.parse_request_body(self.body(request))

    def test_real_pr341_nested_match_schema_and_supported_formal_modes(self) -> None:
        request = self.request()
        parsed = bridge.parse_request_body(self.body(request))
        self.assertEqual(parsed["match"]["competition_id"], "ITA_SerieA")
        self.assertEqual(parsed["match"]["home_team_name"], "Udinese")
        self.assertEqual(parsed["match"]["away_team_name"], "Lazio")
        self.assertEqual(parsed["match"]["kickoff"], "2026-09-07T18:45:00+00:00")
        self.assertEqual(parsed["match"]["cutoff"], "2026-09-07T17:45:00+00:00")
        for mode in ("predict", "PROSPECTIVE_FORMAL_PREDICTION", "ACTIVE_AT_CUTOFF_REPLAY", "CURRENT_MODEL_RETROSPECTIVE_REPLAY"):
            request = self.request(f"request-{mode}")
            request["mode"] = mode
            parsed = bridge.parse_request_body(self.body(request))
            self.assertEqual(parsed["mode"], mode)
            self.assertEqual(contract.execution_request(parsed)["mode"], "predict")

    def test_missing_or_illegal_mode_fails_closed(self) -> None:
        missing = self.request(); missing.pop("mode")
        self.assert_contract_error(missing, "FORMAL_REQUEST_TOP_LEVEL_REQUIRED_FIELD_MISSING")
        bad = self.request(); bad["mode"] = "RETROSPECTIVE_BUT_UNGOVERNED"
        self.assert_contract_error(bad, "FORMAL_REQUEST_MODE_INVALID")

    def test_missing_match_fails_closed(self) -> None:
        request = self.request(); request.pop("match")
        self.assert_contract_error(request, "FORMAL_REQUEST_MATCH_INVALID")

    def test_invalid_competition_id_fails_closed_against_runtime_scope(self) -> None:
        request = self.request(); request["match"]["competition_id"] = "NOT_A_FORMAL_COMPETITION"
        self.assert_contract_error(request, "FORMAL_REQUEST_COMPETITION_ID_INVALID")
        self.assertIn("ITA_SerieA", contract.supported_competitions())

    def test_identical_home_and_away_fail_closed_after_identity_normalization(self) -> None:
        request = self.request(); request["match"]["away_team_name"] = "Udinese"
        self.assert_contract_error(request, "FORMAL_REQUEST_TEAMS_IDENTICAL")

    def test_invalid_kickoff_and_cutoff_fail_closed(self) -> None:
        kickoff = self.request(); kickoff["match"]["kickoff"] = "2026-09-07T18:45:00"
        self.assert_contract_error(kickoff, "FORMAL_REQUEST_KICKOFF_INVALID")
        cutoff = self.request(); cutoff["match"]["cutoff"] = "not-a-datetime"
        self.assert_contract_error(cutoff, "FORMAL_REQUEST_CUTOFF_INVALID")

    def test_cutoff_must_be_strictly_before_kickoff(self) -> None:
        for cutoff in ("2026-09-07T18:45:00+00:00", "2026-09-07T19:45:00+00:00"):
            request = self.request(); request["match"]["cutoff"] = cutoff
            self.assert_contract_error(request, "FORMAL_REQUEST_CUTOFF_NOT_BEFORE_KICKOFF")

    def test_same_request_id_with_different_match_content_is_rejected(self) -> None:
        first = bridge.parse_request_body(self.body(self.request("same-id")))
        changed_request = self.request("same-id"); changed_request["match"]["away_team_name"] = "Inter"
        changed = bridge.parse_request_body(self.body(changed_request))
        first_sha = contract.request_sha256(first); changed_sha = contract.request_sha256(changed)
        self.assertNotEqual(first_sha, changed_sha)
        reservation, _ = bridge.ledger_names("same-id", first_sha)
        with self.assertRaisesRegex(bridge.BridgeError, "AUTO_DISPATCH_REQUEST_ID_CONTENT_MISMATCH"):
            bridge.classify_ledgers("same-id", changed_sha, [{"id": 12, "name": reservation, "expired": False}])

    def test_prepare_sha_binds_entire_request_and_body_change_is_rejected(self) -> None:
        request = bridge.parse_request_body(self.body(self.request("race-id")))
        audit = {"request_id": "race-id", "request_sha256": contract.request_sha256(request)}
        self.assertEqual(bridge.assert_request_unchanged(audit, request), audit["request_sha256"])
        changed = json.loads(json.dumps(request)); changed["match"]["kickoff"] = "2026-09-07T19:45:00+00:00"
        with self.assertRaisesRegex(bridge.BridgeError, "AUTO_DISPATCH_REQUEST_CHANGED_AFTER_RESERVATION"):
            bridge.assert_request_unchanged(audit, changed)

    def test_dispatch_payload_carries_expected_full_request_sha(self) -> None:
        request = bridge.parse_request_body(self.body(self.request("payload-id")))
        request_sha = contract.request_sha256(request)
        payload = bridge.build_dispatch_payload(request_sha)
        self.assertEqual(payload["ref"], bridge.CANONICAL_REF)
        self.assertEqual(payload["inputs"]["request_pr_number"], "341")
        self.assertEqual(payload["inputs"]["expected_request_sha256"], request_sha)
        self.assertNotIn(bridge.CARRIER_REF, json.dumps(payload, sort_keys=True))

    def test_carrier_changed_files_must_be_exact_single_document(self) -> None:
        bridge.validate_carrier_files([bridge.CARRIER_FILE])
        for files in ([bridge.CARRIER_FILE, "README.md"], [bridge.CARRIER_FILE, ".github/workflows/evil.yml"], [bridge.CARRIER_FILE, "scripts/evil.py"], [".github/workflows/evil.yml"]):
            with self.assertRaisesRegex(bridge.BridgeError, "AUTO_DISPATCH_CARRIER_CHANGED_FILES_UNAUTHORIZED"):
                bridge.validate_carrier_files(files)

    def test_duplicate_request_is_idempotent_and_reservation_conflict_fails_closed(self) -> None:
        request = bridge.parse_request_body(self.body(self.request("ledger-id")))
        request_sha = contract.request_sha256(request)
        reservation, completed = bridge.ledger_names("ledger-id", request_sha)
        status, ids = bridge.classify_ledgers("ledger-id", request_sha, [{"id": 91, "name": completed, "expired": False}])
        self.assertEqual((status, ids), ("DUPLICATE_COMPLETED", [91]))
        status, ids = bridge.classify_ledgers("ledger-id", request_sha, [{"id": 92, "name": reservation, "expired": False}])
        self.assertEqual((status, ids), ("RESERVATION_CONFLICT", [92]))

    def test_canonical_head_move_after_reservation_fails_closed(self) -> None:
        audit = {"canonical_execution_sha": "1" * 40}
        bridge.assert_canonical_unchanged(audit, "1" * 40)
        with self.assertRaisesRegex(bridge.BridgeError, "AUTO_DISPATCH_CANONICAL_MOVED_AFTER_RESERVATION"):
            bridge.assert_canonical_unchanged(audit, "2" * 40)

    def test_unauthorized_actor_permission_is_rejected(self) -> None:
        for permission in ("read", "triage", None):
            with self.assertRaisesRegex(bridge.BridgeError, "AUTO_DISPATCH_ACTOR_PERMISSION_DENIED"):
                bridge.validate_permission(permission)
        for permission in ("write", "maintain", "admin"):
            self.assertEqual(bridge.validate_permission(permission), permission)

    def test_dispatched_formal_run_is_mechanically_located_by_sha_and_new_run_id(self) -> None:
        request_sha = "a" * 64; canonical_sha = "b" * 40
        title = f"{bridge.FORMAL_RUN_PREFIX} {request_sha}"
        runs = [
            {"id": 100, "event": "workflow_dispatch", "head_branch": bridge.CANONICAL_REF, "head_sha": canonical_sha, "display_title": title},
            {"id": 101, "event": "workflow_dispatch", "head_branch": bridge.CANONICAL_REF, "head_sha": canonical_sha, "display_title": title, "html_url": "https://example.invalid/run/101"},
        ]
        found = bridge.select_new_formal_run({100}, runs, request_sha, canonical_sha)
        self.assertEqual(found["id"], 101)

    def test_candidate_workflow_is_unprivileged_and_never_executes_carrier_code(self) -> None:
        text = BRIDGE_WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("pull_request_target:", text)
        self.assertNotIn("actions: write", text)
        self.assertIn("github.event.pull_request.number != 341", text)
        self.assertIn("TRUSTED_AUTO_DISPATCH_TRIGGER_UNAVAILABLE", text)
        self.assertNotIn("github.event.pull_request.head.ref == 'football3/formal-gpt-runner-request-carrier-v1'", text)

    def test_formal_runner_transport_contract_requires_expected_request_sha(self) -> None:
        text = FORMAL_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("expected_request_sha256:", text)
        self.assertIn("request_contract_v1", text)
        self.assertIn("PRODUCTION_EXPECTED_REQUEST_SHA_MISSING", text)
        self.assertIn("PRODUCTION_REQUEST_SHA_MISMATCH", text)
        self.assertIn("run-name:", text)
        self.assertIn("PRODUCTION_REQUEST_CARRIER_CHANGED_FILES_UNAUTHORIZED", text)

    def test_binding_receipt_records_request_carrier_and_canonical_execution_identity(self) -> None:
        text = BINDING_HELPER.read_text(encoding="utf-8")
        self.assertIn("expected_request_sha256", text)
        self.assertIn("request_sha_verified", text)
        self.assertIn("carrier_head_sha", text)
        self.assertIn("canonical_execution_sha", text)

    def test_request_sha_binding_verifies_full_request_and_receipt_identity(self) -> None:
        import tempfile
        spec = importlib.util.spec_from_file_location("request_sha_binding_v1", BINDING_HELPER)
        binding_mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(binding_mod)
        request = bridge.parse_request_body(self.body(self.request("binding-id")))
        request_sha = contract.request_sha256(request)
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            request_path = root / "request.json"; transport_path = root / "transport.json"; binding_path = root / "binding.json"
            request_path.write_bytes(contract.canonical_bytes(request))
            transport_path.write_text(json.dumps({"expected_request_sha256": request_sha, "request_sha256": request_sha, "request_sha_verified": True}), encoding="utf-8")
            binding_path.write_text(json.dumps({"request_sha256": request_sha, "request_id": "binding-id", "request_carrier_ref": bridge.CARRIER_REF, "request_carrier_head": "1" * 40, "checkout_head_sha": "2" * 40, "canonical_base_ref": bridge.CANONICAL_REF, "runner_code_source": "CHECKED_OUT_LIVE_CANONICAL_BASE"}), encoding="utf-8")
            receipt = binding_mod.verify(str(request_path), str(transport_path), str(binding_path))
            self.assertEqual(receipt["request_sha256"], request_sha)
            self.assertEqual(receipt["carrier_head_sha"], "1" * 40)
            self.assertEqual(receipt["canonical_execution_sha"], "2" * 40)
            changed = json.loads(request_path.read_text(encoding="utf-8")); changed["match"]["away_team_name"] = "Inter"
            request_path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(binding_mod.RequestBindingError, "PRODUCTION_REQUEST_SHA_MISMATCH"):
                binding_mod.verify(str(request_path), str(transport_path), str(binding_path))


if __name__ == "__main__":
    unittest.main()
