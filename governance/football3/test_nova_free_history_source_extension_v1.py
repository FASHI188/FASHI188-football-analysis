#!/usr/bin/env python3
from __future__ import annotations
import json, tempfile, unittest
from pathlib import Path
from validate_nova_free_history_source_extension_v1 import validate

HERE = Path(__file__).resolve().parent
CATALOG = HERE / "nova_free_history_source_extension_open_tracking_v1.json"

class ExtensionTests(unittest.TestCase):
    def test_catalog_passes(self):
        r = validate(CATALOG)
        self.assertEqual(r["status"], "PASS")
        self.assertEqual(r["qualified_reusable_source_count"], 4)
        self.assertEqual(r["qualified_tracking_source_count"], 2)
        self.assertEqual(r["qualified_identity_result_backbone_count"], 1)
        self.assertEqual(r["n1_big3_status"], "STOP_DATA_COVERAGE")
        self.assertEqual(r["labels_opened"], 0)

    def test_no_source_can_silently_become_n1_exact(self):
        data = json.loads(CATALOG.read_text(encoding="utf-8"))
        data["sources"][0]["n1_exact_feature_eligible"] = True
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bad.json"
            p.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError):
                validate(p)

    def test_ineligible_source_cannot_claim_qualified_status_semantics(self):
        data = json.loads(CATALOG.read_text(encoding="utf-8"))
        metrica = next(x for x in data["sources"] if x["source_id"] == "METRICA_SAMPLE_DATA")
        self.assertFalse(metrica["historical_library_eligible"])
        self.assertEqual(metrica["status"], "BLOCKED_LICENSE_FORMALITY")

    def test_registration_gate_is_explicit(self):
        data = json.loads(CATALOG.read_text(encoding="utf-8"))
        gated = next(x for x in data["sources"] if x["source_id"] == "PEGGY44_SKILLCORNER_EPL_2024_25")
        self.assertTrue(gated["registration_required"])
        self.assertEqual(gated["license_id"], "CC-BY-NC-4.0")

if __name__ == "__main__":
    unittest.main()
