from __future__ import annotations

import datetime as dt
import json
import unittest
from pathlib import Path

from nova_n10_referee_sky_hnalgolia_token_discovery_v1 import (
    UTC,
    build_variant_query,
    build_query,
    classify,
)
from nova_n10_referee_sky_hnalgolia_feasibility_v1 import evaluate_hit

REG=Path(__file__).with_name("nova_n10_referee_sky_hnalgolia_token_discovery_registry_v1.json")


class T(unittest.TestCase):
    def test_registry_scope(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"60025cca45ca91f9bcd764a2a971716db8ee9fdf")
        self.assertEqual([x["round"] for x in p["samples"]],[8,24,34])
        self.assertEqual(p["query_contract"]["variants"],["host_path_tokens","slug_only"])
        self.assertEqual(p["query_contract"]["max_discovery_batches"],1)
        self.assertFalse(p["hard_rules"]["third_query_variant_allowed"])

    def test_variant_query_terms(self):
        target="https://sport.sky.it/calcio/serie-a/2023/05/04/arbitri-serie-a-designazioni-giornata-34"
        a=build_variant_query("host_path_tokens",target)
        b=build_variant_query("slug_only",target)
        self.assertIn("sport.sky.it",a)
        self.assertIn("calcio",a)
        self.assertIn("arbitri serie a designazioni giornata 34",a)
        self.assertEqual(b,"arbitri serie a designazioni giornata 34")

    def test_build_query_url_field_and_pit(self):
        p=json.loads(REG.read_text())
        q=build_query(
            p["source"]["endpoint"],
            "slug_only",
            "https://sport.sky.it/calcio/serie-a/2023/05/04/arbitri-serie-a-designazioni-giornata-34",
            1683198300,
            1683378000,
            0,
            p["source"],
            p["query_contract"],
        )
        self.assertIn("restrictSearchableAttributes=url",q)
        self.assertIn("numericFilters=created_at_i%3E%3D1683198300%2Ccreated_at_i%3C1683378000",q)
        self.assertIn("attributesToRetrieve=objectID%2Curl%2Ccreated_at_i",q)

    def test_evaluate_still_requires_exact_identity(self):
        target="https://sport.sky.it/calcio/serie-a/x"
        hit={"objectID":"1","url":"https://sport.sky.it/calcio/serie-a/x-related","created_at_i":1664452800}
        lower=dt.datetime.fromtimestamp(1664450000,tz=UTC)
        upper=dt.datetime.fromtimestamp(1664460000,tz=UTC)
        out=evaluate_hit(hit,target,lower,upper)
        self.assertFalse(out["exact_identity"])
        self.assertFalse(out["witness_pass"])

    def test_classify_external(self):
        p=json.loads(REG.read_text())
        c,n=classify(0,1,p)
        self.assertEqual(c,"STOP_DATA_COVERAGE")
        self.assertEqual(n,p["reasonable_subroutes"]["if_external_error"])

    def test_classify_zero_closes_hn(self):
        p=json.loads(REG.read_text())
        c,n=classify(0,0,p)
        self.assertEqual(c,"STOP_DATA_COVERAGE")
        self.assertIn("Close the HN Algolia source family",n)


if __name__=="__main__":
    unittest.main()
