from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from assembly.feature_assembler import FeatureAssembler
from governance.r43gov0.feature_integration_audit import (
    STATUSES,
    formal_numeric_families,
    verified_numerical_families,
)


class FeatureIntegrationAuditTests(unittest.TestCase):
    def test_required_feature_families_are_audited(self):
        self.assertEqual(
            set(STATUSES),
            {"lineup_pstart", "availability_status", "player_technical", "head_coach", "market_1x2_ah_ou"},
        )

    def test_no_family_is_currently_allowed_to_be_called_numerically_integrated(self):
        self.assertEqual(verified_numerical_families(), ())
        self.assertEqual(formal_numeric_families(), ())
        self.assertTrue(all(not s.formal_numeric_eligible for s in STATUSES.values()))

    def test_lineup_mechanism_pass_is_not_confused_with_1x2_numeric_use(self):
        s = STATUSES["lineup_pstart"]
        self.assertTrue(s.mechanism_gate_passed)
        self.assertFalse(s.numerical_consumer_exists)
        self.assertFalse(s.pit_bound_numeric_wiring)
        self.assertFalse(s.verified_numerical_integration)
        self.assertFalse(FeatureAssembler.DEFAULT_POLICIES["lineup_pstart"].numeric_effect_enabled)

    def test_availability_and_player_technical_remain_numeric_off(self):
        for family in ("availability_status", "player_technical"):
            s = STATUSES[family]
            self.assertFalse(s.mechanism_gate_passed)
            self.assertFalse(s.verified_numerical_integration)
            self.assertFalse(FeatureAssembler.DEFAULT_POLICIES[family].numeric_effect_enabled)

    def test_head_coach_is_not_even_a_registered_numeric_feature_family(self):
        s = STATUSES["head_coach"]
        self.assertFalse(s.recognized)
        self.assertFalse(s.pit_family_registered)
        self.assertFalse(s.mechanism_gate_passed)
        self.assertNotIn("head_coach", FeatureAssembler.DEFAULT_POLICIES)

    def test_market_has_numerical_consumer_but_not_pit_bound_wiring(self):
        s = STATUSES["market_1x2_ah_ou"]
        self.assertTrue(s.numerical_consumer_exists)
        self.assertFalse(s.pit_bound_numeric_wiring)
        self.assertFalse(s.verified_numerical_integration)
        self.assertFalse(s.formal_numeric_eligible)
        self.assertFalse(FeatureAssembler.DEFAULT_POLICIES["market_1x2_ah_ou"].numeric_effect_enabled)

    def test_evidence_blobs_are_pinned(self):
        self.assertEqual(STATUSES["lineup_pstart"].evidence_blob_sha, "c94de3e9427baef81aad6240fea7858edc169be8")
        self.assertEqual(STATUSES["player_technical"].evidence_blob_sha, "b4a5a38d81d037eae068cd4cdc59121304b5b09f")
        self.assertEqual(STATUSES["head_coach"].evidence_blob_sha, "f7e4c8c42d5327fb7b34320ba5a36a1ecc1e856a")
        self.assertEqual(STATUSES["market_1x2_ah_ou"].evidence_blob_sha, "299b86ed07e49af0b9ec5c7632f519e91e836158")


if __name__ == "__main__":
    unittest.main()
