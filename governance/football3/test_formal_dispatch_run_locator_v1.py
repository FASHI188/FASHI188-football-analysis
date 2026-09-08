#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import unittest

import formal_dispatch_run_locator_v1 as locator_mod

ROOT = Path(__file__).resolve().parents[2]
TRUSTED = ROOT / ".github/workflows/football3-gpt-auto-dispatch-trusted-dispatcher-v1.yml"
REQ = "27fa0c24989be863ba9a353fcb90f986fe04a7d4a187afcca82bc1d2b1472f1b"
OTHER_REQ = "f" * 64
CANON = "0e102ac3689185d3378bab3bc416ccec20d519de"
WORKFLOW_ID = 349340611
START = datetime(2026, 9, 8, 11, 35, 29, tzinfo=timezone.utc)


def run_record(
    run_id: int,
    *,
    request_sha: str = REQ,
    head_branch: str = locator_mod.CANONICAL_REF,
    head_sha: str = CANON,
    workflow_id: int = WORKFLOW_ID,
    created_at: str = "2026-09-08T11:35:32Z",
    name: str | None = None,
) -> dict:
    return {
        "id": run_id,
        "workflow_id": workflow_id,
        "name": name or locator_mod.expected_display_title(request_sha),
        "display_title": locator_mod.expected_display_title(request_sha),
        "event": "workflow_dispatch",
        "head_branch": head_branch,
        "head_sha": head_sha,
        "created_at": created_at,
        "html_url": f"https://github.test/runs/{run_id}",
    }


