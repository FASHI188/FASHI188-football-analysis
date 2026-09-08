#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import unittest
from urllib.parse import parse_qs, urlparse

import formal_dispatch_run_locator_v1 as locator_mod

REQ = "080c7f6b59c19889fb829420291137911ba6a628d0f90c7bde1c44b40feaace0"
OTHER_REQ = "f" * 64
CANON = "0e102ac3689185d3378bab3bc416ccec20d519de"
WORKFLOW_ID = 349340611
ACTUAL_RUN_ID = 34230518992
START = datetime(2026, 9, 8, 13, 12, 6, tzinfo=timezone.utc)


def run_record(
    run_id: int = ACTUAL_RUN_ID,
    *,
    request_sha: str = REQ,
    workflow_id: int = WORKFLOW_ID,
    event: str = "workflow_dispatch",
    head_branch: str = locator_mod.CANONICAL_REF,
    head_sha: str = CANON,
    display_title: str | None = None,
    created_at: str = "2026-09-08T13:12:07Z",
) -> dict:
    return {
        "id": run_id,
        "workflow_id": workflow_id,
        "name": locator_mod.FORMAL_RUN_PREFIX,
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
        stale_workflow: bool = False,
        stale_repository: bool = False,
    ) -> None:
        self.repo = "FASHI188/FASHI188-football-analysis"
        self.workflow_polls = workflow_polls or [[[]]]
        self.repository_polls = repository_polls or [[[]]]
        self.indices = {"workflow": 0, "repository": 0}
        self.reread = reread
        self.post_count = 0
        self.last_payload = None
        self.paths: list[str] = []
        self.stale_workflow = stale_workflow
        self.stale_repository = stale_repository

    def _poll_page(self, channel: str, page: int):
        polls = self.workflow_polls if channel == "workflow" else self.repository_polls
        index = min(self.indices[channel], len(polls) - 1)
        poll = polls[index]
        batch = poll[page - 1] if page <= len(poll) else []
        total = sum(len(x) for x in poll)
        if page >= len(poll):
            self.indices[channel] += 1
        return {"total_count": total, "workflow_runs": batch}

    def _evidence(self, path: str, channel: str, value: dict):
        stale = self.stale_workflow if channel == "workflow" else self.stale_repository
        index = 0 if stale else len(self.paths)
        body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        return {
            "endpoint": path,
            "attempts": [{
                "attempt": 1,
                "observed_at": "2026-09-08T13:12:13+00:00",
                "status": 200,
                "headers": {
                    "date": "Tue, 08 Sep 2026 13:12:06 GMT" if stale else f"Tue, 08 Sep 2026 13:12:{10 + index:02d} GMT",
                    "x-github-request-id": f"STALE-{channel}" if stale else f"REQ-{channel}-{index}",
                    "x-ratelimit-remaining": "4999",
                    "content-length": str(len(body)),
                },
                "body_sha256": hashlib.sha256(body).hexdigest(),
            }],
        }

    def get_json_with_evidence(self, path: str):
        self.paths.append(path)
        parsed = urlparse(path)
        if "/actions/workflows/football3-formal-gpt-runner-integration-v1.yml" in parsed.path:
            value = {"id": WORKFLOW_ID, "path": locator_mod.FORMAL_WORKFLOW_PATH}
            return value, self._evidence(path, "workflow", value)
        if f"/actions/workflows/{WORKFLOW_ID}/runs" in parsed.path:
            page = int(parse_qs(parsed.query)["page"][0])
            value = self._poll_page("workflow", page)
            return value, self._evidence(path, "workflow", value)
        if parsed.path.endswith("/actions/runs"):
            page = int(parse_qs(parsed.query)["page"][0])
            value = self._poll_page("repository", page)
            return value, self._evidence(path, "repository", value)
        if "/actions/runs/" in parsed.path:
            run_id = int(parsed.path.rsplit("/", 1)[1])
            if self.reread is not None:
                value = dict(self.reread)
                return value, self._evidence(path, "repository", value)
            for polls in (self.workflow_polls, self.repository_polls):
                for poll in polls:
                    for page in poll:
                        for run in page:
                            if int(run.get("id", -1)) == run_id:
                                value = dict(run)
                                return value, self._evidence(path, "repository", value)
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
            "called_at": "2026-09-08T13:12:06+00:00",
            "payload": payload,
            "http_status": 204,
            "response_body_utf8": "",
            "response_body_sha256": hashlib.sha256(b"").hexdigest(),
            "response_headers": {"x-github-request-id": "POSTTEST"},
        }


