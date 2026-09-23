from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path

from nova_n10_referee_sky_combined_freeze_v1 import (
    build_freeze,
    round_title_identity,
    select_page_title,
    url_round_tokens,
)

REG=Path(__file__).with_name("nova_n10_referee_sky_combined_freeze_registry_v1.json")


def candidate(round_no: int, url_round: int | None = None) -> dict:
    u_round=round_no if url_round is None else url_round
    return {
        "final_normalized_url":f"https://sport.sky.it/calcio/serie-a/2023/01/01/serie-a-giornata-{u_round}-presentazione-partite",
        "title_candidates":[
            f"Serie A, {round_no}^ giornata: le partite, dove vederle e gli arbitri | Sky Sport"
        ],
        "published_local":"2023-01-01T15:00:00",
        "timezone":"Europe/Rome",
        "visible_marker_context":"01 gen 2023 - 15:00",
        "archive_date":"2023-01-01",
        "archive_page":2,
        "prefix_sha256":"a"*64,
        "prefix_boundary_bytes":1000,
        "network_bytes_read":1024,
        "overshoot_bytes_discarded":24,
    }


class T(unittest.TestCase):
    def test_registry_parent_partition(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"b20c3cdab6c61d788672fecf434ecc9fe94c750e")
        rounds=[]
        for parent in p["parents"]:
            rounds.extend(parent["accepted_rounds"])
        self.assertEqual(sorted(rounds),list(range(1,39)))
        self.assertEqual(len(rounds),38)
        self.assertEqual([x["pr"] for x in p["parents"]],[473,474,475])
        self.assertEqual(p["anomaly_contract"]["known_expected_anomaly_rounds"],[23])

    def test_title_identity(self):
        self.assertTrue(round_title_identity("Serie A, 23^ giornata: le partite e gli arbitri | Sky Sport",23))
        self.assertTrue(round_title_identity("Serie A, arbitri 1 giornata | Sky Sport",1))
        self.assertFalse(round_title_identity("Serie A, 22^ giornata: gli arbitri | Sky Sport",23))
        self.assertFalse(round_title_identity("Serie A 23 giornata: le partite | Sky Sport",23))

    def test_select_page_title(self):
        titles=[
            "La presentazione della giornata di Serie A",
            "Serie A, 27^ giornata: le partite, dove vederle e gli arbitri | Sky Sport",
        ]
        self.assertIn("27^ giornata",select_page_title(titles,27))

    def test_url_round_tokens(self):
        self.assertEqual(
            url_round_tokens("https://sport.sky.it/calcio/serie-a/2023/02/16/serie-a-giornata-22-presentazione-partite"),
            [22],
        )

    def test_build_freeze_records_round23_anomaly(self):
        p=json.loads(REG.read_text())
        payloads=[]
        for parent in p["parents"]:
            reports=[]
            for rnd in parent["accepted_rounds"]:
                reports.append({
                    "round":rnd,
                    "aia_published_date":"2023-01-01",
                    "canonical_candidate":candidate(rnd,22 if rnd==23 else rnd),
                })
            payloads.append({
                "parent":parent,
                "receipt":{
                    "classification":parent["expected_classification"],
                    "round_reports":reports,
                },
                "artifact_zip_sha256":parent["artifact_zip_sha256"],
                "receipt_sha256":parent["receipt_sha256"],
                "evidence_sha256":parent["evidence_sha256"],
            })
        ledger,anoms,prov=build_freeze(p,payloads)
        self.assertEqual(ledger["coverage_round_n"],38)
        self.assertEqual(len(ledger["rows"]),38)
        self.assertEqual(anoms["anomaly_n"],1)
        self.assertEqual(anoms["anomaly_rounds"],[23])
        r23=ledger["rows"][22]
        self.assertEqual(r23["round"],23)
        self.assertEqual(r23["identity_status"],"PASS_WITH_ANOMALY")
        self.assertFalse(anoms["body_content_used"])
        self.assertFalse(anoms["referee_assignment_body_used"])
        self.assertEqual(prov["coverage_round_n"],38)

    def test_zero_label_contract(self):
        p=json.loads(REG.read_text())
        h=p["hard_rules"]
        self.assertFalse(h["network_source_reacquisition_allowed"])
        self.assertFalse(h["search_allowed"])
        self.assertFalse(h["article_body_read"])
        self.assertFalse(h["referee_assignment_body_parsed"])
        self.assertFalse(h["result_labels_read"])
        self.assertFalse(h["score_values_read"])
        self.assertFalse(h["training_allowed"])
        self.assertFalse(h["scoring_allowed"])
        self.assertEqual(h["candidate_weight"],0)
        self.assertEqual(h["matrix_delta"],0)


if __name__=="__main__":
    unittest.main()
