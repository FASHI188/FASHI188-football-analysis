#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

FOOTBALL3_GOVERNED_PRODUCTION_TRANSPORT = "football3-formal-gpt-request-transport-v1"

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
REPO = HERE.parents[1]

import auto_dispatch_bridge_v1 as bridge
import request_contract_v1 as contract
import request_sha_binding_v1 as binding

RECEIVER_WORKFLOW = REPO / ".github/workflows/football3-gpt-auto-dispatch-bridge-v1.yml"
FORMAL_WORKFLOW = REPO / ".github/workflows/football3-formal-gpt-runner-integration-v1.yml"
GOVERNED_WORKFLOW = REPO / ".github/workflows/football3-governed-permanent-regression-v1.yml"


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

    def fake_api(self, request: dict):
        outer = self

        class FakeAPI:
            repo = "owner/repo"

            def live_pr(self):
                return {
                    "number": bridge.CARRIER_PR_NUMBER,
                    "state": "open",
                    "draft": True,
                    "merged_at": None,
                    "body": outer.body(request),
                    "base": {"ref": bridge.CANONICAL_REF, "sha": "1" * 40},
                    "head": {
                        "ref": bridge.CARRIER_REF,
                        "sha": "2" * 40,
                        "repo": {"full_name": self.repo},
                    },
                }

            def carrier_files(self):
                return [bridge.CARRIER_FILE]

            def actor_permission(self, actor):
                return "write"

            def live_canonical_sha(self):
                return "3" * 40

            def all_artifacts(self):
                return []

            def validate_ledger_artifact_sources(self, artifacts, request_id):
                return None

            def matching_formal_run_ids(self, request_sha, canonical_sha):
                return set()

            def dispatch_formal(self, request_sha):
                return None

            def locate_new_formal_run(self, before_ids, request_sha, canonical_sha):
                return {"id": 123456, "html_url": "https://example.invalid/run", "head_sha": canonical_sha}

        return FakeAPI()

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

    def test_trusted_mode_comes_only_from_validated_canonical_request(self) -> None:
        retrospective = self.req("trusted-retro")
        retrospective["mode"] = contract.CURRENT_V2_RETROSPECTIVE_REPLAY
        prospective = self.req("trusted-prospective")
        prospective["mode"] = "PROSPECTIVE_FORMAL_PREDICTION"
        retro = bridge.parse_request_body(self.body(retrospective))
        pro = bridge.parse_request_body(self.body(prospective))
        self.assertEqual(bridge.trusted_request_mode(retro), contract.CURRENT_V2_RETROSPECTIVE_REPLAY)
        self.assertEqual(bridge.trusted_request_mode(pro), "PROSPECTIVE_FORMAL_PREDICTION")
        source = (HERE / "auto_dispatch_bridge_v1.py").read_text(encoding="utf-8")
        self.assertNotIn('prediction.get("request_mode")', source)
        self.assertNotIn('prediction.get("mode")', source)
        self.assertNotIn('state.get("request_mode")', source)

    def test_audit_live_and_prepare_write_trusted_request_mode(self) -> None:
        for mode in (contract.CURRENT_V2_RETROSPECTIVE_REPLAY, "PROSPECTIVE_FORMAL_PREDICTION"):
            request = self.req(f"audit-{mode}")
            request["mode"] = mode
            fake = self.fake_api(request)
            with tempfile.TemporaryDirectory() as td, mock.patch.object(bridge, "GitHubAPI", return_value=fake):
                audit_path = pathlib.Path(td) / "audit-live.json"
                bridge.audit_live(SimpleNamespace(repo=fake.repo, token="token", actor="actor", audit_out=str(audit_path)))
                audit = json.loads(audit_path.read_text(encoding="utf-8"))
                self.assertEqual(audit["request_mode"], mode)
                self.assertEqual(audit["request_sha256"], contract.request_sha256(bridge.parse_request_body(self.body(request))))

                prepare_path = pathlib.Path(td) / "prepare.json"
                bridge.prepare(SimpleNamespace(
                    repo=fake.repo,
                    token="token",
                    actor="actor",
                    trusted_checkout_sha="3" * 40,
                    trusted_dispatcher_sha="4" * 40,
                    trusted_dispatcher_run_id="10",
                    receiver_run_id="11",
                    audit_out=str(prepare_path),
                ))
                prepared = json.loads(prepare_path.read_text(encoding="utf-8"))
                self.assertEqual(prepared["request_mode"], mode)
                self.assertEqual(prepared["request_sha256"], audit["request_sha256"])

    def test_dispatch_rejects_mode_change_even_when_audit_sha_matches_reread_request(self) -> None:
        request = self.req("mode-race")
        request["mode"] = "PROSPECTIVE_FORMAL_PREDICTION"
        parsed = bridge.parse_request_body(self.body(request))
        audit = {
            "request_id": parsed["request_id"],
            "request_sha256": contract.request_sha256(parsed),
            "request_mode": contract.CURRENT_V2_RETROSPECTIVE_REPLAY,
        }
        with self.assertRaisesRegex(bridge.BridgeError, "AUTO_DISPATCH_REQUEST_MODE_CHANGED_AFTER_RESERVATION"):
            bridge.assert_request_unchanged(audit, parsed)

    def test_dispatch_preserves_prepared_trusted_mode(self) -> None:
        request = self.req("dispatch-mode")
        request["mode"] = contract.CURRENT_V2_RETROSPECTIVE_REPLAY
        parsed = bridge.parse_request_body(self.body(request))
        fake = self.fake_api(request)
        with tempfile.TemporaryDirectory() as td, mock.patch.object(bridge, "GitHubAPI", return_value=fake):
            audit_path = pathlib.Path(td) / "dispatch.json"
            audit_path.write_text(json.dumps({
                "schema_version": bridge.SCHEMA,
                "phase": "PREPARED",
                "status": "READY",
                "request_id": parsed["request_id"],
                "request_sha256": contract.request_sha256(parsed),
                "request_mode": contract.CURRENT_V2_RETROSPECTIVE_REPLAY,
                "actor": "actor",
                "carrier_head_sha": "2" * 40,
                "canonical_execution_sha": "3" * 40,
            }), encoding="utf-8")
            bridge.dispatch(SimpleNamespace(repo=fake.repo, token="token", audit=str(audit_path)))
            after = json.loads(audit_path.read_text(encoding="utf-8"))
            self.assertEqual(after["request_mode"], contract.CURRENT_V2_RETROSPECTIVE_REPLAY)
            self.assertEqual(after["phase"], "DISPATCHED")
            self.assertTrue(after["dispatch_performed"])

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
        audit = {"request_id": "race", "request_sha256": contract.request_sha256(first), "request_mode": first["mode"]}
        bridge.assert_request_unchanged(audit, first)
        changed = json.loads(json.dumps(first)); changed["match"]["kickoff"] = "2026-09-07T19:45:00+00:00"
        with self.assertRaisesRegex(bridge.BridgeError, "AUTO_DISPATCH_REQUEST_CHANGED_AFTER_RESERVATION"):
            bridge.assert_request_unchanged(audit, changed)

    def test_request_sha_contract_unchanged_by_trusted_mode_binding(self) -> None:
        request = bridge.parse_request_body(self.body(self.req("sha-contract")))
        before = contract.request_sha256(request)
        self.assertEqual(bridge.trusted_request_mode(request), request["mode"])
        after = contract.request_sha256(request)
        self.assertEqual(before, after)

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

    def test_artifact_download_follows_trusted_redirect_without_forwarding_token(self) -> None:
        buf = bridge.io.BytesIO()
        with bridge.zipfile.ZipFile(buf, "w", compression=bridge.zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("ledger.json", "{}")
        zip_bytes = buf.getvalue()
        location = "https://artifact.example.invalid/archive.zip?sig=temporary"
        captured = {}

        class FakeOpener:
            def open(self, request, timeout=30):
                captured["api_request"] = request
                raise bridge.urllib.error.HTTPError(request.full_url, 302, "Found", {"Location": location}, None)

        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return zip_bytes

        def fake_urlopen(request, timeout=30):
            captured["download_request"] = request
            return FakeResponse()

        api = bridge.GitHubAPI("owner/repo", "secret-token")
        with mock.patch.object(bridge.urllib.request, "build_opener", return_value=FakeOpener()), mock.patch.object(bridge.urllib.request, "urlopen", side_effect=fake_urlopen):
            self.assertEqual(api.download_artifact_zip(123), zip_bytes)
        self.assertEqual(captured["api_request"].get_header("Authorization"), "Bearer secret-token")
        self.assertEqual(captured["download_request"].full_url, location)
        self.assertIsNone(captured["download_request"].get_header("Authorization"))
        self.assertEqual(captured["download_request"].get_header("User-agent"), "football3-gpt-auto-dispatch-bridge-v3")

    def test_artifact_download_rejects_non_https_redirect(self) -> None:
        location = "http://artifact.example.invalid/archive.zip"

        class FakeOpener:
            def open(self, request, timeout=30):
                raise bridge.urllib.error.HTTPError(request.full_url, 302, "Found", {"Location": location}, None)

        api = bridge.GitHubAPI("owner/repo", "secret-token")
        with mock.patch.object(bridge.urllib.request, "build_opener", return_value=FakeOpener()), mock.patch.object(bridge.urllib.request, "urlopen") as redirected:
            with self.assertRaisesRegex(bridge.BridgeError, "AUTO_DISPATCH_ARTIFACT_REDIRECT_INVALID"):
                api.download_artifact_zip(123)
        redirected.assert_not_called()

    def test_receiver_is_read_only_and_never_checks_out_carrier_code(self) -> None:
        text = RECEIVER_WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("pull_request_target:", text); self.assertNotIn("actions: write", text)
        receiver = text.split("  carrier-edit-receiver:", 1)[1].split("  candidate-contract-security:", 1)[0]
        self.assertNotIn("actions/checkout", receiver)
        self.assertIn("github.event.pull_request.number == 341", receiver)
        self.assertIn("github.event.action == 'edited'", receiver)
        self.assertIn("AUTO_DISPATCH_RECEIVER_SIGNAL=READY", receiver)

    def test_candidate_artifacts_bind_internal_head_to_pr_head_checkout(self) -> None:
        governed = GOVERNED_WORKFLOW.read_text(encoding="utf-8")
        exact = "CANDIDATE_EXACT_HEAD: ${{ github.event.pull_request.head.sha }}"
        self.assertEqual(governed.count(exact), 3)
        self.assertGreaterEqual(governed.count("candidate_exact_head"), 3)
        self.assertNotIn("CANDIDATE_EXACT_HEAD: ${{ github.sha }}", governed)
        self.assertGreaterEqual(governed.count('test "$(git rev-parse HEAD)" = "$CANDIDATE_EXACT_HEAD"'), 3)
        receiver = RECEIVER_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(exact, receiver)
        self.assertIn("d['candidate_exact_head']=head", receiver)
        self.assertIn('test "$(git rev-parse HEAD)" = "$CANDIDATE_EXACT_HEAD"', receiver)

    def test_formal_runner_sha_contract_remains_fail_closed(self) -> None:
        text = FORMAL_WORKFLOW.read_text(encoding="utf-8")
        for token in ("expected_request_sha256:", "request_contract_v1", "PRODUCTION_EXPECTED_REQUEST_SHA_MISSING", "PRODUCTION_REQUEST_SHA_MISMATCH", "PRODUCTION_REQUEST_CARRIER_CHANGED_FILES_UNAUTHORIZED", "run-name:"):
            self.assertIn(token, text)

    def test_binding_receipt_and_full_sha_verify(self) -> None:
        request = bridge.parse_request_body(self.body(self.req("bind"))); request_sha = contract.request_sha256(request)
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "request.json").write_bytes(contract.canonical_bytes(request))
            (root / "transport.json").write_text(json.dumps({"transport": binding.CARRIER_TRANSPORT, "expected_request_sha256": request_sha, "request_sha256": request_sha, "request_sha_verified": True, "pr_number": "341"}), encoding="utf-8")
            (root / "binding.json").write_text(json.dumps({"request_sha256": request_sha, "request_id": "bind", "request_carrier_ref": bridge.CARRIER_REF, "request_carrier_head": "1" * 40, "checkout_head_sha": "2" * 40, "canonical_base_ref": bridge.CANONICAL_REF, "runner_code_source": "CANONICAL_INTEGRATION_EXACT_SHA"}), encoding="utf-8")
            receipt = binding.verify(str(root / "request.json"), str(root / "transport.json"), str(root / "binding.json"))
            self.assertEqual(receipt["carrier_pr_number"], "341"); self.assertEqual(receipt["carrier_head_sha"], "1" * 40); self.assertEqual(receipt["canonical_execution_sha"], "2" * 40)
            changed = json.loads((root / "request.json").read_text(encoding="utf-8")); changed["match"]["away_team_name"] = "Inter"; (root / "request.json").write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(binding.RequestBindingError, "PRODUCTION_REQUEST_SHA_MISMATCH"):
                binding.verify(str(root / "request.json"), str(root / "transport.json"), str(root / "binding.json"))

    def test_non_carrier_selftest_binding_is_transport_scoped(self) -> None:
        request = contract.validate_request({"schema_version": contract.SCHEMA, "mode": "cache_reuse_probe", "request_id": "push-selftest"}, carrier_request=False)
        request_sha = contract.request_sha256(request)
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "request.json").write_bytes(contract.canonical_bytes(request))
            transport = {"transport": "COMMITTED_SELFTEST_REQUEST", "expected_request_sha256": None, "request_sha256": request_sha, "request_sha_verified": False, "pr_number": None}
            base_binding = {"request_sha256": request_sha, "request_id": "push-selftest", "request_carrier_ref": None, "request_carrier_head": None, "checkout_head_sha": "2" * 40, "canonical_base_ref": bridge.CANONICAL_REF, "runner_code_source": "CANONICAL_INTEGRATION_EXACT_SHA"}
            (root / "transport.json").write_text(json.dumps(transport), encoding="utf-8")
            (root / "binding.json").write_text(json.dumps(base_binding), encoding="utf-8")
            receipt = binding.verify(str(root / "request.json"), str(root / "transport.json"), str(root / "binding.json"))
            self.assertEqual(receipt["status"], "NOT_APPLICABLE_NON_CARRIER")
            self.assertFalse(receipt["request_sha_verified"])
            transport["expected_request_sha256"] = "3" * 64
            (root / "transport.json").write_text(json.dumps(transport), encoding="utf-8")
            with self.assertRaisesRegex(binding.RequestBindingError, "PRODUCTION_NON_CARRIER_EXPECTED_REQUEST_SHA_UNEXPECTED"):
                binding.verify(str(root / "request.json"), str(root / "transport.json"), str(root / "binding.json"))

    def test_final_orchestration_receipt_contract(self) -> None:
        source = (HERE / "auto_dispatch_bridge_v1.py").read_text(encoding="utf-8")
        for key in ('"request_id"', '"request_sha256"', '"request_mode"', '"carrier_pr_number"', '"carrier_head_sha"', '"trusted_dispatcher_sha"', '"canonical_integration_execution_sha"', '"resulting_production_run_id"', '"formal_receipt_artifact_id"', '"prediction_sha"'):
            self.assertIn(key, source)


