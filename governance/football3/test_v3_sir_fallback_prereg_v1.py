import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent

class TestSIRFallbackPrereg(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = json.loads((ROOT/'v3_sir_fallback_prereg_contract_v1.json').read_text())
        cls.i = json.loads((ROOT/'v3_sir_fallback_zero_label_inventory_v1.json').read_text())

    def test_route_is_fallback_only(self):
        self.assertEqual(self.c['scope']['route_population'], 'FROZEN_V1_EXACT_FALLBACK_ONLY')
        self.assertTrue(self.c['scope']['formal_v2_fusion_population_excluded'])

    def test_not_old_stage6_c_revival(self):
        self.assertFalse(self.c['scope']['old_stage6_c_revival'])
        self.assertFalse(self.c['scope']['old_stage6_c_evidence_reused'])

    def test_two_parameter_zero_identity(self):
        self.assertEqual(self.c['candidate']['parameter_count'], 2)
        self.assertTrue(self.c['candidate']['zero_vector_exact_baseline'])
        self.assertTrue(self.c['candidate']['score_support_unchanged'])

    def test_past_only_pit(self):
        p=self.c['pit_features']
        self.assertFalse(p['future_schedule_used'])
        self.assertFalse(p['cup_or_uefa_schedule_used'])
        self.assertFalse(p['final_lineup_used'])
        self.assertFalse(p['player_minutes_used'])
        self.assertTrue(p['post_match_backfill_forbidden'])

    def test_confirmation_is_reserved_and_sealed(self):
        self.assertEqual(self.c['cohorts']['sealed_confirmation_seasons'], ['2012/13','2013/14'])
        self.assertEqual(self.c['cohorts']['sealed_confirmation_theoretical_n'], 3652)
        self.assertEqual(self.i['confirmation_reservation']['admitted_n'], 0)
        self.assertEqual(self.i['confirmation_reservation']['status'], 'RESERVED_NOT_MATERIALIZED')

    def test_no_labels_training_or_tuning(self):
        self.assertEqual(self.i['result_values_read'], 0)
        self.assertEqual(self.i['goal_values_read'], 0)
        self.assertEqual(self.i['target_labels_opened'], 0)
        self.assertFalse(self.i['development_reservation']['labels_opened'])
        self.assertTrue(self.c['forbidden_changes']['training_in_this_batch'])
        self.assertTrue(self.c['forbidden_changes']['tuning_in_this_batch'])

    def test_confirmation_classification(self):
        self.assertEqual(self.c['confirmation']['classification'], 'SEALED_HISTORICAL_OOS_CONFIRMATION')
        self.assertTrue(self.c['confirmation']['prospective_pass_claim_forbidden'])
        self.assertTrue(self.c['confirmation']['prediction_receipts_before_result_read_required'])

    def test_required_n_fail_closed(self):
        self.assertIn('STOP_DATA_COVERAGE_PRE_LABEL', self.c['development']['confirmation_capacity_rule'])

    def test_frozen_inactive(self):
        self.assertEqual(self.c['inactive'], {'status':'NOT_AVAILABLE','weight':0,'matrix_delta':0,'data_ready':False})
        self.assertTrue(all(self.c['forbidden_changes'].values()))

if __name__ == '__main__':
    unittest.main()
