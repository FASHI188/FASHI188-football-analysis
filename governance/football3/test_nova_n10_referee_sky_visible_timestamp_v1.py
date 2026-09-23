from __future__ import annotations

import json
import unittest
from pathlib import Path

from nova_n10_referee_sky_visible_timestamp_v1 import (
    parse_chunks_until_marker,
    prefixture_pass,
    title_matches,
)

REG=Path(__file__).with_name("nova_n10_referee_sky_visible_timestamp_registry_v1.json")

class T(unittest.TestCase):
    def test_registry_contract(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"5b46bb6d30791fb3af624b9c438915bd03b8334b")
        self.assertEqual([s["round"] for s in p["samples"]],[1,10,19,28,38])
        self.assertFalse(p["hard_rules"]["article_body_read"])
        self.assertFalse(p["hard_rules"]["summary_text_read"])
        self.assertFalse(p["hard_rules"]["referee_assignment_body_parsed"])
        self.assertFalse(p["prefix_contract"]["chunk_overshoot_parsed"])
        self.assertFalse(p["prefix_contract"]["chunk_overshoot_persisted"])

    def test_title_match(self):
        self.assertTrue(title_matches(
            "Arbitri Serie A, le designazioni per la 10^ giornata | Sky Sport",
            ["Arbitri","Serie A","10","giornata"],
        ))
        self.assertFalse(title_matches(
            "Serie A 11 giornata",
            ["Arbitri","Serie A","10","giornata"],
        ))

    def test_marker_stream_stops_and_discards_overshoot(self):
        chunks=[
            b"<html><head><title>Arbitri Serie A, le designazioni per la 10^ giornata | Sky Sport</title></head>",
            b"<body><h1>Arbitri Serie A</h1><div>12 ott 2022 - 12:19</div>",
            b"<p>SUMMARY MUST NOT BE PARSED</p>",
        ]
        r=parse_chunks_until_marker(
            chunks,
            required_title_terms=["Arbitri","Serie A","10","giornata"],
            expected_date="12 ott 2022",
            expected_time="12:19",
            max_bytes=10000,
        )
        self.assertTrue(r["marker_found"])
        self.assertTrue(r["title_identity_pass"])
        self.assertFalse(r["overshoot_parsed"])
        self.assertFalse(r["overshoot_persisted"])
        self.assertFalse(r["raw_prefix_persisted"])
        self.assertEqual(len(r["prefix_sha256"]),64)
        self.assertGreaterEqual(r["overshoot_bytes_discarded"],0)

    def test_prefixture(self):
        self.assertTrue(prefixture_pass({
            "expected_display_local":"2023-05-31T13:10:00",
            "first_fixture_local":"2023-06-02T20:30:00",
        }))
        self.assertFalse(prefixture_pass({
            "expected_display_local":"2023-06-03T13:10:00",
            "first_fixture_local":"2023-06-02T20:30:00",
        }))

    def test_all_frozen_markers_have_minute_precision(self):
        p=json.loads(REG.read_text())
        for s in p["samples"]:
            self.assertRegex(s["expected_marker_time"],r"^\d{2}:\d{2}$")
            self.assertIn("T",s["expected_display_local"])
            self.assertTrue(prefixture_pass(s))

if __name__=="__main__":
    unittest.main()
