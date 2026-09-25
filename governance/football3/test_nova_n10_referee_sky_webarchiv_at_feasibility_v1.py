from __future__ import annotations

import json
import unittest
from pathlib import Path

from nova_n10_referee_sky_webarchiv_at_feasibility_v1 import (
    SkyWebarchivATError,
    build_query,
    classify,
    evaluate,
    http_normalized_target,
    parse_cdxj,
)

REG=Path(__file__).with_name("nova_n10_referee_sky_webarchiv_at_feasibility_registry_v1.json")
TARGET="https://sport.sky.it/calcio/serie-a/2023/05/04/arbitri-serie-a-designazioni-giornata-34"


class T(unittest.TestCase):
    def test_registry_contract(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"2ff541961c2b8e25145aaf0f0c0d1f707a7239b4")
        self.assertEqual([x["round"] for x in p["samples"]],[8,24,34])
        self.assertEqual(p["provider_contract_source"]["commit"],"37d69db28665afe70d1bacf7e704343fe0cf1b6a")
        self.assertEqual(p["provider_contract_source"]["git_blob_sha"],"507dba38cb1cb0d1e8fd8d1ebcc7d9a0e4034dd0")
        self.assertFalse(p["hard_rules"]["replay_fetch_allowed"])
        self.assertFalse(p["hard_rules"]["result_labels_read"])

    def test_http_normalized_target(self):
        self.assertEqual(
            http_normalized_target(TARGET),
            "http://sport.sky.it/calcio/serie-a/2023/05/04/arbitri-serie-a-designazioni-giornata-34",
        )

    def test_build_query_has_exact_window(self):
        q=build_query(
            "https://webarchiv.onb.ac.at/web/cdx",
            TARGET,
            "2023-05-04T11:10:00Z",
            "2023-05-06T13:00:00Z",
            1000,
        )
        self.assertIn("url=http%3A%2F%2Fsport.sky.it",q)
        self.assertIn("from=20230504111000",q)
        self.assertIn("to=20230506125959",q)
        self.assertIn("limit=1000",q)

    def test_parse_cdxj(self):
        raw=(
            'at,sky)/x 20230504123000 {"url":"http://sport.sky.it/x","status":"200","mime":"text/html"}\n'
        ).encode()
        rows=parse_cdxj(raw)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["timestamp"],"20230504123000")
        self.assertEqual(rows[0]["status"],200)

    def test_parse_cdxj_malformed_fails(self):
        with self.assertRaises(SkyWebarchivATError):
            parse_cdxj(b"not-cdxj\n")

    def test_evaluate_identity_time_status(self):
        row={
            "timestamp":"20230504123000",
            "url":"http://www.sport.sky.it/calcio/serie-a/2023/05/04/arbitri-serie-a-designazioni-giornata-34/",
            "status":200,"mime":"text/html","digest":"x","length":"1",
        }
        out=evaluate(row,TARGET,"2023-05-04T11:10:00Z","2023-05-06T13:00:00Z",200)
        self.assertTrue(out["exact_identity"])
        self.assertTrue(out["pit_time_ok"])
        self.assertTrue(out["status_ok"])
        self.assertTrue(out["witness_pass"])

    def test_classify_paths(self):
        p=json.loads(REG.read_text())
        c,n=classify(1,1,p)
        self.assertEqual(c,"POSITIVE_SIGNAL_SOURCE_FEASIBILITY")
        self.assertEqual(n,p["reasonable_subroutes"]["if_positive"])
        c2,n2=classify(0,0,p)
        self.assertEqual(c2,"STOP_DATA_COVERAGE")
        self.assertEqual(n2,p["reasonable_subroutes"]["if_complete_zero"])
        c3,n3=classify(0,1,p)
        self.assertEqual(c3,"STOP_DATA_COVERAGE")
        self.assertEqual(n3,p["reasonable_subroutes"]["if_external_error"])


if __name__=="__main__":
    unittest.main()
