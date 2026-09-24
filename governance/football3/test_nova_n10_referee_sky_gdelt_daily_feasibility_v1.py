from __future__ import annotations

import io
import json
import unittest
import zipfile
from pathlib import Path

from nova_n10_referee_sky_gdelt_daily_feasibility_v1 import (
    classify,
    frozen_dates,
    scan_zip_sky,
)

REG=Path(__file__).with_name("nova_n10_referee_sky_gdelt_daily_feasibility_registry_v1.json")


class T(unittest.TestCase):
    def test_registry_scope(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"348bfe7f2fffea8e580736f9594d7de7dc7a4e40")
        self.assertEqual([x["round"] for x in p["samples"]],[9,24,38])
        self.assertFalse(p["hard_rules"]["prior_source_requery_allowed"])
        self.assertFalse(p["hard_rules"]["result_labels_read"])
        self.assertFalse(p["hard_rules"]["score_values_read"])
        self.assertFalse(p["hard_rules"]["full_gkg_row_persisted"])
        self.assertFalse(p["hard_rules"]["training_allowed"])
        self.assertFalse(p["hard_rules"]["scoring_allowed"])

    def test_frozen_dates_include_cutoff_calendar_day(self):
        out=frozen_dates(
            "2023-02-22T11:31:00Z",
            "2023-02-25T17:00:00Z",
            5,
        )
        self.assertEqual(out,["2023-02-22","2023-02-23","2023-02-24","2023-02-25"])

    def test_scan_zip_exact_sky_identity(self):
        target="https://sport.sky.it/calcio/serie-a/2023/02/22/arbitri-serie-a-designazioni-giornata-24"
        payload=(
            b'20230222\thttps://example.test/x\n'
            b'20230222\thttp://www.sport.sky.it/calcio/serie-a/2023/02/22/arbitri-serie-a-designazioni-giornata-24/amp?utm=x\n'
            b'20230222\thttps://sport.sky.it/calcio/serie-a/2023/02/22/arbitri-serie-a-designazioni-giornata-24-wrong\n'
        )
        bio=io.BytesIO()
        with zipfile.ZipFile(bio,"w",zipfile.ZIP_DEFLATED) as z:
            z.writestr("20230222.gkg.csv",payload)
        r=scan_zip_sky(bio.getvalue(),target,".gkg.csv")
        self.assertEqual(r["match_n"],1)
        self.assertEqual(len(r["matches"][0]["line_sha256"]),64)
        self.assertNotIn("line",r["matches"][0])

    def test_classify_positive(self):
        p=json.loads(REG.read_text())
        audits=[
            {"round":9,"positive_day_n":1,"error_n":0},
            {"round":24,"positive_day_n":0,"error_n":1},
            {"round":38,"positive_day_n":0,"error_n":0},
        ]
        c,n=classify(audits,p)
        self.assertEqual(c,"POSITIVE_SIGNAL_SOURCE_FEASIBILITY")
        self.assertEqual(n,p["reasonable_subroutes"]["if_positive"])

    def test_classify_external(self):
        p=json.loads(REG.read_text())
        audits=[
            {"round":9,"positive_day_n":0,"error_n":0},
            {"round":24,"positive_day_n":0,"error_n":1},
            {"round":38,"positive_day_n":0,"error_n":0},
        ]
        c,n=classify(audits,p)
        self.assertEqual(c,"STOP_DATA_COVERAGE")
        self.assertEqual(n,p["reasonable_subroutes"]["if_external_error"])

    def test_classify_complete_zero(self):
        p=json.loads(REG.read_text())
        audits=[
            {"round":9,"positive_day_n":0,"error_n":0},
            {"round":24,"positive_day_n":0,"error_n":0},
            {"round":38,"positive_day_n":0,"error_n":0},
        ]
        c,n=classify(audits,p)
        self.assertEqual(c,"STOP_DATA_COVERAGE")
        self.assertEqual(n,p["reasonable_subroutes"]["if_complete_zero"])


if __name__=="__main__":
    unittest.main()
