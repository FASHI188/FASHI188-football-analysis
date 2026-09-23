from __future__ import annotations

import json
import unittest
from pathlib import Path

from nova_n10_referee_sky_remaining5_recovery_v1 import (
    candidate_links_relaxed,
    presentation_signal,
)

REG=Path(__file__).with_name("nova_n10_referee_sky_remaining5_recovery_registry_v1.json")


class T(unittest.TestCase):
    def test_registry_partition_and_scope(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"d1b33d74df0331b2f9850629803268b50f3b223d")
        self.assertEqual(p["parent"]["remaining_gap_rounds"],[6,8,23,27,34])
        self.assertEqual(len(p["parent"]["combined_covered_rounds"]),33)
        self.assertEqual(
            sorted(set(p["parent"]["combined_covered_rounds"]+p["parent"]["remaining_gap_rounds"])),
            list(range(1,39)),
        )
        self.assertEqual(p["discovery_contract"]["day_offsets"],[-1,0,1,2])
        self.assertEqual(p["source"]["max_archive_pages_per_day"],20)

    def test_zero_label_contract(self):
        p=json.loads(REG.read_text())
        h=p["hard_rules"]
        self.assertFalse(h["repeat_parent_covered_rounds"])
        self.assertFalse(h["result_labels_read"])
        self.assertFalse(h["score_values_read"])
        self.assertFalse(h["article_body_read"])
        self.assertFalse(h["summary_text_read"])
        self.assertFalse(h["referee_assignment_body_parsed"])
        self.assertFalse(h["training_allowed"])
        self.assertFalse(h["scoring_allowed"])
        self.assertEqual(h["candidate_weight"],0)
        self.assertEqual(h["matrix_delta"],0)

    def test_presentation_signal(self):
        terms=["presentazione","partite","orari","calendario","guida"]
        self.assertTrue(presentation_signal("Serie A, 6 giornata: le partite","",terms))
        self.assertTrue(presentation_signal("","https://sport.sky.it/calcio/serie-a/x/presentazione-partite",terms))
        self.assertFalse(presentation_signal("Serie A mercato","https://sport.sky.it/calcio/serie-a/x",terms))

    def test_relaxed_archive_card_allows_presentation_without_referee_word(self):
        rep={
            "date":"2022-09-09",
            "pages":[{
                "page":3,
                "anchors":[
                    {
                        "href":"https://sport.sky.it/calcio/serie-a/2022/09/09/serie-a-giornata-6-presentazione-partite",
                        "text":"Serie A, 6^ giornata: le partite, dove vederle",
                    },
                    {
                        "href":"https://sport.sky.it/calcio/serie-a/2022/09/09/serie-a-giornata-7-presentazione-partite",
                        "text":"Serie A, 7^ giornata: le partite",
                    },
                ],
            }],
        }
        target={"round":6}
        discovery={
            "referee_terms":["arbitri","arbitro","designazioni","designazione"],
            "presentation_terms":["presentazione","partite","orari","calendario","guida"],
        }
        source={"domain_suffix":"sport.sky.it"}
        out=candidate_links_relaxed(rep,target,discovery,source)
        self.assertEqual(len(out),1)
        self.assertEqual(out[0]["round"],6)
        self.assertIn("giornata-6",out[0]["normalized_url"])

    def test_archive_card_still_requires_exact_round_and_serie_a(self):
        rep={
            "date":"2023-05-04",
            "pages":[{
                "page":1,
                "anchors":[
                    {"href":"https://sport.sky.it/calcio/serie-a/2023/05/04/serie-a-giornata-34-presentazione-partite","text":"Serie A 34^ giornata: presentazione"},
                    {"href":"https://sport.sky.it/calcio/serie-a/2023/05/04/serie-a-giornata-35-presentazione-partite","text":"Serie A 35^ giornata: presentazione"},
                    {"href":"https://sport.sky.it/calcio/premier-league/2023/05/04/giornata-34-guida","text":"Premier League 34 giornata guida"},
                ],
            }],
        }
        discovery={
            "referee_terms":["arbitri","arbitro","designazioni","designazione"],
            "presentation_terms":["presentazione","partite","orari","calendario","guida"],
        }
        out=candidate_links_relaxed(rep,{"round":34},discovery,{"domain_suffix":"sport.sky.it"})
        self.assertEqual(len(out),1)
        self.assertIn("giornata-34",out[0]["normalized_url"])


if __name__=="__main__":
    unittest.main()
