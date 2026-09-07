#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import threading
import unittest
import urllib.error
from email.message import Message
from unittest import mock

import durable_state_selector_v1 as selector
import runtime as rt


class DurableSelectorGithubApiResilienceTests(unittest.TestCase):
    def setUp(self) -> None:
        selector._clear_process_cache_for_tests()

    @staticmethod
    def _response(payload: dict) -> io.BytesIO:
        return io.BytesIO(json.dumps(payload).encode("utf-8"))

    @staticmethod
    def _http_error(url: str, code: int, message: str, *, documentation_url: str = "https://docs.github.com/rest/using-the-rest-api/rate-limits-for-the-rest-api", headers: dict[str, str] | None = None) -> urllib.error.HTTPError:
        hdr = Message()
        for key, value in (headers or {}).items():
            hdr[key] = value
        body = io.BytesIO(json.dumps({
            "message": message,
            "documentation_url": documentation_url,
        }).encode("utf-8"))
        return urllib.error.HTTPError(url, code, message, hdr, body)

    def test_transient_secondary_403_recovers_and_honors_retry_after(self) -> None:
        url = "https://api.github.com/repos/o/r/actions/runs/1"
        calls = []

        def opener(req):
            calls.append(req.full_url)
            if len(calls) == 1:
                raise self._http_error(
                    url,
                    403,
                    "You have exceeded a secondary rate limit.",
                    headers={
                        "Retry-After": "0",
                        "X-RateLimit-Limit": "1000",
                        "X-RateLimit-Remaining": "812",
                        "X-RateLimit-Reset": "9999999999",
                        "X-RateLimit-Resource": "core",
                    },
                )
            return self._response({"id": 1, "status": "completed", "conclusion": "success"})

        with mock.patch.object(selector.urllib.request, "urlopen", side_effect=opener), \
             mock.patch.object(selector.time, "sleep") as sleep:
            result = selector._get_json(url, "token")
        self.assertEqual(result["id"], 1)
        self.assertEqual(len(calls), 2)
        sleep.assert_called_once_with(0.0)

    def test_primary_rate_limit_403_recovers_when_remaining_zero(self) -> None:
        url = "https://api.github.com/repos/o/r/actions/runs/2"
        calls = 0

        def opener(req):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise self._http_error(
                    url,
                    403,
                    "API rate limit exceeded for installation ID 1.",
                    headers={
                        "X-RateLimit-Limit": "1000",
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": "1",
                        "X-RateLimit-Resource": "core",
                    },
                )
            return self._response({"id": 2})

        with mock.patch.object(selector.urllib.request, "urlopen", side_effect=opener), \
             mock.patch.object(selector.time, "sleep") as sleep:
            result = selector._get_json(url, "token")
        self.assertEqual(result["id"], 2)
        self.assertEqual(calls, 2)
        self.assertEqual(sleep.call_count, 1)

    def test_429_recovers_with_bounded_retry(self) -> None:
        url = "https://api.github.com/repos/o/r/actions/runs/3"
        calls = 0

        def opener(req):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise self._http_error(url, 429, "Too many requests", headers={"Retry-After": "0"})
            return self._response({"id": 3})

        with mock.patch.object(selector.urllib.request, "urlopen", side_effect=opener), \
             mock.patch.object(selector.time, "sleep"):
            self.assertEqual(selector._get_json(url, "token")["id"], 3)
        self.assertEqual(calls, 2)

    def test_permission_403_fails_closed_without_retry(self) -> None:
        url = "https://api.github.com/repos/o/r/actions/runs/4"
        calls = 0

        def opener(req):
            nonlocal calls
            calls += 1
            raise self._http_error(
                url,
                403,
                "Resource not accessible by integration",
                documentation_url="https://docs.github.com/rest/actions/workflow-runs#get-a-workflow-run",
                headers={
                    "X-RateLimit-Limit": "1000",
                    "X-RateLimit-Remaining": "999",
                    "X-RateLimit-Resource": "core",
                },
            )

        with mock.patch.object(selector.urllib.request, "urlopen", side_effect=opener), \
             mock.patch.object(selector.time, "sleep") as sleep:
            with self.assertRaises(rt.RuntimeGateError) as ctx:
                selector._get_json(url, "token")
        self.assertIn("Resource not accessible by integration", str(ctx.exception))
        self.assertEqual(calls, 1)
        sleep.assert_not_called()

    def test_duplicate_concurrent_gets_collapse_to_one_request(self) -> None:
        url = "https://api.github.com/repos/o/r/actions/runs/5"
        call_count = 0
        call_lock = threading.Lock()
        results = []
        failures = []

        def opener(req):
            nonlocal call_count
            with call_lock:
                call_count += 1
            return self._response({"id": 5, "status": "completed"})

        def worker():
            try:
                results.append(selector._get_json(url, "token"))
            except Exception as exc:  # pragma: no cover - regression diagnostics
                failures.append(exc)

        with mock.patch.object(selector.urllib.request, "urlopen", side_effect=opener):
            threads = [threading.Thread(target=worker) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertFalse(failures)
        self.assertEqual(len(results), 8)
        self.assertEqual(call_count, 1)

    def test_cache_reset_preserves_independent_selector_process_semantics(self) -> None:
        url = "https://api.github.com/repos/o/r/actions/runs/6"
        calls = 0

        def opener(req):
            nonlocal calls
            calls += 1
            return self._response({"id": 6})

        with mock.patch.object(selector.urllib.request, "urlopen", side_effect=opener):
            selector._get_json(url, "token")
            selector._get_json(url, "token")
            self.assertEqual(calls, 1)
            selector._clear_process_cache_for_tests()
            selector._get_json(url, "token")
        self.assertEqual(calls, 2)

    def test_artifact_pagination_and_deduplication(self) -> None:
        page1 = [{"id": i, "created_at": f"2026-09-01T00:{i % 60:02d}:00Z"} for i in range(1, 101)]
        page2 = [
            {"id": 100, "created_at": "2026-09-01T01:00:00Z"},
            {"id": 101, "created_at": "2026-09-02T00:00:00Z"},
        ]

        def fake_get(url, token):
            if "page=1" in url:
                return {"artifacts": page1}
            if "page=2" in url:
                return {"artifacts": page2}
            self.fail(url)

        with mock.patch.object(selector, "_get_json", side_effect=fake_get) as get:
            rows = selector._artifact_pages("o/r", "token")
        self.assertEqual(len(rows), 101)
        self.assertEqual(len({row["id"] for row in rows}), 101)
        self.assertEqual(get.call_count, 2)

    def test_run_metadata_is_batch_read_and_duplicate_ids_are_not_individually_queried(self) -> None:
        urls = []

        def fake_get(url, token):
            urls.append(url)
            if "/actions/runs?" in url:
                return {"workflow_runs": [
                    {"id": 10, "status": "completed", "conclusion": "success"},
                    {"id": 11, "status": "completed", "conclusion": "success"},
                ]}
            self.fail(f"unexpected individual run lookup: {url}")

        with mock.patch.object(selector, "_get_json", side_effect=fake_get):
            rows = selector._run_records("o/r", "token", {10, 10, 11})
        self.assertEqual(set(rows), {10, 11})
        self.assertEqual(len(urls), 1)
        self.assertIn("/actions/runs?per_page=100&page=1", urls[0])

    def test_stable_candidate_order_is_deterministic_under_input_shuffle(self) -> None:
        a = {"artifact_id": 1, "artifact_created_at": "2026-09-01T00:00:00Z", "_bundle_dir": "/a"}
        b = {"artifact_id": 2, "artifact_created_at": "2026-09-02T00:00:00Z", "_bundle_dir": "/b"}
        c = {"artifact_id": 3, "artifact_created_at": "2026-09-02T00:00:00Z", "_bundle_dir": "/c"}
        expected = selector._stable_public_candidates([a, b, c])
        self.assertEqual(expected, selector._stable_public_candidates([c, a, b]))
        self.assertEqual([row["artifact_id"] for row in expected], [3, 2, 1])
        self.assertTrue(all("_bundle_dir" not in row for row in expected))


if __name__ == "__main__":
    unittest.main(verbosity=2)
