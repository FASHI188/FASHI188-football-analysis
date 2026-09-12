from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

import validate_nextgen_market_pit_prereg_v1 as validator

HERE = Path(__file__).resolve().parent
CONTRACT = json.loads((HERE / "nextgen_market_pit_prereg_contract_v1.json").read_text(encoding="utf-8"))


class CandidateOnePreregContractTests(unittest.TestCase):
    def test_01_authoritative_contract_passes(self):
        checks = validator.validate(CONTRACT)
        self.assertGreaterEqual(len(checks), 45)

    def test_02_candidate_is_stopped_and_inactive(self):
        self.assertEqual(CONTRACT["status"], "STOP_DATA_COVERAGE")
        candidate = CONTRACT["candidate"]
        self.assertEqual((candidate["status"], candidate["weight"], candidate["matrix_delta"]), ("NOT_AVAILABLE", 0, 0))

    def test_03_no_labels_training_tuning_or_provider_calls(self):
        z = CONTRACT["zero_label_audit"]
        self.assertTrue(all(v is False for v in z.values()))

    def test_04_zero_market_acquisition_is_hard_blocker(self):
        ge = CONTRACT["repository_evidence"]["global_alignment"]
        self.assertEqual(ge["market_jsonl_files"], 0)
        self.assertEqual(ge["market_rows"], 0)
        self.assertFalse(ge["timestamped_historical_market_two_season_backfill_complete"])

    def test_05_missing_credentials_are_not_reinterpreted_as_data(self):
        creds = CONTRACT["repository_evidence"]["global_alignment"]["credential_status"]
        self.assertTrue(creds)
        self.assertTrue(all(v is False for v in creds.values()))

    def test_06_multi_line_totals_are_required(self):
        n = CONTRACT["market_normalization_preregistration"]
        self.assertFalse(n["ordinary_featured_totals_single_line_alone_is_sufficient"])
        self.assertTrue(n["alternate_totals_or_materially_equivalent_multi_line_surface_required"])

    def test_07_pit_is_T_minus_15_and_fail_closed(self):
        p = CONTRACT["pit_contract_if_future_data_ready"]
        self.assertEqual(p["master_cutoff"], "T-15m")
        self.assertTrue(p["reject_timezone_naive"])
        self.assertTrue(p["reject_post_cutoff_snapshot"])
        self.assertTrue(p["reject_post_cutoff_market_update"])

    def test_08_dynamic_provider_failures_are_separate(self):
        d = CONTRACT["pit_contract_if_future_data_ready"]["dynamic_failures"]
        self.assertEqual(d["http_503"], "EXTERNAL_DYNAMIC_DATA_FAILURE")
        self.assertEqual(d["http_429"], "EXTERNAL_DYNAMIC_DATA_FAILURE")
        self.assertEqual(d["timeout"], "EXTERNAL_DYNAMIC_DATA_FAILURE")

    def test_09_candidate_form_only_changes_total_goals_marginal(self):
        s = CONTRACT["conditional_scientific_preregistration"]
        self.assertEqual(s["parameter_count"], 1)
        self.assertEqual(s["parameter_range"], [0.0, 0.35])
        self.assertIn("D(H,A|T) remains exactly current V2", s["distribution_constraint"])
        self.assertIn("P_C1(H,A)", s["matrix_reconstruction"])

    def test_10_no_posthoc_method_shopping(self):
        s = CONTRACT["conditional_scientific_preregistration"]
        self.assertFalse(s["league_specific_weights_allowed"])
        self.assertFalse(s["post_hoc_line_subset_allowed"])
        self.assertFalse(s["post_hoc_cutoff_change_allowed"])
        self.assertFalse(s["post_hoc_provider_subset_allowed"])
        self.assertFalse(s["optional_stopping"])

    def test_11_sealed_pools_remain_unread(self):
        pools = CONTRACT["sample_isolation"]["sealed_pools"]
        self.assertEqual({x["name"] for x in pools}, {"C070-F Confirmation1597", "N17 reserve266", "N18C confirmation150"})
        self.assertTrue(all(x["authorized_access"] is False for x in pools))

    def test_12_formal_surfaces_are_forbidden(self):
        self.assertTrue(all(CONTRACT["forbidden_changes"].values()))

    def test_13_tampering_nonzero_weight_fails(self):
        bad = copy.deepcopy(CONTRACT)
        bad["candidate"]["weight"] = 0.01
        with self.assertRaises(validator.ContractError):
            validator.validate(bad)

    def test_14_tampering_target_access_fails(self):
        bad = copy.deepcopy(CONTRACT)
        bad["candidate"]["target_label_access_allowed"] = True
        with self.assertRaises(validator.ContractError):
            validator.validate(bad)

    def test_15_tampering_single_line_sufficiency_fails(self):
        bad = copy.deepcopy(CONTRACT)
        bad["market_normalization_preregistration"]["ordinary_featured_totals_single_line_alone_is_sufficient"] = True
        with self.assertRaises(validator.ContractError):
            validator.validate(bad)

    def test_16_numeric_success_gate_cannot_be_invented_on_stopped_data(self):
        self.assertFalse(CONTRACT["conditional_scientific_preregistration"]["numeric_success_line_frozen_now"])
        self.assertIn("before target labels", CONTRACT["conditional_scientific_preregistration"]["numeric_success_line_policy"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
