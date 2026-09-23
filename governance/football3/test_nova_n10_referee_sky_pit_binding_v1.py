from __future__ import annotations

import json
import unittest
from pathlib import Path

from nova_n10_referee_sky_pit_binding_v1 import (
    cdx_query_url,
    normalize_sky_identity,
    parse_cdx,
    parse_fixture_segment,
    schedule_start,
    heading_spans,
)

REG=Path(__file__).with_name("nova_n10_referee_sky_pit_binding_registry_v1.json")


class T(unittest.TestCase):
    def test_registry_contract(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"62861d06c61b27fcd956248375d8e78d673c4536")
        self.assertEqual(p["parent_freeze"]["coverage_round_n"],38)
        rounds=[]
        for s in p["fixture_schedule_sources"]:
            rounds.extend(s["assigned_rounds"])
        self.assertEqual(sorted(rounds),list(range(1,39)))
        self.assertEqual(len(rounds),38)
        self.assertFalse(p["hard_rules"]["result_labels_read"])
        self.assertFalse(p["hard_rules"]["score_values_read"])
        self.assertFalse(p["hard_rules"]["referee_assignment_body_parsed"])
        self.assertFalse(p["pit_binding_contract"]["referee_oof_allowed"])

    def test_schedule_segment_and_fixture_parse(self):
        text=(
            "intro SERIE A TIM 2022/2023 PROGRAMMAZIONE TELEVISIVA DELLE GARE "
            "1a GIORNATA ANDATA "
            "13/08/2022 Sabato 18.30 MILAN - UDINESE ESCLUSIVA DAZN "
            "13/08/2022 Sabato 18.30 SAMPDORIA - ATALANTA DAZN + SKY "
            "13/08/2022 Sabato 20.45 LECCE - INTER DAZN + SKY "
            "13/08/2022 Sabato 20.45 MONZA - TORINO ESCLUSIVA DAZN "
            "14/08/2022 Domenica 18.30 FIORENTINA - CREMONESE ESCLUSIVA DAZN "
            "14/08/2022 Domenica 18.30 LAZIO - BOLOGNA DAZN + SKY "
            "14/08/2022 Domenica 20.45 SPEZIA - EMPOLI ESCLUSIVA DAZN "
            "14/08/2022 Domenica 20.45 SALERNITANA - ROMA ESCLUSIVA DAZN "
            "15/08/2022 Lunedì 18.30 HELLAS VERONA - NAPOLI ESCLUSIVA DAZN "
            "15/08/2022 Lunedì 20.45 JUVENTUS - SASSUOLO ESCLUSIVA DAZN "
            "2a GIORNATA ANDATA "
        )
        start=schedule_start(text,["SERIE A TIM","2022/2023","PROGRAMMAZIONE TELEVISIVA DELLE GARE"])
        spans=heading_spans(text,start)
        self.assertEqual(spans[0][0],1)
        seg=text[spans[0][1]:spans[0][2]]
        fixtures=parse_fixture_segment(seg,1,"Europe/Rome")
        self.assertEqual(len(fixtures),10)
        self.assertEqual(fixtures[0]["home"],"MILAN")
        self.assertEqual(fixtures[0]["away"],"UDINESE")
        self.assertEqual(fixtures[0]["kickoff_utc"],"2022-08-13T16:30:00Z")

    def test_identity_normalization_amp_http_www_query(self):
        a="https://sport.sky.it/calcio/serie-a/2023/05/31/arbitri-serie-a-designazioni-giornata-38"
        b="http://www.sport.sky.it/calcio/serie-a/2023/05/31/arbitri-serie-a-designazioni-giornata-38/amp?x=1#y"
        self.assertEqual(normalize_sky_identity(a),normalize_sky_identity(b))

    def test_parse_cdx_exact_identity_only(self):
        target="https://sport.sky.it/calcio/serie-a/2023/05/31/arbitri-serie-a-designazioni-giornata-38"
        raw=json.dumps([
            ["timestamp","original","statuscode","mimetype","digest"],
            ["20230531140000",target,"200","text/html","A"],
            ["20230531150000",target+"/amp","200","text/html","B"],
            ["20230531160000",target+"-wrong","200","text/html","C"],
        ]).encode()
        rows=parse_cdx(raw,target)
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]["capture_utc"],"2023-05-31T14:00:00Z")

    def test_cdx_query_is_metadata_only(self):
        p=json.loads(REG.read_text())
        cfg=p["independent_archive_witness"]
        u=cdx_query_url(cfg["endpoint"],"https://sport.sky.it/calcio/serie-a/x",cfg)
        self.assertIn("output=json",u)
        self.assertIn("fl=timestamp%2Coriginal%2Cstatuscode%2Cmimetype%2Cdigest",u)
        self.assertIn("filter=statuscode%3A200",u)
        self.assertNotIn("filter=urlkey",u)

    def test_decision_contract_keeps_assignment_oof_closed(self):
        p=json.loads(REG.read_text())
        self.assertFalse(p["pit_binding_contract"]["referee_assignment_fixture_binding_complete"])
        self.assertFalse(p["pit_binding_contract"]["referee_oof_allowed"])
        self.assertFalse(p["decision_contract"]["referee_oof_allowed"])
        self.assertEqual(p["hard_rules"]["candidate_weight"],0)
        self.assertEqual(p["hard_rules"]["matrix_delta"],0)


if __name__=="__main__":
    unittest.main()
