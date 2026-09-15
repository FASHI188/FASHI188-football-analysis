#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from validate_nova_free_history_source_catalog_v1 import CatalogError, validate_catalog

ROOT = Path(__file__).resolve().parent
CATALOG = ROOT / "nova_free_history_source_catalog_v1.json"


class FreeHistorySourceCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = json.loads(CATALOG.read_text(encoding="utf-8"))

    def test_current_catalog_passes(self) -> None:
        receipt = validate_catalog(copy.deepcopy(self.data))
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["labels_opened"], 0)
        self.assertEqual(receipt["n1_big3_status"], "STOP_DATA_COVERAGE")
        self.assertEqual(
            receipt["n1_big3_exact_coverage"],
            {"Bundesliga": False, "Ligue_1": False, "Serie_A": False},
        )
        self.assertGreaterEqual(receipt["qualified_reusable_source_count"], 8)

    def test_pappalardo_is_reusable_but_not_understat_exact(self) -> None:
        source = next(s for s in self.data["sources"] if s["source_id"] == "PAPPALARDO_WYSCOUT_2017_18_BIG5")
        self.assertTrue(source["historical_library_eligible"])
        self.assertEqual(source["license_id"], "CC-BY-4.0")
        self.assertFalse(source["n1_exact_feature_eligible"])
        self.assertFalse(source["deep_ppda"])

    def test_technical_big3_sources_stay_blocked_without_license(self) -> None:
        for source_id in (
            "CODY_UNDERSTAT_DATA_2014_2025",
            "QWSNXNJENE_FOOTBALL_PREDICTION_RAW",
            "OBISERRA_SOCCER_STATS",
        ):
            source = next(s for s in self.data["sources"] if s["source_id"] == source_id)
            self.assertFalse(source["historical_library_eligible"])
            self.assertFalse(source["n1_exact_feature_eligible"])
            self.assertTrue(source["status"].startswith("BLOCKED_LICENSE"))

    def test_reusable_source_cannot_have_unknown_license(self) -> None:
        mutated = copy.deepcopy(self.data)
        mutated["sources"][0]["license_status"] = "UNKNOWN"
        with self.assertRaises(CatalogError):
            validate_catalog(mutated)

    def test_reusable_source_must_be_free(self) -> None:
        mutated = copy.deepcopy(self.data)
        mutated["sources"][0]["free_access"] = False
        with self.assertRaises(CatalogError):
            validate_catalog(mutated)

    def test_exact_source_requires_match_level_deep_ppda(self) -> None:
        mutated = copy.deepcopy(self.data)
        s = mutated["sources"][0]
        s["n1_exact_feature_eligible"] = True
        s["n1_exact_scope"] = ["Bundesliga 2024/25"]
        s["match_level"] = True
        s["deep_ppda"] = False
        with self.assertRaises(CatalogError):
            validate_catalog(mutated)

    def test_exact_coverage_turns_ready_only_when_all_big3_are_qualified(self) -> None:
        mutated = copy.deepcopy(self.data)
        for league, source_id in (
            ("Bundesliga", "MADFERIT_LALIGA_2014_2025"),
            ("Serie_A", "SMARTPLAY_EPL_2024_25"),
            ("Ligue_1", "PAPPALARDO_WYSCOUT_2017_18_BIG5"),
        ):
            source = next(s for s in mutated["sources"] if s["source_id"] == source_id)
            source["historical_library_eligible"] = True
            source["free_access"] = True
            source["license_status"] = "VERIFIED_REUSABLE"
            source["license_id"] = source["license_id"] or "CC-BY-4.0"
            source["match_level"] = True
            source["deep_ppda"] = True
            source["n1_exact_feature_eligible"] = True
            source["n1_exact_scope"] = [f"{league} 2024/25"]
        mutated["global_conclusion"]["n1_big3_2024_25_exact_deep_ppda_licensed_free_coverage_complete"] = True
        mutated["global_conclusion"]["n1_big3_status"] = "READY_FOR_ZERO_LABEL_IDENTITY_COVERAGE"
        receipt = validate_catalog(mutated)
        self.assertEqual(receipt["n1_big3_status"], "READY_FOR_ZERO_LABEL_IDENTITY_COVERAGE")
        self.assertTrue(all(receipt["n1_big3_exact_coverage"].values()))

    def test_duplicate_source_id_rejected(self) -> None:
        mutated = copy.deepcopy(self.data)
        mutated["sources"].append(copy.deepcopy(mutated["sources"][0]))
        with self.assertRaises(CatalogError):
            validate_catalog(mutated)

    def test_target_scope_drift_rejected(self) -> None:
        mutated = copy.deepcopy(self.data)
        mutated["target_exact_n1_scope"]["season"] = "2023/24"
        with self.assertRaises(CatalogError):
            validate_catalog(mutated)

    def test_no_formal_state_mutation(self) -> None:
        receipt = validate_catalog(copy.deepcopy(self.data))
        self.assertFalse(receipt["formal_v2_changed"])
        self.assertFalse(receipt["current_changed"])
        self.assertFalse(receipt["production_changed"])


if __name__ == "__main__":
    unittest.main()
