#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

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

class Stage4WhitelistTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 23, 0, 0, tzinfo=timezone.utc)
        os.environ["GITHUB_RUN_ID"] = "123456789"
        os.environ["GITHUB_RUN_ATTEMPT"] = "1"

    def observation(self, competition_id: str, count: int = 5):
        base = list(s4.COMPETITIONS).index(competition_id) * 1000 + 100
        payload = {
            "events": [make_event(competition_id, base+i, self.now + timedelta(days=2,hours=i)) for i in range(count)],
            "hasNextPage": False,
        }
        return s4.FixtureProjectionObservation(
            payload=payload,
            source_identity=f"synthetic:{competition_id}",
            source_url=f"https://example.invalid/{competition_id}",
            payload_sha256="a"*64,
            received_at=self.now,
        )

    def fetcher(self, cid, spec):
        return self.observation(cid)

    def test_happy_path_exact_whitelist(self):
        inv, csv_text, lock = s4.materialize_target_inventory(self.fetcher)
        self.assertEqual(inv["target_row_count"],25)
        self.assertEqual(inv["real_target_values_read"],0)
        self.assertEqual(inv["market_values_persisted"],0)
        self.assertEqual(lock["row_count"],25)
        self.assertEqual(csv_text.count("\n"),26)

    def test_unknown_top_level_key_default_denied(self):
        p=self.observation("ENG_PremierLeague").payload
        p["meta"]={}
        with self.assertRaisesRegex(s4.Stage4AcquisitionError,"unknown=.*meta"):
            s4.validate_fixture_projection(p)

    def test_unknown_event_key_default_denied(self):
        p=self.observation("ENG_PremierLeague").payload
        p["events"][0]["anythingUnexpected"]=1
        with self.assertRaisesRegex(s4.Stage4AcquisitionError,"anythingUnexpected"):
            s4.validate_fixture_projection(p)

    def test_unknown_nested_key_default_denied(self):
        p=self.observation("ENG_PremierLeague").payload
        p["events"][0]["homeTeam"]["shortName"]="H"
        with self.assertRaisesRegex(s4.Stage4AcquisitionError,"shortName"):
            s4.validate_fixture_projection(p)

    def test_score_like_field_fails_because_unknown_not_name_blacklist(self):
        p=self.observation("ENG_PremierLeague").payload
        p["events"][0]["homeScore"]={"current":0}
        with self.assertRaisesRegex(s4.Stage4AcquisitionError,"homeScore"):
            s4.validate_fixture_projection(p)

    def test_non_notstarted_denied(self):
        p=self.observation("ENG_PremierLeague").payload
        p["events"][0]["status"]["type"]="inprogress"
        with self.assertRaisesRegex(s4.Stage4AcquisitionError,"exactly notstarted"):
            s4._event_identity(p["events"][0],"ENG_PremierLeague",s4.COMPETITIONS["ENG_PremierLeague"],self.now)

    def test_tournament_mismatch_denied(self):
        p=self.observation("ENG_PremierLeague").payload
        p["events"][0]["tournament"]["uniqueTournament"]["id"]=999
        with self.assertRaisesRegex(s4.Stage4AcquisitionError,"tournament mismatch"):
            s4._event_identity(p["events"][0],"ENG_PremierLeague",s4.COMPETITIONS["ENG_PremierLeague"],self.now)

    def test_season_mismatch_denied(self):
        p=self.observation("ENG_PremierLeague").payload
        p["events"][0]["season"]["id"]=999
        with self.assertRaisesRegex(s4.Stage4AcquisitionError,"season id mismatch"):
            s4._event_identity(p["events"][0],"ENG_PremierLeague",s4.COMPETITIONS["ENG_PremierLeague"],self.now)

    def test_lead_window_excludes_outside(self):
        for kickoff in (self.now+timedelta(minutes=59), self.now+timedelta(days=15)):
            e=make_event("ENG_PremierLeague",77,kickoff)
            self.assertIsNone(s4._event_identity(e,"ENG_PremierLeague",s4.COMPETITIONS["ENG_PremierLeague"],self.now))

    def test_bad_payload_sha_denied(self):
        def f(cid,spec):
            o=self.observation(cid)
            return s4.FixtureProjectionObservation(
                payload=o.payload,
                source_identity=o.source_identity,
                source_url=o.source_url,
                payload_sha256="ABC",
                received_at=o.received_at,
            )
        with self.assertRaisesRegex(s4.Stage4AcquisitionError,"64 lowercase hex"):
            s4.materialize_target_inventory(f)

    def test_provider_event_id_reuse_denied(self):
        def f(cid,spec):
            o=self.observation(cid)
            if cid=="ESP_LaLiga":
                o.payload["events"][0]["id"]=100
                o.payload["events"][0]["homeTeam"]["id"]=1001
                o.payload["events"][0]["awayTeam"]["id"]=1002
            return o
        with self.assertRaisesRegex(s4.Stage4AcquisitionError,"provider event id reused"):
            s4.materialize_target_inventory(f)

    def test_main_is_stop_only_no_live_provider(self):
        source=Path(s4.__file__).read_text(encoding="utf-8")
        self.assertNotIn("urlopen",source)
        self.assertNotIn("urllib",source)
        self.assertNotIn("Request(",source)
        with tempfile.TemporaryDirectory() as td:
            status=s4.write_stop_status(Path(td))
            self.assertEqual(status["status"],"STOP_SOURCE_SCHEMA_WHITELIST")
            self.assertFalse(status["live_provider_call_performed"])
            self.assertEqual(status["real_labels_read"],0)

    def test_no_dangerous_name_blacklist_guard(self):
        core=(HERE/"adaptive_latent_stage4_zero_label_core_v1.py").read_text(encoding="utf-8")
        self.assertNotIn("FORBIDDEN_KEY_FRAGMENTS",core)
        self.assertNotIn("forbidden_paths",core)
        self.assertIn("exact_keys",core)
        self.assertIn("unknown=",core)

if __name__=="__main__":
    unittest.main(verbosity=2)
