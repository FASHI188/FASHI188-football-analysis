from __future__ import annotations

import unittest
from pathlib import Path
import json

from nova_n10_referee_sky_publication_witness_v1 import (
    extract_metadata,
    normalize_text,
    parse_timestamp,
    title_identity_pass,
)

REG=Path(__file__).with_name("nova_n10_referee_sky_publication_witness_registry_v1.json")

class T(unittest.TestCase):
    def test_registry_contract(self):
        p=json.loads(REG.read_text())
        self.assertEqual(
            p["exact_base"],
            "ab3c8ec17390c2542ff62eb14ad8590c53eb5f8e",
        )
        self.assertEqual([x["round"] for x in p["samples"]],[1,10,19,28,38])
        self.assertFalse(p["hard_rules"]["article_body_read"])
        self.assertFalse(p["hard_rules"]["referee_assignment_body_parsed"])
        self.assertFalse(p["hard_rules"]["training_allowed"])
        self.assertFalse(p["metadata_contract"]["independent_immutable_archive_witness"])

    def test_extract_head_metadata(self):
        raw=b'''<html><head>
        <title>Arbitri Serie A, le designazioni per la 10^ giornata | Sky Sport</title>
        <meta property="article:published_time" content="2022-10-12T10:19:00Z">
        <script type="application/ld+json">{"datePublished":"2022-10-12T10:19:00Z"}</script>
        </head>'''
        m=extract_metadata(raw)
        self.assertTrue(any("10^ giornata" in x for x in m["title_candidates"]))
        vals=[x["value"] for x in m["timestamp_candidates"]]
        self.assertIn("2022-10-12T10:19:00Z",vals)

    def test_timezone_conversion(self):
        x=parse_timestamp("2022-10-12T10:19:00Z","Europe/Rome")
        self.assertIsNotNone(x)
        self.assertEqual(x.replace(tzinfo=None).isoformat(),"2022-10-12T12:19:00")

    def test_title_identity(self):
        self.assertTrue(title_identity_pass(
            ["Serie A, 28^ giornata: orari, arbitri e squalificati | Sky Sport"],
            ["Serie A","28","giornata","arbitri"],
        ))
        self.assertFalse(title_identity_pass(
            ["Serie A, 27^ giornata"],
            ["Serie A","28","giornata","arbitri"],
        ))

    def test_normalize_text(self):
        self.assertEqual(normalize_text("  A &amp;  B  "),"A & B")

if __name__=="__main__":
    unittest.main()
