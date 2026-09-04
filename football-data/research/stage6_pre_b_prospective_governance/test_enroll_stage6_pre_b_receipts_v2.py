from __future__ import annotations

import unittest
from datetime import datetime, timezone

import enroll_stage6_pre_b_receipts_v2 as m


class QueueOrderRegressionTests(unittest.TestCase):
    def test_same_kickoff_group_keeps_frozen_queue_order_not_mid_order(self):
        ko = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
        fixtures = [
            {
                "fixture_identity_sha256": "id-b",
                "understat_match_id": 20,
                "competition": "EPL",
                "home_team": "H2",
                "away_team": "A2",
                "scheduled_kickoff_utc": "2026-09-05T12:00:00Z",
            },
            {
                "fixture_identity_sha256": "id-a",
                "understat_match_id": 10,
                "competition": "EPL",
                "home_team": "H1",
                "away_team": "A1",
                "scheduled_kickoff_utc": "2026-09-05T12:00:00Z",
            },
        ]
        future = [
            {"mid": 10, "league": "EPL", "home_team": "H1", "away_team": "A1", "kickoff": ko},
            {"mid": 20, "league": "EPL", "home_team": "H2", "away_team": "A2", "kickoff": ko},
        ]
        out = m.map_queue_to_future(fixtures, future)
        self.assertEqual([q["fixture_identity_sha256"] for q, _ in out], ["id-b", "id-a"])
        self.assertEqual([r["mid"] for _, r in out], [20, 10])


if __name__ == "__main__":
    unittest.main()
