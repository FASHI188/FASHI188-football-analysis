#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "research" / "build_epl2425_goal_event_chain.py"
SPEC = importlib.util.spec_from_file_location("r34_policy_2425_goal_chain", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class GoalChainParserTests(unittest.TestCase):
    def test_football_period_ordering(self) -> None:
        self.assertEqual(MODULE.parse_minute("45+6"), (45, 6, 1, "1:04506"))
        self.assertEqual(MODULE.parse_minute("46"), (46, 0, 2, "2:04600"))
        self.assertEqual(MODULE.parse_minute("90+10"), (90, 10, 2, "2:09010"))

    def test_split_multi_goal_cell_and_replay(self) -> None:
        match = {
            "matchId": "fixture-1",
            "goals": [
                "10' Home One (1-0)<br>45+2' Away One (1-1)",
                "46' Home Two (2-1)<br>90+5' Away Two (2-2)",
            ],
        }
        events = MODULE.parse_match_events(match, 2, 2)
        self.assertEqual([event.scoring_side for event in events], ["H", "A", "H", "A"])
        self.assertEqual(events[-1].home_score_after, 2)
        self.assertEqual(events[-1].away_score_after, 2)

    def test_zero_zero_is_valid_empty_chain(self) -> None:
        match = {"matchId": "fixture-0", "goals": ["", "", ""]}
        self.assertEqual(MODULE.parse_match_events(match, 0, 0), [])

    def test_nonzero_score_without_events_fails(self) -> None:
        match = {"matchId": "fixture-bad", "goals": ["", ""]}
        with self.assertRaisesRegex(ValueError, "event count 0 != final goals 1"):
            MODULE.parse_match_events(match, 1, 0)

    def test_invalid_two_goal_jump_fails(self) -> None:
        match = {"matchId": "fixture-jump", "goals": ["10' Home (2-0)"]}
        with self.assertRaisesRegex(ValueError, "invalid score transition"):
            MODULE.parse_match_events(match, 2, 0)

    def test_git_blob_sha_changes_with_payload(self) -> None:
        self.assertNotEqual(MODULE.git_blob_sha1(b"[]"), MODULE.git_blob_sha1(b"[ ]"))


if __name__ == "__main__":
    unittest.main()
