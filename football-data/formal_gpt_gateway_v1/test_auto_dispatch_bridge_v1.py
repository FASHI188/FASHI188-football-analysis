#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

FOOTBALL3_GOVERNED_PRODUCTION_TRANSPORT = "football3-formal-gpt-request-transport-v1"

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
REPO = HERE.parents[1]

import auto_dispatch_bridge_v1 as bridge
import request_contract_v1 as contract
import request_sha_binding_v1 as binding

RECEIVER_WORKFLOW = REPO / ".github/workflows/football3-gpt-auto-dispatch-bridge-v1.yml"
FORMAL_WORKFLOW = REPO / ".github/workflows/football3-formal-gpt-runner-integration-v1.yml"


class BridgeContractSecurityTest(unittest.TestCase):
    def req(self, rid: str = "r1") -> dict:
        return {
            "schema_version": contract.SCHEMA,
            "mode": "predict",
            "request_id": rid,
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
        return bridge.BEGIN_MARKER + "\n" + json.dumps(request, separators=(",", ":")) + "\n" + bridge.END_MARKER

    def assert_contract_error(self, request: dict, code: str) -> None:
        with self.assertRaisesRegex(bridge.BridgeError, code):
            bridge.parse_request_body(self.body(request))

    def test_real_nested_match_and_all_formal_modes(self) -> None:
        for mode in ("predict", "PROSPECTIVE_FORMAL_PREDICTION", "ACTIVE_AT_CUTOFF_REPLAY", "CURRENT_MODEL_RETROSPECTIVE_REPLAY"):
            request = self.req(mode); request["mode"] = mode
            parsed = bridge.parse_request_body(self.body(request))
            self.assertEqual(parsed["match"]["competition_id"], "ITA_SerieA")
            self.assertEqual(contract.execution_request(parsed)["mode"], "predict")

    def test_missing_or_illegal_mode(self) -> None:
        request = self.req(); request.pop("mode")
        self.assert_contract_error(request, "FORMAL_REQUEST_TOP_LEVEL_REQUIRED_FIELD_MISSING")
        request = self.req(); request["mode"] = "BAD"
        self.assert_contract_error(request, "FORMAL_REQUEST_MODE_INVALID")

    def test_missing_match(self) -> None:
        request = self.req(); request.pop("match")
        self.assert_contract_error(request, "FORMAL_REQUEST_MATCH_INVALID")

    def test_invalid_competition_id(self) -> None:
        request = self.req(); request["match"]["competition_id"] = "NOPE"
        self.assert_contract_error(request, "FORMAL_REQUEST_COMPETITION_ID_INVALID")

    def test_identical_teams(self) -> None:
        request = self.req(); request["match"]["away_team_name"] = "Udinese FC"
        self.assert_contract_error(request, "FORMAL_REQUEST_TEAMS_IDENTICAL")

    def test_invalid_kickoff_and_cutoff(self) -> None:
        request = self.req(); request["match"]["kickoff"] = "2026-09-07T18:45:00"
        self.assert_contract_error(request, "FORMAL_REQUEST_KICKOFF_INVALID")
        request = self.req(); request["match"]["cutoff"] = "bad"
        self.assert_contract_error(request, "FORMAL_REQUEST_CUTOFF_INVALID")

    def test_cutoff_must_precede_kickoff(self) -> None:
        request = self.req(); request["match"]["cutoff"] = request["match"]["kickoff"]
        self.assert_contract_error(request, "FORMAL_REQUEST_CUTOFF_NOT_BEFORE_KICKOFF")

    def test_same_request_id_different_match_is_rejected(self) -> None:
        first = bridge.parse_request_body(self.body(self.req("same")))
        second = self.req("same"); second["match"]["away_team_name"] = "Inter"; second = bridge.parse_request_body(self.body(second))
        first_sha = contract.request_sha256(first); second_sha = contract.request_sha256(second)
        self.assertNotEqual(first_sha, second_sha)
        reservation, _ = bridge.ledger_names("same", first_sha)
        with self.assertRaisesRegex(bridge.BridgeError, "AUTO_DISPATCH_REQUEST_ID_CONTENT_MISMATCH"):
            bridge.classify_ledgers("same", second_sha, [{"id": 1, "name": reservation, "expired": False}])

    def test_prepare_after_body_change_is_rejected(self) -> None:
        first = bridge.parse_request_body(self.body(self.req("race")))
        audit = {"request_id": "race", "request_sha256": contract.request_sha256(first)}
        bridge.assert_request_unchanged(audit, first)
        changed = json.loads(json.dumps(first)); changed["match"]["kickoff"] = "2026-09-07T19:45:00+00:00"
        with self.assertRaisesRegex(bridge.BridgeError, "AUTO_DISPATCH_REQUEST_CHANGED_AFTER_RESERVATION"):
            bridge.assert_request_unchanged(audit, changed)

    def test_dispatch_input_binds_formal_runner_to_exact_request_sha(self) -> None:
        request = bridge.parse_request_body(self.body(self.req("payload"))); request_sha = contract.request_sha256(request)
        payload = bridge.build_dispatch_payload(request_sha)
        self.assertEqual(payload["inputs"], {"request_pr_number": "341", "expected_request_sha256": request_sha})
        self.assertEqual(payload["ref"], bridge.CANONICAL_REF)

    def test_carrier_changed_files_exactly_one(self) -> None:
        bridge.validate_carrier_files([bridge.CARRIER_FILE])
        for files in ([bridge.CARRIER_FILE, "README.md"], [bridge.CARRIER_FILE, ".github/workflows/evil.yml"], [bridge.CARRIER_FILE, "scripts/evil.py"]):
            with self.assertRaisesRegex(bridge.BridgeError, "AUTO_DISPATCH_CARRIER_CHANGED_FILES_UNAUTHORIZED"):
                bridge.validate_carrier_files(files)

    def test_duplicate_request_and_reservation_conflict(self) -> None:
        request = bridge.parse_request_body(self.body(self.req("ledger"))); request_sha = contract.request_sha256(request)
        reservation, completed = bridge.ledger_names("ledger", request_sha)
        self.assertEqual(bridge.classify_ledgers("ledger", request_sha, [{"id": 1, "name": completed}])[0], "DUPLICATE_COMPLETED")
        self.assertEqual(bridge.classify_ledgers("ledger", request_sha, [{"id": 2, "name": reservation}])[0], "RESERVATION_CONFLICT")

    def test_canonical_head_change_after_reservation_fails_closed(self) -> None:
        bridge.assert_canonical_unchanged({"canonical_execution_sha": "1" * 40}, "1" * 40)
        with self.assertRaisesRegex(bridge.BridgeError, "AUTO_DISPATCH_CANONICAL_MOVED_AFTER_RESERVATION"):
            bridge.assert_canonical_unchanged({"canonical_execution_sha": "1" * 40}, "2" * 40)

    def test_unauthorized_actor_is_rejected(self) -> None:
        for permission in ("read", "triage", None):
            with self.assertRaisesRegex(bridge.BridgeError, "AUTO_DISPATCH_ACTOR_PERMISSION_DENIED"):
                bridge.validate_permission(permission)

    def test_new_formal_run_is_mechanically_located(self) -> None:
        request_sha = "a" * 64; head = "b" * 40; title = f"{bridge.FORMAL_RUN_PREFIX} {request_sha}"
        runs = [{"id": 1, "event": "workflow_dispatch", "head_branch": bridge.CANONICAL_REF, "head_sha": head, "display_title": title}, {"id": 2, "event": "workflow_dispatch", "head_branch": bridge.CANONICAL_REF, "head_sha": head, "display_title": title}]
        self.assertEqual(bridge.select_new_formal_run({1}, runs, request_sha, head)["id"], 2)

    def test_receiver_is_read_only_and_never_checks_out_carrier_code(self) -> None:
        text = RECEIVER_WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("pull_request_target:", text); self.assertNotIn("actions: write", text)
        receiver = text.split("  carrier-edit-receiver:", 1)[1].split("  candidate-contract-security:", 1)[0]
        self.assertNotIn("actions/checkout", receiver)
        self.assertIn("github.event.pull_request.number == 341", receiver)
        self.assertIn("github.event.action == 'edited'", receiver)
        self.assertIn("AUTO_DISPATCH_RECEIVER_SIGNAL=READY", receiver)

    def test_formal_runner_sha_contract_remains_fail_closed(self) -> None:
        text = FORMAL_WORKFLOW.read_text(encoding="utf-8")
        for token in ("expected_request_sha256:", "request_contract_v1", "PRODUCTION_EXPECTED_REQUEST_SHA_MISSING", "PRODUCTION_REQUEST_SHA_MISMATCH", "PRODUCTION_REQUEST_CARRIER_CHANGED_FILES_UNAUTHORIZED", "run-name:"):
            self.assertIn(token, text)

    def test_binding_receipt_and_full_sha_verify(self) -> None:
        request = bridge.parse_request_body(self.body(self.req("bind"))); request_sha = contract.request_sha256(request)
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "request.json").write_bytes(contract.canonical_bytes(request))
            (root / "transport.json").write_text(json.dumps({"expected_request_sha256": request_sha, "request_sha256": request_sha, "request_sha_verified": True, "pr_number": "341"}), encoding="utf-8")
            (root / "binding.json").write_text(json.dumps({"request_sha256": request_sha, "request_id": "bind", "request_carrier_ref": bridge.CARRIER_REF, "request_carrier_head": "1" * 40, "checkout_head_sha": "2" * 40, "canonical_base_ref": bridge.CANONICAL_REF, "runner_code_source": "CANONICAL_INTEGRATION_EXACT_SHA"}), encoding="utf-8")
            receipt = binding.verify(str(root / "request.json"), str(root / "transport.json"), str(root / "binding.json"))
            self.assertEqual(receipt["carrier_pr_number"], "341"); self.assertEqual(receipt["carrier_head_sha"], "1" * 40); self.assertEqual(receipt["canonical_execution_sha"], "2" * 40)
            changed = json.loads((root / "request.json").read_text(encoding="utf-8")); changed["match"]["away_team_name"] = "Inter"; (root / "request.json").write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(binding.RequestBindingError, "PRODUCTION_REQUEST_SHA_MISMATCH"):
                binding.verify(str(root / "request.json"), str(root / "transport.json"), str(root / "binding.json"))

    def test_final_orchestration_receipt_contract(self) -> None:
        source = (HERE / "auto_dispatch_bridge_v1.py").read_text(encoding="utf-8")
        for key in ('"request_id"', '"request_sha256"', '"carrier_pr_number"', '"carrier_head_sha"', '"trusted_dispatcher_sha"', '"canonical_integration_execution_sha"', '"resulting_production_run_id"', '"formal_receipt_artifact_id"', '"prediction_sha"'):
            self.assertIn(key, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
