from __future__ import annotations

import json
import unittest
from pathlib import Path

from nova_n10_referee_sky_memgator_recovery_v1 import (
    SkyMemGatorRecoveryError,
    audit_mementos,
    build_timemap_url,
    classify,
    parse_timemap,
    provider_family,
)

REG=Path(__file__).with_name("nova_n10_referee_sky_memgator_recovery_registry_v1.json")
TARGET="https://sport.sky.it/calcio/serie-a/2023/05/04/arbitri-serie-a-designazioni-giornata-34"


class T(unittest.TestCase):
    def test_registry_recovery_scope(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"6f69e9008015061cc4802a1df40b7d5565679b5b")
        self.assertEqual([x["round"] for x in p["samples"]],[8,24,34])
        self.assertEqual(
            [x["id"] for x in p["recovery_provider_families"]],
            [
                "perma.cc","warp.da.ndl.go.jp","web.archive.org.au",
                "webarchiveweb.bac-lac.canada.ca","webarchive.nrscotland.gov.uk",
                "webarchive.org.uk","webarchive.parliament.uk",
            ],
        )
        self.assertEqual(
            set(x["id"] for x in p["excluded_provider_families"]),
            {
                "web.archive.org","arquivo.pt","wayback.archive-it.org",
                "archive.today","waext.banq.qc.ca","wayback.vefsafn.is",
            },
        )
        self.assertFalse(p["hard_rules"]["replay_proxy_fetch_allowed"])
        self.assertFalse(p["hard_rules"]["memento_target_fetch_allowed"])

    def test_build_timemap_url_encodes_exact_target(self):
        q=build_timemap_url("https://memgator.cs.odu.edu",TARGET)
        self.assertTrue(q.startswith("https://memgator.cs.odu.edu/timemap/json/"))
        self.assertIn("%3A%2F%2F",q)
        self.assertNotIn("/memento/proxy/",q)

    def test_parse_timemap_contract(self):
        raw=json.dumps({
            "original_uri":TARGET,
            "mementos":{"list":[
                {"datetime":"Thu, 04 May 2023 12:00:00 GMT","uri":"https://perma.cc/ABCD-EFGH"},
                {"datetime":"Fri, 05 May 2023 12:00:00 GMT","uri":"https://web.archive.org/web/20230505120000/"+TARGET},
            ]},
        }).encode()
        original,items=parse_timemap(raw)
        self.assertEqual(original,TARGET)
        self.assertEqual(len(items),2)

    def test_provider_family_recovery_and_excluded(self):
        p=json.loads(REG.read_text())
        r=p["recovery_provider_families"]
        e=p["excluded_provider_families"]
        self.assertEqual(provider_family("https://perma.cc/ABCD-EFGH",r,e),("RECOVERY","perma.cc"))
        self.assertEqual(provider_family("https://web.archive.org/web/2023/x",r,e),("EXCLUDED","web.archive.org"))
        self.assertEqual(provider_family("https://unknown.example/x",r,e),("UNKNOWN",None))

    def test_audit_counts_only_recovery_provider_inside_pit(self):
        p=json.loads(REG.read_text())
        items=[
            {"datetime":"Thu, 04 May 2023 12:00:00 GMT","uri":"https://perma.cc/ABCD-EFGH"},
            {"datetime":"Thu, 04 May 2023 12:10:00 GMT","uri":"https://web.archive.org/web/20230504121000/"+TARGET},
            {"datetime":"Sun, 07 May 2023 12:00:00 GMT","uri":"https://perma.cc/LATE-TEST"},
        ]
        out=audit_mementos(
            TARGET,items,TARGET,
            "2023-05-04T11:10:00Z","2023-05-06T13:00:00Z",
            p["recovery_provider_families"],p["excluded_provider_families"],
        )
        self.assertEqual(out["memento_n"],3)
        self.assertEqual(out["eligible_recovery_witness_n"],1)
        self.assertEqual(out["excluded_provider_memento_n"],1)

    def test_audit_rejects_original_identity_mismatch(self):
        p=json.loads(REG.read_text())
        with self.assertRaises(SkyMemGatorRecoveryError):
            audit_mementos(
                "https://sport.sky.it/wrong",
                [],
                TARGET,
                "2023-05-04T11:10:00Z",
                "2023-05-06T13:00:00Z",
                p["recovery_provider_families"],
                p["excluded_provider_families"],
            )

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
