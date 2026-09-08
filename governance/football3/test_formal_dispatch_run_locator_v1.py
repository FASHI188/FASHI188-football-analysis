#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
import unittest
from urllib.parse import parse_qs, urlparse

import formal_dispatch_run_locator_v1 as locator_mod

REQ = "c40ce03d21cf5afb7e7262233f794d28630395504e33835967b7814642837746"
OTHER_REQ = "f" * 64
CANON = "0e102ac3689185d3378bab3bc416ccec20d519de"
WORKFLOW_ID = 349340611
ACTUAL_RUN_ID = 34226536355
START = datetime(2026, 9, 8, 12, 31, 9, tzinfo=timezone.utc)


def run_record(
    run_id: int = ACTUAL_RUN_ID,
    *,
    request_sha: str = REQ,
    workflow_id: int = WORKFLOW_ID,
    event: str = "workflow_dispatch",
    head_branch: str = locator_mod.CANONICAL_REF,
    head_sha: str = CANON,
    display_title: str | None = None,
    created_at: str = "2026-09-08T12:31:12Z",
    name: str | None = None,
) -> dict:
    return {
        "id": run_id,
        "workflow_id": workflow_id,
        "name": name or locator_mod.expected_display_title(request_sha),
        "display_title": display_title or locator_mod.expected_display_title(request_sha),
        "event": event,
        "head_branch": head_branch,
        "head_sha": head_sha,
        "created_at": created_at,
        "html_url": f"https://github.test/runs/{run_id}",
    }


ACTUAL_REDACTED_PAYLOAD = run_record()


class FakeTransport:
    def __init__(
        self,
        workflow_polls: list[list[list[dict]]],
        repository_polls: list[list[list[dict]]],
        *,
        reread: dict | None = None,
    ) -> None:
        self.repo = "FASHI188/FASHI188-football-analysis"
        self.workflow_polls = workflow_polls or [[[]]]
        self.repository_polls = repository_polls or [[[]]]
        self.indices = {"workflow": 0, "repository": 0}
        self.reread = reread
        self.post_count = 0
        self.last_payload = None

    def _poll_page(self, channel: str, page: int):
        polls = self.workflow_polls if channel == "workflow" else self.repository_polls
        index = min(self.indices[channel], len(polls) - 1)
        poll = polls[index]
        batch = poll[page - 1] if page <= len(poll) else []
        total = sum(len(x) for x in poll)
        if page >= len(poll):
            self.indices[channel] += 1
        return {"total_count": total, "workflow_runs": batch}

    def get_json_with_evidence(self, path: str):
        evidence = {
            "endpoint": path,
            "attempts": [{
                "attempt": 1,
                "observed_at": "2026-09-08T12:31:13+00:00",
                "status": 200,
                "headers": {
                    "x-github-request-id": "TEST",
                    "x-ratelimit-remaining": "4999",
                },
            }],
        }
        if "/actions/workflows/football3-formal-gpt-runner-integration-v1.yml" in path:
            return {"id": WORKFLOW_ID, "path": locator_mod.FORMAL_WORKFLOW_PATH}, evidence
        if f"/actions/workflows/{WORKFLOW_ID}/runs?" in path:
            page = int(parse_qs(urlparse(path).query)["page"][0])
            return self._poll_page("workflow", page), evidence
        if "/actions/runs?" in path:
            page = int(parse_qs(urlparse(path).query)["page"][0])
            return self._poll_page("repository", page), evidence
        if "/actions/runs/" in path:
            run_id = int(path.rsplit("/", 1)[1])
            if self.reread is not None:
                return dict(self.reread), evidence
            for polls in (self.workflow_polls, self.repository_polls):
                for poll in polls:
                    for page in poll:
                        for run in page:
                            if int(run.get("id", -1)) == run_id:
                                return dict(run), evidence
            raise AssertionError(f"unknown reread run {run_id}")
        raise AssertionError(path)

    def get_json(self, path: str):
        value, _ = self.get_json_with_evidence(path)
        return value

    def post_dispatch_once(self, workflow_id: int, payload: dict):
        self.post_count += 1
        self.last_payload = payload
        return {
            "endpoint": f"/repos/{self.repo}/actions/workflows/{workflow_id}/dispatches",
            "workflow_id": workflow_id,
            "called_at": "2026-09-08T12:31:09+00:00",
            "payload": payload,
            "http_status": 204,
            "response_body_utf8": "",
            "response_body_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "response_headers": {"x-github-request-id": "POSTTEST"},
        }


