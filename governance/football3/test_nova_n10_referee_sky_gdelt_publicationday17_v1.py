from __future__ import annotations

import io
import json
import unittest
import zipfile
from pathlib import Path

from nova_n10_referee_sky_gdelt_publicationday17_v1 import (
    classify,
    publication_day,
)
from nova_n10_referee_sky_gdelt_daily_feasibility_v1 import scan_zip_sky

REG=Path(__file__).with_name("nova_n10_referee_sky_gdelt_publicationday17_registry_v1.json")


class T(unittest.TestCase):
    def test_registry_scope_and_partition(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"ab817a9c05d41b0d97418aa6a40189753bb34d2a")
        self.assertEqual(p["sample_rounds_already_scanned"],[9,24,38])
        self.assertEqual(
            p["target_rounds"],
            [8,11,12,14,15,16,17,19,20,21,22,23,25,27,28,32,34],
        )
        self.assertEqual(
            sorted(set(p["unresolved_rounds_all"])-set(p["sample_rounds_already_scanned"])),
            p["target_rounds"],
        )
        self.assertFalse(p["hard_rules"]["prior_sample_round_requery_allowed"])
        self.assertFalse(p["hard_rules"]["result_labels_read"])
        self.assertFalse(p["hard_rules"]["score_values_read"])

    def test_publication_day(self):
        self.assertEqual(
            publication_day("2023-05-04T11:10:00Z","2023-05-06T13:00:00Z"),
            "2023-05-04",
        )

    def test_scan_zip_exact_identity(self):
        target="https://sport.sky.it/calcio/serie-a/2023/05/04/arbitri-serie-a-designazioni-giornata-34"
        payload=(
            b'20230504\thttps://example.test/x\n'
            b'20230504\thttp://www.sport.sky.it/calcio/serie-a/2023/05/04/arbitri-serie-a-designazioni-giornata-34/amp?utm=x\n'
            b'20230504\thttps://sport.sky.it/calcio/serie-a/2023/05/04/arbitri-serie-a-designazioni-giornata-34-wrong\n'
        )
        bio=io.BytesIO()
        with zipfile.ZipFile(bio,"w",zipfile.ZIP_DEFLATED) as z:
            z.writestr("20230504.gkg.csv",payload)
        r=scan_zip_sky(bio.getvalue(),target,".gkg.csv")
        self.assertEqual(r["match_n"],1)
        self.assertEqual(len(r["matches"][0]["line_sha256"]),64)
        self.assertNotIn("line",r["matches"][0])

    def test_classify_positive(self):
        p=json.loads(REG.read_text())
        audits=[
            {"round":8,"positive_day":True,"error":None},
            {"round":11,"positive_day":False,"error":"x"},
        ]
        c,n=classify(audits,p)
        self.assertEqual(c,"POSITIVE_SIGNAL_SOURCE_FEASIBILITY")
        self.assertEqual(n,p["reasonable_subroutes"]["if_positive"])

    def test_classify_external(self):
        p=json.loads(REG.read_text())
        audits=[
            {"round":8,"positive_day":False,"error":None},
            {"round":11,"positive_day":False,"error":"timeout"},
        ]
        c,n=classify(audits,p)
        self.assertEqual(c,"STOP_DATA_COVERAGE")
        self.assertEqual(n,p["reasonable_subroutes"]["if_external_error"])

    def test_classify_complete_zero(self):
        p=json.loads(REG.read_text())
        audits=[
            {"round":8,"positive_day":False,"error":None},
            {"round":11,"positive_day":False,"error":None},
        ]
        c,n=classify(audits,p)
        self.assertEqual(c,"STOP_DATA_COVERAGE")
        self.assertEqual(n,p["reasonable_subroutes"]["if_complete_zero"])


if __name__=="__main__":
    unittest.main()
