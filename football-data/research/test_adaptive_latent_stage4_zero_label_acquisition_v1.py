#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# Minimal local copy of the already-published identity-lock contract is supplied by
# the test harness workspace. In-repository runs import the real sibling module.
import adaptive_latent_stage4_zero_label_acquisition_v1 as s4


def make_event(competition_id: str, event_id: int, kickoff: datetime) -> dict:
    spec = s4.COMPETITIONS[competition_id]
    return {
        "id": event_id,
        "status": {"type": "notstarted"},
        "startTimestamp": int(kickoff.timestamp()),
        "tournament": {"uniqueTournament": {"id": spec["tournament_id"]}},
        "season": {"id": spec["season_id"], "year": spec["season_year"]},
        "homeTeam": {"id": event_id * 10 + 1, "name": f"Home {event_id}"},
        "awayTeam": {"id": event_id * 10 + 2, "name": f"Away {event_id}"},
    }


class Stage4ZeroLabelTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 22, 16, 0, tzinfo=timezone.utc)
        os.environ["GITHUB_RUN_ID"] = "123456789"
        os.environ["GITHUB_RUN_ATTEMPT"] = "1"

    def observation(self, competition_id: str, count: int = 5, *, add_forbidden: bool = False):
        base = list(s4.COMPETITIONS).index(competition_id) * 1000 + 100
        events = [
            make_event(competition_id, base + i, self.now + timedelta(days=2, hours=i))
            for i in range(count)
        ]
        payload = {"events": events, "hasNextPage": True}
        if add_forbidden:
            payload["events"][0]["homeScore"] = {"current": 0}
        return s4.HttpObservation(
            payload=payload,
            request_url=s4.fixture_url(
                int(s4.COMPETITIONS[competition_id]["tournament_id"]),
                int(s4.COMPETITIONS[competition_id]["season_id"]),
            ),
            payload_sha256="a" * 64,
            http_status=200,
            content_type="application/json",
            received_at=self.now,
            byte_count=100,
        )

    def fetcher(self, url: str):
        for cid, spec in s4.COMPETITIONS.items():
            if url == s4.fixture_url(int(spec["tournament_id"]), int(spec["season_id"])):
                return self.observation(cid)
        raise AssertionError(url)

    def test_happy_path_materializes_identity_only(self):
        inventory, csv_text, lock = s4.materialize_target_inventory(self.fetcher)
        self.assertEqual(inventory["target_row_count"], 25)
        self.assertEqual(set(inventory["required_competitions"]), set(s4.COMPETITIONS))
        self.assertEqual(inventory["label_fields_persisted"], 0)
        self.assertEqual(inventory["real_target_values_read"], 0)
        self.assertFalse(inventory["raw_provider_payload_persisted"])
        self.assertEqual(lock["row_count"], 25)
        self.assertEqual(csv_text.count("\n"), 26)
        for row in inventory["targets"]:
            kickoff = datetime.fromisoformat(row["kickoff_at"].replace("Z", "+00:00"))
            cutoff = datetime.fromisoformat(row["prediction_cutoff"].replace("Z", "+00:00"))
            self.assertEqual(kickoff - cutoff, timedelta(minutes=15))
            encoded = json.dumps(row, sort_keys=True).casefold()
            self.assertNotIn("homescore", encoded)
            self.assertNotIn("awayscore", encoded)
            self.assertNotIn("winnercode", encoded)


    def test_provider_event_id_reuse_across_competitions_rejected(self):
        def fetcher(url: str):
            for cid, spec in s4.COMPETITIONS.items():
                if url == s4.fixture_url(int(spec["tournament_id"]), int(spec["season_id"])):
                    obs = self.observation(cid)
                    if cid == "ESP_LaLiga":
                        # Reuse one EPL provider event id while preserving otherwise legal LaLiga semantics.
                        obs.payload["events"][0]["id"] = 100
                    return obs
            raise AssertionError(url)
        with self.assertRaisesRegex(s4.Stage4AcquisitionError, "provider event id reused"):
            s4.materialize_target_inventory(fetcher)

    def test_forbidden_score_field_rejects_whole_payload(self):
        with self.assertRaisesRegex(s4.Stage4AcquisitionError, "zero-label boundary violation"):
            s4.assert_zero_label_fixture_payload(self.observation("ENG_PremierLeague", add_forbidden=True).payload)

    def test_forbidden_result_and_winner_keys_are_default_denied(self):
        for payload in ({"events": [], "winnerCode": 1}, {"events": [], "finalResultOnly": True}):
            with self.assertRaises(s4.Stage4AcquisitionError):
                s4.assert_zero_label_fixture_payload(payload)

    def test_non_notstarted_event_rejected(self):
        event = make_event("ENG_PremierLeague", 77, self.now + timedelta(days=2))
        event["status"] = {"type": "inprogress"}
        with self.assertRaisesRegex(s4.Stage4AcquisitionError, "non-notstarted"):
            s4._event_identity(event, "ENG_PremierLeague", s4.COMPETITIONS["ENG_PremierLeague"], self.now)

    def test_tournament_mismatch_rejected(self):
        event = make_event("ENG_PremierLeague", 77, self.now + timedelta(days=2))
        event["tournament"]["uniqueTournament"]["id"] = 999
        with self.assertRaisesRegex(s4.Stage4AcquisitionError, "tournament mismatch"):
            s4._event_identity(event, "ENG_PremierLeague", s4.COMPETITIONS["ENG_PremierLeague"], self.now)

    def test_season_mismatch_rejected(self):
        event = make_event("ENG_PremierLeague", 77, self.now + timedelta(days=2))
        event["season"]["id"] = 999
        with self.assertRaisesRegex(s4.Stage4AcquisitionError, "season id mismatch"):
            s4._event_identity(event, "ENG_PremierLeague", s4.COMPETITIONS["ENG_PremierLeague"], self.now)

    def test_lead_window_excludes_too_close_and_too_far(self):
        for kickoff in (self.now + timedelta(minutes=59), self.now + timedelta(days=15)):
            event = make_event("ENG_PremierLeague", 77, kickoff)
            self.assertIsNone(s4._event_identity(event, "ENG_PremierLeague", s4.COMPETITIONS["ENG_PremierLeague"], self.now))

    def test_fixed_url_rejects_other_origin(self):
        with self.assertRaisesRegex(s4.Stage4AcquisitionError, "outside fixed SofaScore"):
            s4.fetch_json_fixed("https://example.com/api/v1/thing")

    def test_statistics_parser_extracts_exact_xg_without_result_surface(self):
        payload = {
            "statistics": [
                {
                    "period": "ALL",
                    "groups": [
                        {
                            "groupName": "Expected",
                            "statisticsItems": [
                                {"name": "Expected goals", "key": "expectedGoals", "homeValue": 1.42, "awayValue": 0.77}
                            ],
                        }
                    ],
                }
            ]
        }
        self.assertEqual(s4.extract_expected_goals_statistics(payload), (1.42, 0.77))

    def test_statistics_parser_rejects_result_fields(self):
        payload = {
            "winnerCode": 1,
            "statistics": [
                {"period": "ALL", "groups": [{"statisticsItems": [{"key": "expectedGoals", "homeValue": 1.0, "awayValue": 1.0}]}]}
            ],
        }
        with self.assertRaisesRegex(s4.Stage4AcquisitionError, "zero-label boundary violation"):
            s4.extract_expected_goals_statistics(payload)

    def test_statistics_parser_requires_exactly_one_expected_goals(self):
        with self.assertRaisesRegex(s4.Stage4AcquisitionError, "exactly once"):
            s4.extract_expected_goals_statistics({"statistics": [{"period": "ALL", "groups": []}]})

    def test_response_observation_semantics_use_received_timestamp(self):
        source = Path(s4.__file__).read_text(encoding="utf-8")
        read_pos = source.index("raw = response.read()")
        stamp_pos = source.index("received_at = _utcnow()")
        self.assertLess(read_pos, stamp_pos)

    def test_live_main_has_no_xg_statistics_network_call(self):
        source = Path(s4.__file__).read_text(encoding="utf-8")
        main_source = source[source.index("def _write_outputs"):]
        self.assertNotIn("statistics_url(", main_source)
        self.assertIn('"xg_live_provider_call_performed": False', main_source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
