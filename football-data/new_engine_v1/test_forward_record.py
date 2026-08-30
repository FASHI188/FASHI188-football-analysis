from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import forward_capture_projection as projection
import forward_record as forward


class ForwardProtocolTests(unittest.TestCase):
    def test_safe_projection_strips_market_payload(self) -> None:
        capture = {
            "status": "PASS_FULL17_CAPTURE_WITH_VALID_PIT",
            "generated_at_utc": "2026-08-30T06:40:00+00:00",
            "identity_registry_path": "config/identity.json",
            "identity_registry_sha256": "a" * 64,
            "group_alias_path": "config/groups.json",
            "group_alias_sha256": "b" * 64,
            "events": [{
                "status": "VALID_KAMBI_FULL17_PIT_WRITTEN",
                "competition_id": "ENG_PremierLeague",
                "event_id": 123,
                "canonical_home": "Home",
                "canonical_away": "Away",
                "provider_start": "2026-08-31T12:00:00+00:00",
                "provider_state": "NOT_STARTED",
                "detail_observed_at_utc": "2026-08-30T06:40:00+00:00",
                "formal_snapshot_path": "evidence/markets/x.json",
                "one_x_two": {"home": 2.0, "draw": 3.0, "away": 4.0},
                "asian_handicap": {"line": 0.0},
                "over_under": {"line": 2.5},
            }],
        }
        safe = projection.project(capture, "c" * 64)
        self.assertEqual(safe["safe_event_count"], 1)
        row = safe["events"][0]
        self.assertNotIn("one_x_two", row)
        self.assertNotIn("asian_handicap", row)
        self.assertNotIn("over_under", row)
        self.assertFalse(any("odds" in key.casefold() or "prob" in key.casefold() for key in row))

    def test_t60_and_lock_are_fail_closed(self) -> None:
        lock = {"forward_not_before_utc": "2026-08-30T06:35:00+00:00"}
        base = {
            "provider_event_id": "1",
            "competition_id": "ENG_PremierLeague",
            "canonical_home": "Home",
            "canonical_away": "Away",
            "provider_state": "NOT_STARTED",
            "kickoff_utc": "2026-08-30T10:00:00+00:00",
            "observed_at_utc": "2026-08-30T08:59:59+00:00",
        }
        self.assertEqual(forward.eligibility(base, lock), (True, "eligible"))
        late = dict(base, observed_at_utc="2026-08-30T09:00:01+00:00")
        self.assertEqual(forward.eligibility(late, lock)[1], "after_t60_cutoff")
        early = dict(base, observed_at_utc="2026-08-30T06:34:59+00:00")
        self.assertEqual(forward.eligibility(early, lock)[1], "before_forward_lock")
        started = dict(base, provider_state="STARTED")
        self.assertEqual(forward.eligibility(started, lock)[1], "not_prematch")

    def test_ledger_rejects_top_level_labels(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ledger.jsonl"
            path.write_text(json.dumps({"provider_event_id": "1", "result": "H"}) + "\n", encoding="utf-8")
            with patch.object(forward, "LEDGER_PATH", path):
                with self.assertRaises(RuntimeError):
                    forward.load_ledger()

    def test_duplicate_event_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ledger.jsonl"
            rows = [
                {"provider_event_id": "1", "labels_present": False, "outcomes_read": False},
                {"provider_event_id": "1", "labels_present": False, "outcomes_read": False},
            ]
            path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            with patch.object(forward, "LEDGER_PATH", path):
                with self.assertRaises(RuntimeError):
                    forward.load_ledger()


if __name__ == "__main__":
    unittest.main()
