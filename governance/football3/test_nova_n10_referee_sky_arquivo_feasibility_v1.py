from __future__ import annotations

import datetime as dt
import json
import unittest
from pathlib import Path

from nova_n10_referee_sky_arquivo_feasibility_v1 import (
    UTC,
    build_query,
    decide,
    eligible_items,
    normalize_identity,
    pagination_incomplete,
    query_bounds,
    safe_metadata,
)

REG=Path(__file__).with_name("nova_n10_referee_sky_arquivo_feasibility_registry_v1.json")


class T(unittest.TestCase):
    def test_registry_scope(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"81a959a28657868bbf4aca00cfeffd8b6039fdce")
        self.assertEqual([x["round"] for x in p["samples"]],[12,24,34])
        self.assertEqual(p["source"]["mode"],"versionHistory")
        self.assertFalse(p["hard_rules"]["wayback_requery_allowed"])
        self.assertFalse(p["hard_rules"]["commoncrawl_requery_allowed"])
        self.assertFalse(p["hard_rules"]["urlscan_requery_allowed"])
        self.assertFalse(p["source"]["archived_page_content_fetch_allowed"])

    def test_identity_normalization(self):
        a="https://sport.sky.it/calcio/serie-a/2023/05/04/arbitri-serie-a-designazioni-giornata-34"
        b="http://www.sport.sky.it/calcio/serie-a/2023/05/04/arbitri-serie-a-designazioni-giornata-34/amp?x=1#y"
        self.assertEqual(normalize_identity(a),normalize_identity(b))

    def test_query_bounds_and_version_history_query(self):
        row={
            "sky_visible_published_utc":"2023-05-04T11:10:00Z",
            "first_fixture_cutoff_utc":"2023-05-06T13:00:00Z",
        }
        lower,upper,frm,to=query_bounds(row,300)
        self.assertEqual(lower,dt.datetime(2023,5,4,11,5,tzinfo=UTC))
        self.assertEqual(upper,dt.datetime(2023,5,6,13,0,tzinfo=UTC))
        q=build_query("https://arquivo.pt/textsearch","https://sport.sky.it/x",row,300,50)
        self.assertIn("versionHistory=https%3A%2F%2Fsport.sky.it%2Fx",q)
        self.assertIn("from=20230504110500",q)
        self.assertIn("to=20230506125959",q)
        self.assertIn("maxItems=50",q)

    def test_safe_metadata_only(self):
        raw={
            "title":"Example","originalURL":"https://sport.sky.it/x","tstamp":"20230504120000",
            "mimeType":"text/html","statusCode":200,"digest":"ABC","collection":"AWP",
            "linkToArchive":"must not persist","linkToExtractedText":"must not persist",
        }
        m=safe_metadata(raw)
        self.assertEqual(
            set(m),
            {"title","originalURL","tstamp","mimeType","statusCode","digest","collection"},
        )

    def test_eligible_exact_identity_and_pit(self):
        target="https://sport.sky.it/calcio/serie-a/2023/05/04/arbitri-serie-a-designazioni-giornata-34"
        lower=dt.datetime(2023,5,4,11,5,tzinfo=UTC)
        upper=dt.datetime(2023,5,6,13,0,tzinfo=UTC)
        rows=[
            {"title":"a","originalURL":target+"?utm=x","tstamp":"20230504120000","mimeType":"text/html","statusCode":200,"digest":"A","collection":"X"},
            {"title":"b","originalURL":target,"tstamp":"20230506140000","mimeType":"text/html","statusCode":200,"digest":"B","collection":"X"},
            {"title":"c","originalURL":target+"-wrong","tstamp":"20230504123000","mimeType":"text/html","statusCode":200,"digest":"C","collection":"X"},
            {"title":"d","originalURL":target,"tstamp":"20230504124000","mimeType":"image/png","statusCode":200,"digest":"D","collection":"X"},
        ]
        out=eligible_items(rows,target,lower,upper)
        self.assertEqual(len(out),1)
        self.assertEqual(out[0]["digest"],"A")
        self.assertEqual(out[0]["capture_utc"],"2023-05-04T12:00:00Z")

    def test_decision_fail_closed(self):
        self.assertFalse(pagination_incomplete(
            {"estimated_nr_results":0,"next_page":"https://arquivo.pt/textsearch?offset=50"},
            0,
        ))
        self.assertFalse(pagination_incomplete(
            {"estimated_nr_results":1,"next_page":"https://arquivo.pt/textsearch?offset=50"},
            1,
        ))
        self.assertTrue(pagination_incomplete(
            {"estimated_nr_results":51,"next_page":"https://arquivo.pt/textsearch?offset=50"},
            50,
        ))
        p=json.loads(REG.read_text())
        c,n=decide([],[],p)
        self.assertEqual(c,"STOP_DATA_COVERAGE")
        self.assertEqual(n,p["reasonable_subroutes"]["if_zero_no_external_errors"])
        c2,n2=decide([],[24],p)
        self.assertEqual(c2,"STOP_DATA_COVERAGE")
        self.assertEqual(n2,p["reasonable_subroutes"]["if_external_error"])
        c3,n3=decide([34],[24],p)
        self.assertEqual(c3,"POSITIVE_SIGNAL_SOURCE_FEASIBILITY")
        self.assertEqual(n3,p["reasonable_subroutes"]["if_positive"])


if __name__=="__main__":
    unittest.main()