class StaleReservationRetryPolicyTest(unittest.TestCase):
    def request(self) -> dict:
        return bridge.parse_request_body(
            bridge.BEGIN_MARKER
            + "\n"
            + json.dumps(
                {
                    "schema_version": contract.SCHEMA,
                    "mode": "ACTIVE_AT_CUTOFF_REPLAY",
                    "request_id": "stale-reservation-replay",
                    "match": {
                        "competition_id": "JPN_J1",
                        "season": "2026",
                        "kickoff": "2026-09-12T10:00:00+00:00",
                        "cutoff": "2026-09-12T09:00:00+00:00",
                        "home_team_name": "Gamba Osaka",
                        "away_team_name": "FC Tokyo",
                    },
                },
                separators=(",", ":"),
            )
            + "\n"
            + bridge.END_MARKER
        )

    def fake_api(self, *, current_head: str, prior_head: str, prior_conclusion: str):
        import io
        import zipfile

        request = self.request()
        request_sha = contract.request_sha256(request)
        reservation_name, _ = bridge.ledger_names(request["request_id"], request_sha)
        dispatcher_run_id = 77
        artifact_id = 9001
        ledger = {
            "schema_version": bridge.SCHEMA,
            "phase": "PREPARED",
            "status": "READY",
            "request_id": request["request_id"],
            "request_sha256": request_sha,
            "request_mode": request["mode"],
            "canonical_execution_sha": prior_head,
            "trusted_dispatcher_run_id": dispatcher_run_id,
        }
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("ledger.json", json.dumps(ledger, separators=(",", ":")))
        zip_bytes = raw.getvalue()
        body = bridge.BEGIN_MARKER + "\n" + json.dumps(request, separators=(",", ":")) + "\n" + bridge.END_MARKER

        class FakeAPI:
            repo = "owner/repo"

            def live_pr(self):
                return {
                    "number": bridge.CARRIER_PR_NUMBER,
                    "state": "open",
                    "draft": True,
                    "merged_at": None,
                    "body": body,
                    "base": {"ref": bridge.CANONICAL_REF, "sha": "a" * 40},
                    "head": {"ref": bridge.CARRIER_REF, "sha": "b" * 40, "repo": {"full_name": self.repo}},
                }

            def carrier_files(self):
                return [bridge.CARRIER_FILE]

            def actor_permission(self, actor):
                return "write"

            def live_canonical_sha(self):
                return current_head

            def all_artifacts(self):
                return [{"id": artifact_id, "name": reservation_name, "expired": False, "workflow_run": {"id": dispatcher_run_id}}]

            def validate_ledger_artifact_sources(self, artifacts, request_id):
                return None

            def download_artifact_zip(self, requested_artifact_id):
                if requested_artifact_id != artifact_id:
                    raise AssertionError(requested_artifact_id)
                return zip_bytes

            def json_value(self, path):
                expected = f"/repos/{self.repo}/actions/runs/{dispatcher_run_id}"
                if path != expected:
                    raise AssertionError(path)
                return {
                    "id": dispatcher_run_id,
                    "event": "workflow_run",
                    "name": bridge.TRUSTED_DISPATCHER_WORKFLOW_NAME,
                    "status": "completed",
                    "conclusion": prior_conclusion,
                }

        return FakeAPI(), artifact_id

    def prepare_args(self, fake, current_head: str, audit_path: pathlib.Path):
        return SimpleNamespace(
            repo=fake.repo,
            token="token",
            actor="actor",
            trusted_checkout_sha=current_head,
            trusted_dispatcher_sha="d" * 40,
            trusted_dispatcher_run_id="123",
            receiver_run_id="456",
            audit_out=str(audit_path),
        )

    def test_failed_old_head_reservation_is_retryable(self) -> None:
        current_head = "3" * 40
        fake, artifact_id = self.fake_api(current_head=current_head, prior_head="1" * 40, prior_conclusion="failure")
        with tempfile.TemporaryDirectory() as td, mock.patch.object(bridge, "GitHubAPI", return_value=fake):
            audit_path = pathlib.Path(td) / "audit.json"
            self.assertEqual(bridge.prepare(self.prepare_args(fake, current_head, audit_path)), 0)
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
        self.assertEqual(audit["phase"], "PREPARED")
        self.assertEqual(audit["status"], "READY")
        self.assertEqual(audit["reservation_retry_policy"], "FAILED_PRIOR_DISPATCHER_DIFFERENT_CANONICAL_ONLY")
        self.assertEqual(audit["superseded_failed_reservations"][0]["reservation_artifact_id"], artifact_id)
        self.assertFalse(audit["superseded_failed_reservations"][0]["same_canonical_execution_sha"])

    def test_failed_same_head_reservation_still_fails_closed(self) -> None:
        current_head = "3" * 40
        fake, _ = self.fake_api(current_head=current_head, prior_head=current_head, prior_conclusion="failure")
        with tempfile.TemporaryDirectory() as td, mock.patch.object(bridge, "GitHubAPI", return_value=fake):
            audit_path = pathlib.Path(td) / "audit.json"
            with self.assertRaisesRegex(bridge.BridgeError, "AUTO_DISPATCH_REQUEST_ID_RESERVATION_PRESENT"):
                bridge.prepare(self.prepare_args(fake, current_head, audit_path))
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
        self.assertEqual(audit["phase"], "RESERVATION_CONFLICT_FAIL_CLOSED")
        self.assertTrue(audit["reservation_evidence"][0]["same_canonical_execution_sha"])

    def test_nonfailed_old_head_reservation_still_fails_closed(self) -> None:
        current_head = "3" * 40
        fake, _ = self.fake_api(current_head=current_head, prior_head="1" * 40, prior_conclusion="success")
        with tempfile.TemporaryDirectory() as td, mock.patch.object(bridge, "GitHubAPI", return_value=fake):
            audit_path = pathlib.Path(td) / "audit.json"
            with self.assertRaisesRegex(bridge.BridgeError, "AUTO_DISPATCH_REQUEST_ID_RESERVATION_PRESENT"):
                bridge.prepare(self.prepare_args(fake, current_head, audit_path))
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
        self.assertEqual(audit["phase"], "RESERVATION_CONFLICT_FAIL_CLOSED")
        self.assertEqual(audit["reservation_evidence"][0]["trusted_dispatcher_conclusion"], "success")


if __name__ == "__main__":
    unittest.main(verbosity=2)
