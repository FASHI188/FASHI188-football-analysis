#!/usr/bin/env python3
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import current_v2_retrospective_replay_v1 as replay
import current_v2_retrospective_utc_day_boundary_v1 as boundary

FOOTBALL3_GOVERNED_RESEARCH_REPLAY = "football3-current-formal-retrospective-research-replay-v1"


class UtcDayHistoryBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.original = replay._safe_history_upper
        boundary.install(replay)

    def tearDown(self):
        replay._safe_history_upper = self.original

    def test_utc_target_returns_same_utc_date_midnight(self):
        target = datetime(2026, 9, 7, 18, 45, tzinfo=timezone.utc)
        self.assertEqual(replay._safe_history_upper(target), datetime(2026, 9, 7, tzinfo=timezone.utc))

    def test_positive_offset_is_normalized_to_utc_before_date_floor(self):
        target = datetime(2026, 9, 8, 1, 30, tzinfo=timezone(timedelta(hours=9)))
        self.assertEqual(replay._safe_history_upper(target), datetime(2026, 9, 7, tzinfo=timezone.utc))

    def test_negative_offset_is_normalized_to_utc_before_date_floor(self):
        target = datetime(2026, 9, 7, 23, 30, tzinfo=timezone(timedelta(hours=-4)))
        self.assertEqual(replay._safe_history_upper(target), datetime(2026, 9, 8, tzinfo=timezone.utc))

    def test_midnight_target_is_stable(self):
        target = datetime(2026, 9, 7, tzinfo=timezone.utc)
        self.assertEqual(replay._safe_history_upper(target), target)

    def test_naive_datetime_fails_closed(self):
        with self.assertRaises(replay.rt.RuntimeGateError):
            replay._safe_history_upper(datetime(2026, 9, 7, 18, 45))

    def test_target_utc_day_is_excluded_and_previous_utc_date_remains_eligible(self):
        target = datetime(2026, 9, 7, 18, 45, tzinfo=timezone.utc)
        upper = replay._safe_history_upper(target)
        records = [
            datetime(2026, 9, 6, 23, 59, 59, tzinfo=timezone.utc),
            datetime(2026, 9, 7, 0, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 9, 7, 12, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 9, 7, 23, 59, 59, tzinfo=timezone.utc),
        ]
        eligible = [x for x in records if x < upper]
        self.assertEqual(eligible, [records[0]])

    def test_install_changes_only_research_history_boundary(self):
        original_rows = object()
        original_xg = object()
        module = SimpleNamespace(
            _safe_history_upper=object(),
            _research_v1_rows=original_rows,
            _current_xg_labels=original_xg,
        )
        audit = boundary.install(module)
        self.assertIs(module._safe_history_upper, boundary.safe_history_upper)
        self.assertIs(module._research_v1_rows, original_rows)
        self.assertIs(module._current_xg_labels, original_xg)
        self.assertTrue(audit["target_utc_day_excluded"])
        self.assertEqual(audit["naive_datetime_policy"], "FAIL_CLOSED")
        self.assertFalse(audit["target_fixture_kickoff_changed"])
        self.assertFalse(audit["prospective_path_changed"])
        self.assertFalse(audit["strict_pit_path_changed"])

    def test_entry_reasserts_utc_day_boundary_after_exact_history_install(self):
        entry = Path(__file__).with_name("entry.py").read_text(encoding="utf-8")
        exact_pos = entry.index("current_v2_retrospective_exact_history_v1.install(current_v2_retrospective_replay_v1)")
        boundary_pos = entry.index("current_v2_retrospective_utc_day_boundary_v1.install(current_v2_retrospective_replay_v1)")
        replay_pos = entry.index("current_v2_retrospective_replay_v1.install(gateway)")
        self.assertLess(exact_pos, boundary_pos)
        self.assertLess(boundary_pos, replay_pos)


if __name__ == "__main__":
    unittest.main()
