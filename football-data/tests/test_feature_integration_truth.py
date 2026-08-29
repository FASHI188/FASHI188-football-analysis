from __future__ import annotations

import unittest

from assembly.feature_assembler import FeatureAssembler
from governance.feature_integration_truth import (
    FEATURE_TRUTH,
    assert_no_false_numeric_claims,
    numeric_enabled_families,
)


class FeatureIntegrationTruthTests(unittest.TestCase):
    def test_required_feature_families_are_explicit(self):
        self.assertEqual(
            set(FEATURE_TRUTH),
            {"lineup_pstart", "availability_status", "player_technical", "head_coach", "market_1x2_ah_ou"},
        )

    def test_no_feature_is_enabled_by_default(self):
        assert_no_false_numeric_claims()
        self.assertEqual(numeric_enabled_families(), ())

    def test_lineup_mechanism_pass_does_not_claim_1x2_effect(self):
        x = FEATURE_TRUTH["lineup_pstart"]
        self.assertTrue(x.historical_mechanism_gate_passed)
        self.assertFalse(x.numerical_consumer_exists)
        self.assertFalse(x.numeric_effect_enabled)
        self.assertFalse(x.formal_promotion_allowed)

    def test_player_technical_failed_promotion(self):
        x = FEATURE_TRUTH["player_technical"]
        self.assertFalse(x.historical_mechanism_gate_passed)
        self.assertTrue(x.numerical_consumer_exists)
        self.assertFalse(x.currently_pit_bound_to_consumer)
        self.assertFalse(x.numeric_effect_enabled)

    def test_head_coach_failed_and_has_no_pit_contract(self):
        x = FEATURE_TRUTH["head_coach"]
        self.assertFalse(x.historical_mechanism_gate_passed)
        self.assertFalse(x.pit_contract_available)
        self.assertFalse(x.numeric_effect_enabled)

    def test_market_is_pit_bound_research_candidate_but_default_disabled(self):
        x = FEATURE_TRUTH["market_1x2_ah_ou"]
        self.assertFalse(x.historical_mechanism_gate_passed)
        self.assertTrue(x.numerical_consumer_exists)
        self.assertTrue(x.pit_contract_available)
        self.assertTrue(x.currently_pit_bound_to_consumer)
        self.assertFalse(x.numeric_effect_enabled)
        self.assertFalse(x.formal_promotion_allowed)

    def test_feature_assembler_default_policies_do_not_overclaim_numeric_effect(self):
        assembler = FeatureAssembler()
        self.assertTrue(assembler.policy("lineup_pstart").experiment_passed)
        self.assertFalse(assembler.policy("lineup_pstart").numeric_effect_enabled)
        self.assertFalse(assembler.policy("player_technical").numeric_effect_enabled)
        self.assertFalse(assembler.policy("head_coach").numeric_effect_enabled)
        self.assertFalse(assembler.policy("market_1x2_ah_ou").experiment_passed)
        self.assertFalse(assembler.policy("market_1x2_ah_ou").numeric_effect_enabled)


if __name__ == "__main__":
    unittest.main()
