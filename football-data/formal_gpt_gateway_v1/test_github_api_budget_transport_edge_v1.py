#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import time
import unittest
import urllib.error
import urllib.request
from email.message import Message
from unittest import mock

import durable_state_contract_v1 as contract
import github_api_budget_transport_v1 as transport
import runtime as rt


class Response(io.BytesIO):
    def __init__(self, obj, url=""):
        super().__init__(json.dumps(obj).encode())
        self.headers = Message()
        self.status = self.code = 200
        self.url = url
    def geturl(self): return self.url
    def __enter__(self): return self
    def __exit__(self, *args): self.close(); return False


def rate_error(url: str, reset: int):
    headers = Message()
    headers["X-RateLimit-Limit"] = "5000"
    headers["X-RateLimit-Remaining"] = "0"
    headers["X-RateLimit-Reset"] = str(reset)
    headers["X-RateLimit-Resource"] = "core"
    body = io.BytesIO(json.dumps({"message": "API rate limit exceeded for installation."}).encode())
    return urllib.error.HTTPError(url, 403, "Forbidden", headers, body)


class EdgeTests(unittest.TestCase):
    def setUp(self):
        transport.reset_for_tests()
        transport.install()

    def test_primary_reset_inside_cap_waits_once_then_recovers(self):
        url = "https://api.github.com/repos/o/r/actions/runs/123"
        calls = 0
        now = 1000.0
        def original(req, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise rate_error(url, 1005)
            return Response({"id": 123}, url)
        with mock.patch.object(transport, "_ORIGINAL_URLOPEN", side_effect=original), \
             mock.patch.object(transport.time, "time", return_value=now), \
             mock.patch.object(transport.time, "sleep") as sleep:
            payload = json.load(urllib.request.urlopen(urllib.request.Request(url)))
        self.assertEqual(payload["id"], 123)
        self.assertEqual(calls, 2)
        sleep.assert_called_once_with(5.0)

    def test_non_actions_url_is_transparent_and_outside_budget(self):
        url = "https://example.com/source.json"
        calls = 0
        def original(req, *args, **kwargs):
            nonlocal calls
            calls += 1
            return Response({"ok": True}, url)
        with mock.patch.object(transport, "_ORIGINAL_URLOPEN", side_effect=original):
            self.assertTrue(json.load(urllib.request.urlopen(urllib.request.Request(url)))["ok"])
        self.assertEqual(calls, 1)
        self.assertEqual(transport.snapshot()["network_request_count"], 0)

    def test_candidate_shuffle_keeps_same_formal_selection(self):
        cutoff = rt._parse_dt("2026-09-04T13:19:44+00:00", "cutoff")
        def row(artifact_id: int, state_cutoff: str, created_at: str):
            return {
                "artifact_id": artifact_id,
                "artifact_name": f"formal-gpt-runner-state-{artifact_id}",
                "artifact_created_at": created_at,
                "artifact_role_ok": True,
                "verified": True,
                "schema_ok": True,
                "runtime_ok": True,
                "model_current_ok": True,
                "competition_scope_ok": True,
                "pit_ok": True,
                "competition_id": "ENG_PremierLeague",
                "state_cutoff": state_cutoff,
            }
        rows = [
            row(1, "2026-09-01T00:00:00+00:00", "2026-09-01T00:00:01Z"),
            row(2, "2026-09-04T13:19:44+00:00", "2026-09-04T13:19:45Z"),
            row(3, "2026-09-04T13:19:44+00:00", "2026-09-04T13:19:46Z"),
        ]
        a, _ = contract.choose_candidate(rows, cutoff, "ENG_PremierLeague")
        b, _ = contract.choose_candidate([rows[2], rows[0], rows[1]], cutoff, "ENG_PremierLeague")
        self.assertEqual(a["artifact_id"], 3)
        self.assertEqual(b["artifact_id"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
