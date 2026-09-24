from __future__ import annotations

import datetime as dt
import json
import unittest
from pathlib import Path

from nova_n10_referee_sky_archivetoday_feasibility_v1 import (
    UTC,
    captures_from_index,
    classify,
    original_from_link_header,
    parse_capture_url,
    pit_filter,
    timegate_capture,
)

REG=Path(__file__).with_name("nova_n10_referee_sky_archivetoday_feasibility_registry_v1.json")


class T(unittest.TestCase):
    def test_registry_scope(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"32ced8e95ae0c1a52581921c5bfd10899eb06e88")
        self.assertEqual([x["round"] for x in p["samples"]],[8,24,34])
        self.assertFalse(p["source"]["follow_snapshot_redirects"])
        self.assertFalse(p["source"]["snapshot_body_fetch_allowed"])
        self.assertFalse(p["hard_rules"]["snapshot_body_read"])
        self.assertFalse(p["hard_rules"]["snapshot_creation_allowed"])
        self.assertTrue(p["metadata_contract"]["timegate_requires_original_link_exact"])

    def test_parse_long_capture_exact_identity(self):
        target="https://sport.sky.it/calcio/serie-a/2023/05/04/arbitri-serie-a-designazioni-giornata-34"
        u="https://archive.ph/2023.05.04-121500/https%3A//sport.sky.it/calcio/serie-a/2023/05/04/arbitri-serie-a-designazioni-giornata-34"
        cap=parse_capture_url(u,target,["archive.ph","archive.is","archive.today"])
        self.assertIsNotNone(cap)
        self.assertEqual(cap["capture_utc"],"2023-05-04T12:15:00Z")

    def test_index_parser_only_archive_links(self):
        target="https://sport.sky.it/calcio/serie-a/2023/05/04/arbitri-serie-a-designazioni-giornata-34"
        raw=f'''<html><body>
        <a href="/2023.05.04-121500/https%3A//sport.sky.it/calcio/serie-a/2023/05/04/arbitri-serie-a-designazioni-giornata-34">good</a>
        <a href="/2023.05.04-121600/https%3A//sport.sky.it/calcio/serie-a/2023/05/04/wrong">bad</a>
        </body></html>'''.encode()
        out=captures_from_index(raw,"https://archive.ph/"+target,target,["archive.ph","archive.is","archive.today"])
        self.assertEqual(len(out),1)
        self.assertEqual(out[0]["capture_utc"],"2023-05-04T12:15:00Z")

    def test_timegate_requires_original_link(self):
        target="https://sport.sky.it/calcio/serie-a/x"
        response={
            "headers":{
                "memento-datetime":"Thu, 29 Sep 2022 15:00:00 GMT",
                "link":f'<{target}>; rel="original"',
            },
            "location":"https://archive.ph/AbCdE",
        }
        cap=timegate_capture(response,target=target,allowed_hosts=["archive.ph","archive.is","archive.today"])
        self.assertIsNotNone(cap)
        self.assertEqual(cap["capture_utc"],"2022-09-29T15:00:00Z")
        bad={**response,"headers":{**response["headers"],"link":'<https://sport.sky.it/wrong>; rel="original"'}}
        self.assertIsNone(timegate_capture(bad,target=target,allowed_hosts=["archive.ph"]))

    def test_original_link_parser(self):
        link='<https://archive.ph/AbCdE>; rel="memento"; datetime="x", <https://sport.sky.it/x>; rel="original"'
        self.assertEqual(original_from_link_header(link),"https://sport.sky.it/x")

    def test_pit_filter(self):
        caps=[
            {"capture_utc":"2023-05-04T11:00:00Z","archive_url":"https://archive.ph/a","original_url":"x","surface":"index"},
            {"capture_utc":"2023-05-04T12:00:00Z","archive_url":"https://archive.ph/b","original_url":"x","surface":"index"},
            {"capture_utc":"2023-05-06T14:00:00Z","archive_url":"https://archive.ph/c","original_url":"x","surface":"index"},
        ]
        lower=dt.datetime(2023,5,4,11,5,tzinfo=UTC)
        upper=dt.datetime(2023,5,6,13,0,tzinfo=UTC)
        out=pit_filter(caps,lower,upper)
        self.assertEqual([x["archive_url"] for x in out],["https://archive.ph/b"])

    def test_classify_paths(self):
        p=json.loads(REG.read_text())
        c,n=classify([{"pit_capture_n":1,"error":None}],p)
        self.assertEqual(c,"POSITIVE_SIGNAL_SOURCE_FEASIBILITY")
        self.assertEqual(n,p["reasonable_subroutes"]["if_positive"])
        c2,n2=classify([{"pit_capture_n":0,"error":"403"}],p)
        self.assertEqual(c2,"STOP_DATA_COVERAGE")
        self.assertEqual(n2,p["reasonable_subroutes"]["if_external_or_contract_error"])
        c3,n3=classify([{"pit_capture_n":0,"error":None}],p)
        self.assertEqual(c3,"STOP_DATA_COVERAGE")
        self.assertEqual(n3,p["reasonable_subroutes"]["if_complete_zero"])


if __name__=="__main__":
    unittest.main()