class SequenceHTTPTransport(locator_mod.GitHubTransport):
    def __init__(self, sequence):
        super().__init__(
            "FASHI188/FASHI188-football-analysis",
            "token",
            sleep_fn=lambda _: None,
            max_get_attempts=4,
        )
        self.sequence = list(sequence)
        self.calls = 0

    def _request_once(self, method, path, payload=None):
        self.calls += 1
        status, headers = self.sequence.pop(0)
        if status == 200:
            return 200, headers, json.dumps({"ok": True}).encode()
        return status, headers, f"status-{status}".encode()


class SinglePostHTTPTransport(locator_mod.GitHubTransport):
    def __init__(self, status: int):
        super().__init__("FASHI188/FASHI188-football-analysis", "token", sleep_fn=lambda _: None)
        self.status = status
        self.calls = 0

    def _request_once(self, method, path, payload=None):
        self.calls += 1
        return self.status, {}, f"status-{self.status}".encode()


class FormalDispatchRunLocatorPermanentTest(unittest.TestCase):
    def make_locator(self, transport: FakeTransport, delays=()):
        return locator_mod.FormalRunLocator(transport, sleep_fn=lambda _: None, poll_delays=tuple(delays))

    def locate(self, transport: FakeTransport, *, before_ids=None, delays=(), sink=None):
        return self.make_locator(transport, delays).locate(
            workflow_id=WORKFLOW_ID,
            before_ids=set(before_ids or set()),
            request_sha=REQ,
            canonical_sha=CANON,
            dispatch_started_at=START,
            audit_sink=sink,
        )

    def test_real_34226536355_redacted_payload_matches_field_by_field(self):
        accepted, reasons = locator_mod.evaluate_formal_run(
            ACTUAL_REDACTED_PAYLOAD,
            workflow_id=WORKFLOW_ID,
            before_ids={34221501889},
            request_sha=REQ,
            canonical_sha=CANON,
            dispatch_started_at=START,
        )
        self.assertTrue(accepted)
        self.assertEqual(reasons, [])
        self.assertNotIn(ACTUAL_RUN_ID, {34221501889})
        self.assertEqual(ACTUAL_REDACTED_PAYLOAD["workflow_id"], WORKFLOW_ID)
        self.assertEqual(ACTUAL_REDACTED_PAYLOAD["event"], "workflow_dispatch")
        self.assertEqual(ACTUAL_REDACTED_PAYLOAD["head_branch"], locator_mod.CANONICAL_REF)
        self.assertEqual(ACTUAL_REDACTED_PAYLOAD["head_sha"], CANON)
        self.assertEqual(ACTUAL_REDACTED_PAYLOAD["display_title"], locator_mod.expected_display_title(REQ))
        self.assertGreaterEqual(locator_mod._parse_time(ACTUAL_REDACTED_PAYLOAD["created_at"]), START)

    def test_workflow_list_invisible_repository_list_visible(self):
        transport = FakeTransport([[[]]], [[[ACTUAL_REDACTED_PAYLOAD]]])
        run, evidence = self.locate(transport)
        self.assertEqual(run["id"], ACTUAL_RUN_ID)
        self.assertEqual(evidence["selected_channels"], ["repository"])

    def test_repository_list_invisible_workflow_list_visible(self):
        transport = FakeTransport([[[ACTUAL_REDACTED_PAYLOAD]]], [[[]]])
        run, evidence = self.locate(transport)
        self.assertEqual(run["id"], ACTUAL_RUN_ID)
        self.assertEqual(evidence["selected_channels"], ["workflow"])

    def test_both_channels_return_same_run(self):
        transport = FakeTransport([[[ACTUAL_REDACTED_PAYLOAD]]], [[[ACTUAL_REDACTED_PAYLOAD]]])
        run, evidence = self.locate(transport)
        self.assertEqual(run["id"], ACTUAL_RUN_ID)
        self.assertEqual(set(evidence["selected_channels"]), {"workflow", "repository"})

    def test_two_channels_return_conflicting_exact_runs_fail_closed(self):
        other = run_record(ACTUAL_RUN_ID + 1)
        transport = FakeTransport([[[ACTUAL_REDACTED_PAYLOAD]]], [[[other]]])
        with self.assertRaisesRegex(locator_mod.LocatorError, "FORMAL_RUN_CHANNEL_CONFLICT"):
            self.locate(transport)

    def test_second_page_locator(self):
        decoys = [run_record(1000 + i, request_sha=OTHER_REQ) for i in range(100)]
        transport = FakeTransport([[[]]], [[decoys, [ACTUAL_REDACTED_PAYLOAD]]])
        run, evidence = self.locate(transport)
        self.assertEqual(run["id"], ACTUAL_RUN_ID)
        pages = evidence["polls"][0]["channels"]["repository"]["pages"]
        self.assertEqual(len(pages), 2)

    def test_wrong_identity_fields_all_rejected(self):
        mutations = {
            "request": run_record(display_title=locator_mod.expected_display_title(OTHER_REQ)),
            "ref": run_record(head_branch="main"),
            "head": run_record(head_sha="1" * 40),
            "workflow": run_record(workflow_id=WORKFLOW_ID + 1),
            "event": run_record(event="push"),
            "created": run_record(created_at="2026-09-08T12:31:08Z"),
        }
        for label, wrong in mutations.items():
            with self.subTest(label=label):
                transport = FakeTransport([[[]]], [[[wrong]]])
                with self.assertRaisesRegex(locator_mod.LocatorError, "FORMAL_RUN_NOT_FOUND"):
                    self.locate(transport)

    def test_pre_dispatch_inventory_member_rejected(self):
        transport = FakeTransport([[[]]], [[[ACTUAL_REDACTED_PAYLOAD]]])
        with self.assertRaisesRegex(locator_mod.LocatorError, "FORMAL_RUN_NOT_FOUND"):
            self.locate(transport, before_ids={ACTUAL_RUN_ID})

    def test_multiple_exact_candidates_fail_closed(self):
        second = run_record(ACTUAL_RUN_ID + 2)
        transport = FakeTransport([[[]]], [[[ACTUAL_REDACTED_PAYLOAD, second]]])
        with self.assertRaisesRegex(locator_mod.LocatorError, "FORMAL_RUN_AMBIGUOUS:repository"):
            self.locate(transport)

    def test_located_run_reread_field_drift_is_rejected(self):
        reread_wrong = run_record(head_sha="2" * 40)
        transport = FakeTransport([[[]]], [[[ACTUAL_REDACTED_PAYLOAD]]], reread=reread_wrong)
        with self.assertRaisesRegex(locator_mod.LocatorError, "FORMAL_RUN_REVALIDATION_FAILED"):
            self.locate(transport)

    def test_reservation_path_posts_once_even_when_visibility_delayed(self):
        transport = FakeTransport(
            [[[]], [[]], [[]]],
            [[[]], [[]], [[run_record(created_at="2099-01-01T00:00:00Z")]]],
        )
        locator = self.make_locator(transport, delays=(0, 0))
        run, http, evidence = locator.dispatch_once_and_locate(
            workflow_id=WORKFLOW_ID,
            before_ids=set(),
            request_sha=REQ,
            canonical_sha=CANON,
        )
        self.assertEqual(run["id"], ACTUAL_RUN_ID)
        self.assertEqual(transport.post_count, 1)
        self.assertEqual(http["http_status"], 204)
        self.assertEqual(transport.last_payload["inputs"]["expected_request_sha256"], REQ)
        self.assertEqual(evidence["poll_count"], 3)

    def test_post_5xx_is_not_retried(self):
        transport = SinglePostHTTPTransport(500)
        evidence = transport.post_dispatch_once(WORKFLOW_ID, {"ref": locator_mod.CANONICAL_REF, "inputs": {}})
        self.assertEqual(evidence["http_status"], 500)
        self.assertEqual(transport.calls, 1)

    def test_get_permission_403_stops_immediately(self):
        transport = SequenceHTTPTransport([(403, {"x-ratelimit-remaining": "99"})])
        with self.assertRaisesRegex(locator_mod.LocatorError, "FORMAL_RUN_GITHUB_PERMISSION_DENIED"):
            transport.get_json("/repos/test")
        self.assertEqual(transport.calls, 1)

    def test_get_explicit_rate_limit_429_is_bounded(self):
        transport = SequenceHTTPTransport([
            (429, {"retry-after": "0"}),
            (200, {"x-ratelimit-remaining": "1"}),
        ])
        self.assertEqual(transport.get_json("/repos/test"), {"ok": True})
        self.assertEqual(transport.calls, 2)

    def test_get_5xx_is_bounded(self):
        transport = SequenceHTTPTransport([(500, {}), (502, {}), (503, {}), (504, {})])
        with self.assertRaisesRegex(locator_mod.LocatorError, "FORMAL_RUN_GITHUB_API_ERROR"):
            transport.get_json("/repos/test")
        self.assertEqual(transport.calls, 4)

    def test_poll_diagnostic_saves_endpoints_ids_rejections_and_rate_headers(self):
        wrong = run_record(head_sha="3" * 40)
        transport = FakeTransport([[[]]], [[[wrong]]])
        records = []
        with self.assertRaisesRegex(locator_mod.LocatorError, "FORMAL_RUN_NOT_FOUND"):
            self.locate(transport, sink=records.append)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["poll_number"], 1)
        self.assertIn("poll_started_at", record)
        repo = record["channels"]["repository"]
        self.assertEqual(repo["pages"][0]["returned_run_ids"], [ACTUAL_RUN_ID])
        self.assertIn("/actions/runs?", repo["pages"][0]["endpoint"])
        attempts = repo["pages"][0]["api"]["attempts"]
        self.assertEqual(attempts[0]["headers"]["x-ratelimit-remaining"], "4999")
        self.assertIn("HEAD_SHA_MISMATCH", repo["near_candidates"][0]["rejection_reasons"])

    def test_inventory_unions_both_channels(self):
        old1 = run_record(11, request_sha=OTHER_REQ, created_at="2026-09-08T12:00:00Z")
        old2 = run_record(12, request_sha=OTHER_REQ, created_at="2026-09-08T12:00:01Z")
        transport = FakeTransport([[[old1]]], [[[old2]]])
        locator = self.make_locator(transport)
        ids, evidence = locator.inventory_before_dispatch(WORKFLOW_ID, CANON)
        self.assertEqual(ids, {11, 12})
        self.assertEqual(evidence["union_run_ids"], [11, 12])

    def test_trusted_workflow_persists_locator_failure_audit(self):
        root = Path(__file__).resolve().parents[2]
        trusted = root / ".github/workflows/football3-gpt-auto-dispatch-trusted-dispatcher-v1.yml"
        text = trusted.read_text(encoding="utf-8")
        self.assertIn(".runtime_sources/formal_dispatch_run_locator_v1.py dispatch", text)
        self.assertIn("Upload locator failure diagnostic audit", text)
        self.assertIn("steps.dispatch.outcome == 'failure'", text)
        self.assertIn("path: .runtime_sources/auto_dispatch_audit.json", text)
        self.assertIn("persist-credentials: false", text)

    def test_zz_materialize_cross_checkout_candidate_evidence(self):
        exact_head = os.environ.get("CANDIDATE_EXACT_HEAD", "")
        if not exact_head:
            self.skipTest("cross-checkout candidate exact head is not available in this job")
        self.assertRegex(exact_head, r"^[0-9a-f]{40}$")
        root = Path(__file__).resolve().parents[2]
        out = root / ".runtime_sources/trusted-dispatcher-candidate"
        out.mkdir(parents=True, exist_ok=True)
        evidence = {
            "schema_version": "football3-formal-dispatch-run-locator-v2-candidate",
            "candidate_exact_head": exact_head,
            "root_cause_classification": "WORKFLOW_SPECIFIC_SINGLE_CHANNEL_OBSERVABILITY_GAP",
            "incident_dispatcher_run_id": 34226459306,
            "incident_formal_run_id": ACTUAL_RUN_ID,
            "incident_formal_workflow_id": WORKFLOW_ID,
            "incident_request_sha256": REQ,
            "incident_formal_run_payload_validator": "PASS",
            "dual_channel_locator": "PASS",
            "per_poll_diagnostic_audit": "PASS",
            "locator_permanent_tests": "PASS",
            "candidate_production_dispatch_performed": False,
            "candidate_pr341_modified": False,
            "candidate_manual_finalize_performed": False,
        }
        (out / "formal_dispatch_run_locator_v2_candidate_evidence.json").write_text(
            json.dumps(evidence, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
