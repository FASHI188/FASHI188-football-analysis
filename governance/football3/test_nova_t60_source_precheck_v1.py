#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
P = HERE / "nova_t60_source_precheck_v1.py"
spec = importlib.util.spec_from_file_location("m", P)
m = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(m)
OBS = datetime(2026, 9, 16, 0, 0, tzinfo=timezone.utc)

class SourcePrecheckTests(unittest.TestCase):
    def test_openfootball_ignores_score_poison(self):
        raw = json.dumps({"matches":[{"round":"R9","date":"2026-10-01","time":"20:00","team1":"A","team2":"B","score":{"ft":object().__repr__()}}]}).encode()
        xs=m.parse_openfootball_payload(raw,competition="EPL",season="2026/27",timezone_name="Europe/London",observed_at=OBS)
        self.assertEqual(len(xs),1); self.assertNotIn("score",xs[0])

    def test_openfootball_stable_id_ignores_kickoff(self):
        a={"round":"R1","date":"2026-10-01","time":"19:00","team1":"A","team2":"B"}
        b=dict(a); b["time"]="20:00"
        self.assertEqual(m.stable_openfootball_id("EPL","2026/27",a),m.stable_openfootball_id("EPL","2026/27",b))

    def test_j1_ignores_score_visitors_other(self):
        raw=("year,category,term,date,kickoffdate,homeTeam,score,awayTeam,stadiumName,visitors,other,homeTeamId,awayTeamId,competitionName,jLeagueCompetitionId,competitionId\n"
             "2026,1,R3,2026/10/10,2026-10-10T19:00:00+09:00,H,POISON,A,S,POISON,POISON,10,20,J1,,\n").encode()
        xs=m.parse_j1_csv(raw,observed_at=OBS)
        self.assertEqual(len(xs),1); self.assertNotIn("score",xs[0]); self.assertNotIn("visitors",xs[0]); self.assertNotIn("other",xs[0])

    def test_j1_stable_id_ignores_kickoff(self):
        a={"year":"2026","term":"R3","homeTeamId":"10","awayTeamId":"20","kickoffdate":"x"}
        b=dict(a); b["kickoffdate"]="y"
        self.assertEqual(m.stable_j1_id(a),m.stable_j1_id(b))

    def test_k1_uses_id_event_and_ignores_scores(self):
        raw=json.dumps({"events":[{"idEvent":"999","idLeague":"4689","strSeason":"2026","idHomeTeam":"138115","idAwayTeam":"138111","strHomeTeam":"FC Seoul","strAwayTeam":"Jeonbuk","strTimestamp":"2026-10-04T10:00:00Z","intHomeScore":"99","intAwayScore":"99","strStatus":"Match Finished"}]}).encode()
        xs=m.parse_k1_response(raw,expected_team_id="138115",league_id="4689",season="2026",observed_at=OBS)
        self.assertEqual(xs[0]["fixture_id"],"thesportsdb:999"); self.assertNotIn("intHomeScore",xs[0]); self.assertNotIn("strStatus",xs[0])

    def test_k1_accepts_polled_team_as_away(self):
        raw=json.dumps({"events":[{"idEvent":"999","idLeague":"4689","idHomeTeam":"139783","idAwayTeam":"138113","strHomeTeam":"Bucheon","strAwayTeam":"Gimcheon","strTimestamp":"2026-10-04T10:00:00Z"}]}).encode()
        xs=m.parse_k1_response(raw,expected_team_id="138113",league_id="4689",season="2026",observed_at=OBS)
        self.assertEqual(xs[0]["away_team_id"],"138113")

    def test_unique_fixture_guard(self):
        with self.assertRaises(m.PrecheckError): m.require_unique([{"fixture_id":"x"},{"fixture_id":"x"}],"x")

    def test_config_never_activates_replay(self):
        c={"status":"PRECHECK_SOURCE_ROUTES_NOT_ENABLED","activation":{"enabled":False,"replay_coverage_start_at":None},"global_contract":{"secret_required":False,"result_fields_read":0,"formal_v2_changed":False,"current_changed":False,"production_changed":False,"candidate_weight":0,"matrix_delta":0,"competitions":["EPL","La_liga","Bundesliga","Serie_A","Ligue_1","J1","K1"]}}
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"c.json"; p.write_text(json.dumps(c),encoding="utf-8")
            x=m.load_routes(p); self.assertFalse(x["activation"]["enabled"]); self.assertIsNone(x["activation"]["replay_coverage_start_at"])

if __name__=="__main__": unittest.main()
