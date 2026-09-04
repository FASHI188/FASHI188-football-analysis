from __future__ import annotations

import json
import pathlib
import unittest

HERE = pathlib.Path(__file__).resolve().parent
CONTRACT = HERE / "MW3_PROSPECTIVE_SHADOW_CONTRACT.json"
FIXTURES = HERE / "ENG_PL_2026_27_MW3_FIXTURE_FREEZE.json"
COLLECTOR = HERE / "collect_mw3_pit_snapshot.py"

FROZEN_V311 = "a90762a97515f3edd564e8ad204db0d0d4231494"
FORMAL_V2 = "e12f5d1193be5d81f60301cf34ab2140e11712a9"


class ProspectiveMw3SnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        cls.fixtures = json.loads(FIXTURES.read_text(encoding="utf-8"))
        cls.collector = COLLECTOR.read_text(encoding="utf-8")

    def test_exact_lineage_and_frozen_status(self):
        self.assertEqual(self.contract["status"], "FROZEN_BEFORE_ANY_MW3_TARGET_KICKOFF")
        self.assertEqual(self.contract["clean_parent_head"], FROZEN_V311)
        self.assertEqual(self.contract["formal_v2_head"], FORMAL_V2)
        self.assertTrue(self.contract["research_only"])
        self.assertEqual(self.contract["formal_weight"], 0)

    def test_forbidden_target_access_is_closed(self):
        gov = self.contract["governance"]
        forbidden = (
            "target_result_access",
            "target_score_access",
            "target_confirmed_xi_access",
            "target_postmatch_event_access",
            "market_or_odds_access",
            "retrospective_availability_backfill",
            "historical_final_xi_backfill_as_current_xi",
            "formal_model_change_allowed",
            "formal_probability_change_allowed",
            "CURRENT_change_allowed",
            "production_pointer_change_allowed",
            "formal_weights_change_allowed",
            "2023_confirmation_set_access",
            "3504_access",
        )
        for key in forbidden:
            self.assertIs(gov[key], False, key)

    def test_fixture_cohort_is_exactly_ten_matches_twenty_teams(self):
        rows = self.fixtures["fixtures"]
        codes = self.fixtures["expected_team_short_names"]
        self.assertEqual(len(rows), 10)
        self.assertEqual(len(codes), 20)
        self.assertEqual(len(set(codes)), 20)
        used = {r["home_short"] for r in rows} | {r["away_short"] for r in rows}
        self.assertEqual(used, set(codes))
        self.assertIs(self.fixtures["label_access"], False)
        self.assertIs(self.fixtures["confirmed_xi_access"], False)
        self.assertIs(self.fixtures["market_access"], False)

    def test_all_kickoffs_follow_contract_freeze(self):
        from datetime import datetime
        freeze = datetime.fromisoformat(self.contract["frozen_at_utc"].replace("Z", "+00:00"))
        kicks = [datetime.fromisoformat(r["kickoff_at_utc"].replace("Z", "+00:00")) for r in self.fixtures["fixtures"]]
        self.assertTrue(all(k > freeze for k in kicks))
        self.assertEqual(min(kicks).isoformat(), "2026-09-04T19:00:00+00:00")

    def test_mandatory_source_is_official_fpl_only(self):
        mandatory = self.contract["public_sources"]["mandatory"]
        self.assertEqual(len(mandatory), 1)
        self.assertEqual(mandatory[0]["url"], "https://fantasy.premierleague.com/api/bootstrap-static/")
        self.assertEqual(mandatory[0]["tier"], "OFFICIAL_LEAGUE_STRUCTURED_PUBLIC")
        allowed = set(mandatory[0]["allowed_fields"])
        self.assertNotIn("fixtures", allowed)
        self.assertNotIn("event", allowed)
        self.assertNotIn("score", allowed)
        self.assertNotIn("lineup", allowed)

    def test_collector_has_no_target_result_xi_or_market_fetch_route(self):
        # Fixed network endpoints are the only URL literals used by the collector.
        urls = [line.strip() for line in self.collector.splitlines() if line.strip().startswith(("FPL_BOOTSTRAP =", "PL_INJURIES ="))]
        self.assertEqual(len(urls), 2)
        lowered = self.collector.lower()
        for forbidden_url_token in (
            "/fixtures?",
            "/lineups",
            "odds-api",
            "the-odds-api",
            "betfair",
            "flashscore",
        ):
            self.assertNotIn(forbidden_url_token, lowered)
        self.assertIn('"target_result_access": False', self.collector)
        self.assertIn('"target_confirmed_xi_access": False', self.collector)
        self.assertIn('"market_access": False', self.collector)

    def test_temporal_stop_precedes_source_fetch(self):
        validation_pos = self.collector.index("validate_freezes(contract, fixtures, snapshot_start)")
        stop_pos = self.collector.index("if violations:", validation_pos)
        source_defs_pos = self.collector.index("source_defs =", stop_pos)
        self.assertLess(validation_pos, stop_pos)
        self.assertLess(stop_pos, source_defs_pos)
        self.assertIn("SNAPSHOT_STARTED_AT_OR_AFTER_EARLIEST_KICKOFF", self.collector)

    def test_terminal_statuses_are_frozen(self):
        terminal = self.contract["terminal"]
        self.assertEqual(terminal["snapshot_pass"], "MW3_PROSPECTIVE_PIT_SNAPSHOT_LOCKED_WAITING_FOR_FUTURE_XI_LABELS")
        self.assertEqual(terminal["snapshot_stop"], "MW3_PROSPECTIVE_PIT_SNAPSHOT_STOPPED_NO_TARGET_SCORING")
        self.assertIn(terminal["snapshot_pass"], self.collector)
        self.assertIn(terminal["snapshot_stop"], self.collector)


if __name__ == "__main__":
    unittest.main()
