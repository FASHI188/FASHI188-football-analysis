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

    def test_k1_ics_uid_identity_ignores_description_and_kickoff_revision(self):
        aliases=["Bucheon 1995","Sangju Sangmu"]
        def feed(kickoff):
            return ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:k1-abc@example\r\nDTSTART;TZID=Asia/Seoul:"+kickoff+"\r\nSUMMARY:Bucheon 1995 - Sangju Sangmu\r\nDESCRIPTION:POISON SCORE 9-9\r\nLOCATION:Bucheon Stadium\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n").encode()
        a=m.parse_k1_ics(feed("20260919T163000"),season="2026",default_timezone="Asia/Seoul",observed_at=OBS,team_aliases=aliases)[0]
        b=m.parse_k1_ics(feed("20260919T170000"),season="2026",default_timezone="Asia/Seoul",observed_at=OBS,team_aliases=aliases)[0]
        self.assertEqual(a["fixture_id"],b["fixture_id"]); self.assertEqual(a["source_uid"],"k1-abc@example"); self.assertNotIn("description",a); self.assertNotEqual(a["kickoff"],b["kickoff"])

    def test_k1_ics_rejects_unknown_team_alias(self):
        raw=("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:x\nDTSTART:20261001T100000Z\nSUMMARY:Unknown FC - Ulsan\nEND:VEVENT\nEND:VCALENDAR\n").encode()
        with self.assertRaises(m.PrecheckError):
            m.parse_k1_ics(raw,season="2026",default_timezone="Asia/Seoul",observed_at=OBS,team_aliases=["Ulsan"])

    def test_unique_fixture_guard(self):
        with self.assertRaises(m.PrecheckError): m.require_unique([{"fixture_id":"x"},{"fixture_id":"x"}],"x")

    def test_config_never_activates_replay(self):
        c={"status":"PRECHECK_SOURCE_ROUTES_NOT_ENABLED","activation":{"enabled":False,"replay_coverage_start_at":None},"global_contract":{"secret_required":False,"result_fields_read":0,"formal_v2_changed":False,"current_changed":False,"production_changed":False,"candidate_weight":0,"matrix_delta":0,"competitions":["EPL","La_liga","Bundesliga","Serie_A","Ligue_1","J1","K1"]}}
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"c.json"; p.write_text(json.dumps(c),encoding="utf-8")
            x=m.load_routes(p); self.assertFalse(x["activation"]["enabled"]); self.assertIsNone(x["activation"]["replay_coverage_start_at"])

if __name__=="__main__": unittest.main()
