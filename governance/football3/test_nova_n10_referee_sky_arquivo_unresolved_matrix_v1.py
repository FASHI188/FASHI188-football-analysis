from __future__ import annotations

import json
import unittest
from pathlib import Path

from nova_n10_referee_sky_arquivo_unresolved_matrix_v1 import (
    build_page_query,
    classify,
    page_complete,
)

REG=Path(__file__).with_name("nova_n10_referee_sky_arquivo_unresolved_matrix_registry_v1.json")


class T(unittest.TestCase):
    def test_registry_partition(self):
        p=json.loads(REG.read_text())
        u=p["unresolved_contract"]
        self.assertEqual(p["exact_base"],"f2575df36a7b2c4b2a6d5e94f480955a65cd67df")
        self.assertEqual(u["frozen_zero_rounds"],[12,24,34])
        self.assertEqual(len(u["query_rounds"]),17)
        self.assertEqual(len(u["all_unresolved_rounds"]),20)
        self.assertEqual(
            sorted(set(u["query_rounds"]+u["frozen_zero_rounds"])),
            sorted(u["all_unresolved_rounds"]),
        )
        self.assertTrue(set(u["query_rounds"]).isdisjoint(u["frozen_zero_rounds"]))

    def test_empty_estimate_is_complete_zero(self):
        complete,bad,reason=page_complete(
            estimated=0,
            cumulative_item_n=0,
            page_item_n=0,
            next_page="https://arquivo.pt/textsearch?offset=50",
        )
        self.assertTrue(complete)
        self.assertFalse(bad)
        self.assertEqual(reason,"COMPLETE_ZERO_ESTIMATE")

    def test_pagination_continues_and_fails_closed(self):
        complete,bad,reason=page_complete(
            estimated=75,
            cumulative_item_n=50,
            page_item_n=50,
            next_page="https://arquivo.pt/textsearch?offset=50",
        )
        self.assertFalse(complete)
        self.assertFalse(bad)
        self.assertEqual(reason,"CONTINUE")
        complete2,bad2,reason2=page_complete(
            estimated=75,
            cumulative_item_n=50,
            page_item_n=0,
            next_page=None,
        )
        self.assertFalse(complete2)
        self.assertTrue(bad2)
        self.assertEqual(reason2,"INCOMPLETE_EMPTY_BEFORE_ESTIMATE")

    def test_build_page_query_keeps_exact_pit_window(self):
        row={
            "sky_visible_published_utc":"2023-02-22T11:31:00Z",
            "first_fixture_cutoff_utc":"2023-02-25T17:00:00Z",
        }
        q=build_page_query(
            "https://arquivo.pt/textsearch",
            "https://sport.sky.it/calcio/serie-a/x",
            row,
            300,
            50,
            50,
        )
        self.assertIn("versionHistory=https%3A%2F%2Fsport.sky.it%2Fcalcio%2Fserie-a%2Fx",q)
        self.assertIn("from=20230222112600",q)
        self.assertIn("to=20230225165959",q)
        self.assertIn("offset=50",q)
        self.assertIn("maxItems=50",q)

    def test_classification_contract(self):
        p=json.loads(REG.read_text())
        c,closed,n=classify(positive_rounds=[8],incomplete_rounds=[],registry=p)
        self.assertEqual(c,"POSITIVE_SIGNAL_SOURCE_FEASIBILITY")
        self.assertFalse(closed)
        self.assertEqual(n,p["decision_contract"]["next_if_positive"])
        c2,closed2,n2=classify(positive_rounds=[],incomplete_rounds=[19],registry=p)
        self.assertEqual(c2,"STOP_DATA_COVERAGE")
        self.assertFalse(closed2)
        self.assertEqual(n2,p["decision_contract"]["next_if_incomplete"])
        c3,closed3,n3=classify(positive_rounds=[],incomplete_rounds=[],registry=p)
        self.assertEqual(c3,"STOP_DATA_COVERAGE")
        self.assertTrue(closed3)
        self.assertEqual(n3,p["decision_contract"]["next_if_closed_zero"])

    def test_zero_label_and_no_requery_contract(self):
        p=json.loads(REG.read_text())
        h=p["hard_rules"]
        self.assertFalse(h["frozen_zero_rounds_requeried"])
        self.assertFalse(h["wayback_requery_allowed"])
        self.assertFalse(h["commoncrawl_requery_allowed"])
        self.assertFalse(h["urlscan_requery_allowed"])
        self.assertFalse(h["result_labels_read"])
        self.assertFalse(h["score_values_read"])
        self.assertFalse(h["referee_assignment_body_parsed"])
        self.assertFalse(h["arquivo_archived_page_content_fetched"])
        self.assertFalse(h["training_allowed"])
        self.assertFalse(h["scoring_allowed"])
        self.assertEqual(h["candidate_weight"],0)
        self.assertEqual(h["matrix_delta"],0)


if __name__=="__main__":
    unittest.main()
