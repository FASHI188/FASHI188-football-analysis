from __future__ import annotations

import gzip
import unittest
from datetime import datetime, timezone

from understat_deep_ppda_preflight import (
    decode_http_body,
    fixture_identity_sha,
    parse_dt,
    select_atomic_queue,
    validate_history_row,
)


class Stage6PreBSourcePreflightTests(unittest.TestCase):
    def test_gzip_decode(self):
        raw = gzip.compress(b'{"ok":true}')
        self.assertEqual(decode_http_body(raw, "gzip"), b'{"ok":true}')
        self.assertEqual(decode_http_body(raw, ""), b'{"ok":true}')

    def test_datetime_parse_is_utc(self):
        got = parse_dt("2026-09-05 18:30:00")
        self.assertEqual(got, datetime(2026, 9, 5, 18, 30, tzinfo=timezone.utc))

    def test_history_semantics(self):
        got = validate_history_row({
            "date": "2026-08-30 15:00:00",
            "deep": 9,
            "ppda": {"att": 102, "def": 12},
        })
        self.assertEqual(got["deep"], 9.0)
        self.assertEqual(got["ppda_att"], 102.0)
        self.assertEqual(got["ppda_def"], 12.0)
        self.assertAlmostEqual(got["ppda_ratio"], 8.5)

    def test_missing_ppda_fails_closed(self):
        with self.assertRaises(RuntimeError):
            validate_history_row({"date": "2026-08-30 15:00:00", "deep": 9})
        with self.assertRaises(RuntimeError):
            validate_history_row({
                "date": "2026-08-30 15:00:00",
                "deep": 9,
                "ppda": {"att": 102},
            })

    def test_same_kickoff_boundary_is_atomic(self):
        fixtures = [
            {"scheduled_kickoff_utc": "2026-09-05T12:00:00Z", "competition": "EPL", "home_team": "A", "away_team": "B"},
            {"scheduled_kickoff_utc": "2026-09-05T15:00:00Z", "competition": "EPL", "home_team": "C", "away_team": "D"},
            {"scheduled_kickoff_utc": "2026-09-05T15:00:00Z", "competition": "Serie_A", "home_team": "E", "away_team": "F"},
            {"scheduled_kickoff_utc": "2026-09-06T12:00:00Z", "competition": "EPL", "home_team": "G", "away_team": "H"},
        ]
        got = select_atomic_queue(fixtures, 2)
        self.assertEqual(len(got), 3)
        self.assertTrue(all(x["scheduled_kickoff_utc"] <= "2026-09-05T15:00:00Z" for x in got))

    def test_fixture_identity_hash_is_canonical(self):
        a = {
            "competition": "EPL",
            "season": "2026/27",
            "home_team": "Alpha",
            "away_team": "Beta",
            "scheduled_kickoff_utc": "2026-09-05T15:00:00Z",
        }
        b = dict(reversed(list(a.items())))
        self.assertEqual(fixture_identity_sha(a), fixture_identity_sha(b))
        self.assertEqual(len(fixture_identity_sha(a)), 64)


if __name__ == "__main__":
    unittest.main()