class FakeTransport:
    def __init__(self, polls: list[list[list[dict]]], *, reread: dict | None = None) -> None:
        self.repo = "FASHI188/FASHI188-football-analysis"
        self.polls = polls
        self.poll_index = 0
        self.reread = reread
        self.post_count = 0
        self.last_payload = None

    def get_json(self, path: str):
        if "/actions/workflows/football3-formal-gpt-runner-integration-v1.yml" in path:
            return {"id": WORKFLOW_ID, "path": locator_mod.FORMAL_WORKFLOW_PATH}
        if "/actions/workflows/" in path and "/runs?" in path:
            page = int(path.rsplit("page=", 1)[1])
            poll = self.polls[min(self.poll_index, len(self.polls) - 1)]
            batch = poll[page - 1] if page <= len(poll) else []
            total = sum(len(x) for x in poll)
            if page >= len(poll):
                self.poll_index += 1
            return {"total_count": total, "workflow_runs": batch}
        if "/actions/runs/" in path:
            run_id = int(path.rsplit("/", 1)[1])
            if self.reread is not None:
                return dict(self.reread)
            for poll in self.polls:
                for page in poll:
                    for run in page:
                        if int(run.get("id", -1)) == run_id:
                            return dict(run)
            raise AssertionError(f"unknown run {run_id}")
        raise AssertionError(path)

    def post_dispatch_once(self, workflow_id: int, payload: dict):
        self.post_count += 1
        self.last_payload = payload
        return {
            "endpoint": f"/repos/{self.repo}/actions/workflows/{workflow_id}/dispatches",
            "workflow_id": workflow_id,
            "called_at": "2026-09-08T11:35:29+00:00",
            "payload": payload,
            "http_status": 204,
            "response_body_utf8": "",
            "response_body_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "response_headers": {},
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
        status = self.sequence.pop(0)
        if status == 200:
            return 200, {}, json.dumps({"ok": True}).encode()
        return status, {}, f"status-{status}".encode()


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
        return locator_mod.FormalRunLocator(
            transport,
            sleep_fn=lambda _: None,
            poll_delays=tuple(delays),
        )

    def test_dispatch_success_run_appears_immediately(self):
        candidate = run_record(34221501889)
        transport = FakeTransport([[[candidate]]])
        run, evidence = self.make_locator(transport).locate(
            workflow_id=WORKFLOW_ID,
            before_ids=set(),
            request_sha=REQ,
            canonical_sha=CANON,
            dispatch_started_at=START,
        )
        self.assertEqual(run["id"], 34221501889)
        self.assertEqual(evidence["poll_count"], 1)

    def test_run_delayed_for_multiple_polls_then_appears(self):
        candidate = run_record(34221501889)
        transport = FakeTransport([[[]], [[]], [[]], [[candidate]]])
        run, evidence = self.make_locator(transport, delays=(0, 0, 0)).locate(
            workflow_id=WORKFLOW_ID,
            before_ids=set(),
            request_sha=REQ,
            canonical_sha=CANON,
            dispatch_started_at=START,
        )
        self.assertEqual(run["id"], 34221501889)
        self.assertEqual(evidence["poll_count"], 4)

    def test_run_on_second_page_is_found(self):
        decoys = [run_record(1000 + i, request_sha=OTHER_REQ) for i in range(100)]
        candidate = run_record(34221501889)
        transport = FakeTransport([[decoys, [candidate]]])
        run, evidence = self.make_locator(transport).locate(
            workflow_id=WORKFLOW_ID,
            before_ids=set(),
            request_sha=REQ,
            canonical_sha=CANON,
            dispatch_started_at=START,
        )
        self.assertEqual(run["id"], 34221501889)
        self.assertEqual(evidence["pages_per_poll"], [2])

    def test_display_title_binds_request_sha_even_when_name_is_fixed(self):
        candidate = run_record(34221501889, name="Football3 Formal GPT Runner Integration V1")
        transport = FakeTransport([[[candidate]]])
        run, _ = self.make_locator(transport).locate(
            workflow_id=WORKFLOW_ID,
            before_ids=set(),
            request_sha=REQ,
            canonical_sha=CANON,
            dispatch_started_at=START,
        )
        self.assertEqual(run["id"], 34221501889)

    def test_old_workflow_dispatch_run_is_not_selected(self):
        old = run_record(42)
        transport = FakeTransport([[[old]]])
        with self.assertRaisesRegex(locator_mod.LocatorError, "FORMAL_RUN_NOT_FOUND"):
            self.make_locator(transport).locate(
                workflow_id=WORKFLOW_ID,
                before_ids={42},
                request_sha=REQ,
                canonical_sha=CANON,
                dispatch_started_at=START,
            )

    def test_other_request_sha_is_not_selected(self):
        wrong = run_record(43, request_sha=OTHER_REQ)
        transport = FakeTransport([[[wrong]]])
        with self.assertRaisesRegex(locator_mod.LocatorError, "FORMAL_RUN_NOT_FOUND"):
            self.make_locator(transport).locate(
                workflow_id=WORKFLOW_ID,
                before_ids=set(),
                request_sha=REQ,
                canonical_sha=CANON,
                dispatch_started_at=START,
            )

    def test_wrong_ref_or_head_is_not_selected(self):
        wrong_ref = run_record(44, head_branch="main")
        wrong_head = run_record(45, head_sha="1" * 40)
        transport = FakeTransport([[[wrong_ref, wrong_head]]])
        with self.assertRaisesRegex(locator_mod.LocatorError, "FORMAL_RUN_NOT_FOUND"):
            self.make_locator(transport).locate(
                workflow_id=WORKFLOW_ID,
                before_ids=set(),
                request_sha=REQ,
                canonical_sha=CANON,
                dispatch_started_at=START,
            )

    def test_multiple_candidates_fail_closed(self):
        transport = FakeTransport([[[run_record(46), run_record(47)]]])
        with self.assertRaisesRegex(locator_mod.LocatorError, "FORMAL_RUN_AMBIGUOUS"):
            self.make_locator(transport).locate(
                workflow_id=WORKFLOW_ID,
                before_ids=set(),
                request_sha=REQ,
                canonical_sha=CANON,
                dispatch_started_at=START,
            )

    def test_api_403_429_5xx_are_bounded_and_retryable_for_get(self):
        transport = SequenceHTTPTransport([403, 429, 500, 200])
        self.assertEqual(transport.get_json("/repos/test"), {"ok": True})
        self.assertEqual(transport.calls, 4)
        failing = SequenceHTTPTransport([500, 500, 500, 500])
        with self.assertRaisesRegex(locator_mod.LocatorError, "FORMAL_RUN_GITHUB_API_ERROR"):
            failing.get_json("/repos/test")
        self.assertEqual(failing.calls, 4)

    def test_dispatch_post_is_never_retried_on_5xx(self):
        transport = SinglePostHTTPTransport(500)
        evidence = transport.post_dispatch_once(WORKFLOW_ID, {"ref": locator_mod.CANONICAL_REF, "inputs": {}})
        self.assertEqual(evidence["http_status"], 500)
        self.assertEqual(transport.calls, 1)

    def test_timeout_is_formal_run_not_found(self):
        transport = FakeTransport([[[]]])
        with self.assertRaisesRegex(locator_mod.LocatorError, "^FORMAL_RUN_NOT_FOUND$"):
            self.make_locator(transport).locate(
                workflow_id=WORKFLOW_ID,
                before_ids=set(),
                request_sha=REQ,
                canonical_sha=CANON,
                dispatch_started_at=START,
            )

    def test_reservation_path_dispatches_exactly_once_while_locator_polls(self):
        candidate = run_record(34221501889, created_at="2099-01-01T00:00:00Z")
        transport = FakeTransport([[[]], [[]], [[candidate]]])
        run, http, evidence = self.make_locator(transport, delays=(0, 0)).dispatch_once_and_locate(
            workflow_id=WORKFLOW_ID,
            before_ids=set(),
            request_sha=REQ,
            canonical_sha=CANON,
        )
        self.assertEqual(run["id"], 34221501889)
        self.assertEqual(transport.post_count, 1)
        self.assertEqual(http["http_status"], 204)
        self.assertEqual(transport.last_payload["inputs"]["expected_request_sha256"], REQ)
        self.assertEqual(evidence["poll_count"], 3)

    def test_located_run_is_reread_and_revalidated(self):
        candidate = run_record(34221501889)
        reread_wrong = run_record(34221501889, head_sha="2" * 40)
        transport = FakeTransport([[[candidate]]], reread=reread_wrong)
        with self.assertRaisesRegex(locator_mod.LocatorError, "FORMAL_RUN_REVALIDATION_FAILED"):
            self.make_locator(transport).locate(
                workflow_id=WORKFLOW_ID,
                before_ids=set(),
                request_sha=REQ,
                canonical_sha=CANON,
                dispatch_started_at=START,
            )

    def test_created_at_before_dispatch_baseline_is_rejected(self):
        stale = run_record(48, created_at="2026-09-08T11:35:28Z")
        transport = FakeTransport([[[stale]]])
        with self.assertRaisesRegex(locator_mod.LocatorError, "FORMAL_RUN_NOT_FOUND"):
            self.make_locator(transport).locate(
                workflow_id=WORKFLOW_ID,
                before_ids=set(),
                request_sha=REQ,
                canonical_sha=CANON,
                dispatch_started_at=START,
            )

    def test_trusted_workflow_materializes_exact_main_locator_and_uses_it(self):
        text = TRUSTED.read_text(encoding="utf-8")
        self.assertIn("git show \"${GITHUB_SHA}:governance/football3/formal_dispatch_run_locator_v1.py\"", text)
        self.assertIn(".runtime_sources/formal_dispatch_run_locator_v1.py dispatch", text)
        dispatch_step = text.split("- name: Dispatch formal SHA-bound runner and locate new production Run ID", 1)[1].split("- name: Wait for formal terminal state", 1)[0]
        self.assertNotIn("auto_dispatch_bridge_v1.py dispatch", dispatch_step)
        self.assertIn("persist-credentials: false", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
