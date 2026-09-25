from __future__ import annotations

import json
import unittest
from pathlib import Path

from nova_n10_referee_sky_megalodon_feasibility_v1 import (
    audit_anchors,
    build_lookup_url,
    classify,
    parse_archive_href,
    parse_visible_timestamp,
)

REG=Path(__file__).with_name("nova_n10_referee_sky_megalodon_feasibility_registry_v1.json")
TARGET="https://sport.sky.it/calcio/serie-a/2023/05/04/arbitri-serie-a-designazioni-giornata-34"


class T(unittest.TestCase):
    def test_registry_scope_and_pins(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"d44de65d73e42000ebf5d1b941a0625ec6e6c1fd")
        self.assertEqual([x["round"] for x in p["samples"]],[8,24,34])
        self.assertEqual(p["lookup_contract_sources"][0]["git_blob_sha"],"21d02d5025c2351fe4dfeb473399125cc5e774c8")
        self.assertEqual(p["lookup_contract_sources"][1]["git_blob_sha"],"3543138aacf595cc0059ba6c0245fb798360be67")
        self.assertFalse(p["timestamp_contract"]["path_timestamp_timezone_proven"])
        self.assertFalse(p["hard_rules"]["archive_creation_allowed"])

    def test_lookup_url_get_contract(self):
        q=build_lookup_url("https://megalodon.jp/",TARGET)
        self.assertTrue(q.startswith("https://megalodon.jp/?"))
        self.assertIn("url=https%3A%2F%2Fsport.sky.it",q)

    def test_parse_archive_href_exact_original(self):
        href="https://megalodon.jp/2023-0504-2130-15/https://sport.sky.it:443/calcio/serie-a/2023/05/04/arbitri-serie-a-designazioni-giornata-34"
        m=parse_archive_href(href)
        self.assertIsNotNone(m)
        self.assertEqual(m["path_timestamp_local_unqualified"],"2023-05-04 21:30:15")
        self.assertIn("sport.sky.it:443",m["embedded_original"])

    def test_path_timestamp_alone_is_metadata_insufficient(self):
        href="https://megalodon.jp/2023-0504-2130-15/https://sport.sky.it:443/calcio/serie-a/2023/05/04/arbitri-serie-a-designazioni-giornata-34"
        out=audit_anchors(
            [{"href":href,"text":"existing archive"}],
            TARGET,"2023-05-04T11:10:00Z","2023-05-06T13:00:00Z",
        )
        self.assertEqual(out["exact_archive_candidate_n"],1)
        self.assertEqual(out["metadata_insufficient_candidate_n"],1)
        self.assertEqual(out["witness_pass_n"],0)

    def test_explicit_jst_visible_timestamp_can_bind(self):
        href="https://megalodon.jp/2023-0504-2130-15/https://sport.sky.it:443/calcio/serie-a/2023/05/04/arbitri-serie-a-designazioni-giornata-34"
        out=audit_anchors(
            [{"href":href,"text":"取得日時 2023年5月4日 21:30:15 JST"}],
            TARGET,"2023-05-04T11:10:00Z","2023-05-06T13:00:00Z",
        )
        self.assertEqual(out["metadata_insufficient_candidate_n"],0)
        self.assertEqual(out["witness_pass_n"],1)
        self.assertEqual(out["witnesses"][0]["capture_utc"],"2023-05-04T12:30:15Z")

    def test_timezone_less_visible_time_rejected(self):
        self.assertEqual(parse_visible_timestamp("取得日時 2023年5月4日 21:30:15"),[])
        self.assertEqual(parse_visible_timestamp("2023-05-04 21:30:15"),[])

    def test_classify_paths(self):
        p=json.loads(REG.read_text())
        c,n=classify(1,1,1,p)
        self.assertEqual(c,"POSITIVE_SIGNAL_SOURCE_FEASIBILITY")
        self.assertEqual(n,p["reasonable_subroutes"]["if_positive"])
        c2,n2=classify(0,1,0,p)
        self.assertEqual(c2,"STOP_DATA_COVERAGE")
        self.assertEqual(n2,p["reasonable_subroutes"]["if_external_error"])
        c3,n3=classify(0,0,1,p)
        self.assertEqual(c3,"STOP_DATA_COVERAGE")
        self.assertEqual(n3,p["reasonable_subroutes"]["if_metadata_insufficient"])
        c4,n4=classify(0,0,0,p)
        self.assertEqual(c4,"STOP_DATA_COVERAGE")
        self.assertEqual(n4,p["reasonable_subroutes"]["if_clean_zero"])


if __name__=="__main__":
    unittest.main()