class SequenceHTTPTransport(locator_mod.GitHubTransport):
    def __init__(self, sequence, *, max_http_requests=20):
        super().__init__(
            "FASHI188/FASHI188-football-analysis",
            "token",
            sleep_fn=lambda _: None,
            max_get_attempts=4,
            max_http_requests=max_http_requests,
        )
        self.sequence = list(sequence)

    def _request_once(self, method, path, payload=None):
        self.http_request_count += 1
        if self.http_request_count > self.max_http_requests:
            raise locator_mod.LocatorError("FORMAL_RUN_GITHUB_REQUEST_BUDGET_EXCEEDED")
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
    def make_locator(self, transport: FakeTransport, delays=(), max_pages=10):
        return locator_mod.FormalRunLocator(transport, sleep_fn=lambda _: None, poll_delays=tuple(delays), max_pages=max_pages)

    def locate(self, transport: FakeTransport, *, before_ids=None, delays=(), sink=None, max_pages=10):
        return self.make_locator(transport, delays, max_pages).locate(
            workflow_id=WORKFLOW_ID,
            before_ids=set(before_ids or set()),
            request_sha=REQ,
            canonical_sha=CANON,
            dispatch_started_at=START,
            audit_sink=sink,
        )

    def test_incident_34230518992_redacted_payload_matches_strict_validator(self):
        accepted, reasons = locator_mod.evaluate_formal_run(ACTUAL_REDACTED_PAYLOAD, workflow_id=WORKFLOW_ID, before_ids={34226536355}, request_sha=REQ, canonical_sha=CANON, dispatch_started_at=START)
        self.assertTrue(accepted)
        self.assertEqual(reasons, [])

    def test_cached_workflow_response_other_channel_finds_new_run(self):
        old = run_record(34226536355, request_sha=OTHER_REQ, created_at="2026-09-08T12:31:12Z")
        transport = FakeTransport([[[old]], [[old]]], [[[]], [[ACTUAL_REDACTED_PAYLOAD]]], stale_workflow=True)
        records = []
        run, evidence = self.locate(transport, delays=(0,), sink=records.append)
        self.assertEqual(run["id"], ACTUAL_RUN_ID)
        self.assertEqual(evidence["selected_channels"], ["repository"])
        self.assertTrue(records[1]["channels"]["workflow"]["pages"][0]["stale_response_suspected"])
        self.assertEqual(records[1]["channels"]["workflow"]["pages"][0]["freshness_status"], "STALE_RESPONSE_SUSPECTED")

    def test_each_poll_channel_page_has_unique_cache_buster(self):
        transport = FakeTransport([[[]], [[]]], [[[]], [[]]])
        with self.assertRaisesRegex(locator_mod.LocatorError, "FORMAL_RUN_NOT_FOUND"):
            self.locate(transport, delays=(0,))
        busters = []
        for path in transport.paths:
            qs = parse_qs(urlparse(path).query)
            if "f3_cache_buster" in qs:
                busters.append(qs["f3_cache_buster"][0])
        self.assertGreaterEqual(len(busters), 4)
        self.assertEqual(len(busters), len(set(busters)))

    def test_get_request_writes_no_cache_headers(self):
        transport = locator_mod.GitHubTransport("FASHI188/FASHI188-football-analysis", "token")
        req = transport._build_request("GET", "/repos/test/actions/runs?event=workflow_dispatch")
        headers = {k.lower(): v for k, v in req.header_items()}
        self.assertEqual(headers["cache-control"], "no-cache, no-store, max-age=0")
        self.assertEqual(headers["pragma"], "no-cache")

    def test_repository_query_does_not_remote_filter_branch_or_head_sha(self):
        transport = FakeTransport([[[]]], [[[]]])
        locator = self.make_locator(transport)
        endpoint, _ = locator._endpoint("repository", WORKFLOW_ID, 1, poll_number=1, created_anchor=START)
        query = parse_qs(urlparse(endpoint).query)
        self.assertEqual(query["event"], ["workflow_dispatch"])
        self.assertIn("created", query)
        self.assertNotIn("branch", query)
        self.assertNotIn("head_sha", query)

    def test_repository_mixed_runs_local_validator_accepts_only_unique_correct_run(self):
        mixed = [run_record(10, workflow_id=WORKFLOW_ID + 1), run_record(11, request_sha=OTHER_REQ), run_record(12, head_branch="main"), run_record(13, head_sha="1" * 40), run_record(14, event="push"), ACTUAL_REDACTED_PAYLOAD]
        transport = FakeTransport([[[]]], [[mixed]])
        run, evidence = self.locate(transport)
        self.assertEqual(run["id"], ACTUAL_RUN_ID)
        self.assertEqual(evidence["selected_channels"], ["repository"])

    def test_wrong_identity_fields_all_rejected(self):
        mutations = {
            "request": run_record(display_title=locator_mod.expected_display_title(OTHER_REQ)),
            "ref": run_record(head_branch="main"),
            "head": run_record(head_sha="1" * 40),
            "workflow": run_record(workflow_id=WORKFLOW_ID + 1),
            "event": run_record(event="push"),
            "created": run_record(created_at="2026-09-08T13:12:05Z"),
        }
        for label, wrong in mutations.items():
            with self.subTest(label=label):
                with self.assertRaisesRegex(locator_mod.LocatorError, "FORMAL_RUN_NOT_FOUND"):
                    self.locate(FakeTransport([[[]]], [[[wrong]]]))

    def test_two_channels_same_run_merge(self):
        run, evidence = self.locate(FakeTransport([[[ACTUAL_REDACTED_PAYLOAD]]], [[[ACTUAL_REDACTED_PAYLOAD]]]))
        self.assertEqual(run["id"], ACTUAL_RUN_ID)
        self.assertEqual(set(evidence["selected_channels"]), {"workflow", "repository"})

    def test_two_channels_conflicting_runs_fail_closed(self):
        with self.assertRaisesRegex(locator_mod.LocatorError, "FORMAL_RUN_CHANNEL_CONFLICT"):
            self.locate(FakeTransport([[[ACTUAL_REDACTED_PAYLOAD]]], [[[run_record(ACTUAL_RUN_ID + 1)]]]))

    def test_multiple_candidates_fail_closed(self):
        with self.assertRaisesRegex(locator_mod.LocatorError, "FORMAL_RUN_AMBIGUOUS:repository"):
            self.locate(FakeTransport([[[]]], [[[ACTUAL_REDACTED_PAYLOAD, run_record(ACTUAL_RUN_ID + 2)]]]))

    def test_selected_run_reread_field_drift_rejected(self):
        with self.assertRaisesRegex(locator_mod.LocatorError, "FORMAL_RUN_REVALIDATION_FAILED"):
            self.locate(FakeTransport([[[]]], [[[ACTUAL_REDACTED_PAYLOAD]]], reread=run_record(head_sha="2" * 40)))

    def test_reread_has_unique_cache_buster_and_revalidated_true(self):
        _, evidence = self.locate(FakeTransport([[[]]], [[[ACTUAL_REDACTED_PAYLOAD]]]))
        self.assertTrue(evidence["reread"]["revalidated"])
        self.assertIn("f3_cache_buster=", evidence["reread"]["endpoint"])

    def test_pagination_second_page(self):
        decoys = [run_record(1000 + i, request_sha=OTHER_REQ) for i in range(100)]
        run, evidence = self.locate(FakeTransport([[[]]], [[decoys, [ACTUAL_REDACTED_PAYLOAD]]]))
        self.assertEqual(run["id"], ACTUAL_RUN_ID)
        self.assertEqual(len(evidence["polls"][0]["channels"]["repository"]["pages"]), 2)

    def test_pagination_limit_fail_closed(self):
        full = [run_record(1000 + i, request_sha=OTHER_REQ) for i in range(100)]
        with self.assertRaisesRegex(locator_mod.LocatorError, "FORMAL_RUN_LIST_PAGINATION_LIMIT:repository"):
            self.locate(FakeTransport([[[]]], [[full, full]]), max_pages=1)

    def test_pre_dispatch_inventory_member_rejected(self):
        with self.assertRaisesRegex(locator_mod.LocatorError, "FORMAL_RUN_NOT_FOUND"):
            self.locate(FakeTransport([[[]]], [[[ACTUAL_REDACTED_PAYLOAD]]]), before_ids={ACTUAL_RUN_ID})

    def test_dispatch_post_exactly_once_when_visibility_delayed(self):
        future = run_record(created_at="2099-01-01T00:00:00Z")
        transport = FakeTransport([[[]], [[]], [[]]], [[[]], [[]], [[future]]])
        run, http, evidence = self.make_locator(transport, delays=(0, 0)).dispatch_once_and_locate(workflow_id=WORKFLOW_ID, before_ids=set(), request_sha=REQ, canonical_sha=CANON)
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

    def test_permission_403_stops_immediately(self):
        transport = SequenceHTTPTransport([(403, {"x-ratelimit-remaining": "99"})])
        with self.assertRaisesRegex(locator_mod.LocatorError, "FORMAL_RUN_GITHUB_PERMISSION_DENIED"):
            transport.get_json("/repos/test")
        self.assertEqual(transport.http_request_count, 1)

    def test_rate_limit_429_is_bounded(self):
        transport = SequenceHTTPTransport([(429, {"retry-after": "0"}), (200, {"x-ratelimit-remaining": "1"})])
        self.assertEqual(transport.get_json("/repos/test"), {"ok": True})
        self.assertEqual(transport.http_request_count, 2)

    def test_5xx_is_bounded(self):
        transport = SequenceHTTPTransport([(500, {}), (502, {}), (503, {}), (504, {})])
        with self.assertRaisesRegex(locator_mod.LocatorError, "FORMAL_RUN_GITHUB_API_ERROR"):
            transport.get_json("/repos/test")
        self.assertEqual(transport.http_request_count, 4)

    def test_http_request_budget_fail_closed(self):
        transport = SequenceHTTPTransport([(200, {}), (200, {}), (200, {})], max_http_requests=2)
        self.assertEqual(transport.get_json("/repos/a"), {"ok": True})
        self.assertEqual(transport.get_json("/repos/b"), {"ok": True})
        with self.assertRaisesRegex(locator_mod.LocatorError, "FORMAL_RUN_GITHUB_REQUEST_BUDGET_EXCEEDED"):
            transport.get_json("/repos/c")

    def test_failure_audit_explains_each_poll_response(self):
        wrong = run_record(head_sha="3" * 40)
        records = []
        with self.assertRaisesRegex(locator_mod.LocatorError, "FORMAL_RUN_NOT_FOUND"):
            self.locate(FakeTransport([[[wrong]], [[wrong]]], [[[]], [[]]], stale_workflow=True), delays=(0,), sink=records.append)
        self.assertEqual(len(records), 2)
        for index, record in enumerate(records, start=1):
            self.assertEqual(record["poll_number"], index)
            for channel in ("workflow", "repository"):
                page = record["channels"][channel]["pages"][0]
                self.assertIn("endpoint", page)
                self.assertIn("cache_buster", page)
                attempt = page["api"]["attempts"][0]
                self.assertIn("observed_at", attempt)
                self.assertIn("body_sha256", attempt)
                self.assertIn("x-github-request-id", attempt["headers"])
                self.assertIn("x-ratelimit-remaining", attempt["headers"])
        self.assertIn("HEAD_SHA_MISMATCH", records[0]["channels"]["workflow"]["near_candidates"][0]["rejection_reasons"])
        self.assertEqual(records[1]["channels"]["workflow"]["pages"][0]["freshness_status"], "STALE_RESPONSE_SUSPECTED")

    def test_inventory_unions_both_channels_without_remote_sha_correctness(self):
        old1 = run_record(11, request_sha=OTHER_REQ, created_at="2026-09-08T13:11:00Z")
        old2 = run_record(12, request_sha=OTHER_REQ, created_at="2026-09-08T13:11:01Z")
        locator = self.make_locator(FakeTransport([[[old1]]], [[[old2]]]))
        ids, evidence = locator.inventory_before_dispatch(WORKFLOW_ID, CANON)
        self.assertEqual(ids, {11, 12})
        repo_endpoint = evidence["channels"]["repository"]["pages"][0]["endpoint"]
        self.assertNotIn("head_sha=", repo_endpoint)
        self.assertNotIn("branch=", repo_endpoint)

    def test_trusted_workflow_still_persists_locator_failure_audit(self):
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
            self.skipTest("candidate exact head unavailable")
        self.assertRegex(exact_head, r"^[0-9a-f]{40}$")
        root = Path(__file__).resolve().parents[2]
        out = root / ".runtime_sources/trusted-dispatcher-candidate"
        out.mkdir(parents=True, exist_ok=True)
        evidence = {
            "schema_version": "football3-formal-dispatch-freshness-locator-v3-candidate",
            "candidate_exact_head": exact_head,
            "root_cause_classification": "GITHUB_ACTIONS_RUN_LIST_CACHE_AND_REMOTE_FILTER_GAP",
            "incident_dispatcher_run_id": 34230442565,
            "incident_formal_run_id": ACTUAL_RUN_ID,
            "incident_request_sha256": REQ,
            "incident_canonical_sha": CANON,
            "incident_locator_failure_artifact_id": 10057624378,
            "freshness_headers": "PASS",
            "unique_cache_buster": "PASS",
            "stale_response_detection": "PASS",
            "repository_local_identity_validation": "PASS",
            "repository_remote_branch_head_filter_removed": "PASS",
            "dual_channel_locator": "PASS",
            "selected_run_reread": "PASS",
            "bounded_pagination_and_request_budget": "PASS",
            "single_dispatch_post": "PASS",
            "locator_permanent_tests": "PASS",
            "candidate_production_dispatch_performed": False,
            "candidate_pr341_modified": False,
            "candidate_manual_finalize_performed": False,
        }
        (out / "formal_dispatch_freshness_locator_candidate_evidence.json").write_text(json.dumps(evidence, sort_keys=True, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main(verbosity=2)
