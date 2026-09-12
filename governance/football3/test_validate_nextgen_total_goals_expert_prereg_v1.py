from __future__ import annotations
import copy,json,unittest
from pathlib import Path
import validate_nextgen_total_goals_expert_prereg_v1 as v
HERE=Path(__file__).resolve().parent
C=json.loads((HERE/"nextgen_total_goals_expert_prereg_contract_v1.json").read_text(encoding="utf-8"))
class T(unittest.TestCase):
 def test_01_contract(self): self.assertGreaterEqual(len(v.validate(C)),55)
 def test_02_stop_inactive(self): self.assertEqual((C["status"],C["candidate"]["status"],C["candidate"]["weight"],C["candidate"]["matrix_delta"]),("STOP_DATA_COVERAGE","NOT_AVAILABLE",0,0))
 def test_03_zero_label(self): self.assertTrue(all(x is False for x in C["zero_label_audit"].values()))
 def test_04_nonmarket_only(self): self.assertFalse(C["candidate"]["market_input_allowed"]); self.assertFalse(C["candidate"]["candidate1_market_totals_reuse_allowed"])
 def test_05_old_direct_total_already_screened(self): self.assertEqual(C["legacy_direct_total_evidence"]["round1"]["competition_count_built"],17)
 def test_06_no_jpn_rescue(self): self.assertTrue(C["legacy_direct_total_evidence"]["joint_matrix"]["old_single_domain_success_may_not_define_new_scope"])
 def test_07_strict_daily_is_old_baseline(self): self.assertEqual(C["legacy_shot_total_evidence"]["strict_daily_pit_repair"]["formal_current_version"],"V5.0.1")
 def test_08_freeze_no_backfill(self):
  f=C["legacy_shot_total_evidence"]["prospective_freeze"]; self.assertTrue(f["no_backfill"]); self.assertTrue(f["model_refit_after_freeze_forbidden"])
 def test_09_freeze_not_current_v2(self): self.assertFalse(C["legacy_shot_total_evidence"]["prospective_freeze"]["bound_to_current_formal_v2"])
 def test_10_source_row_times_missing(self):
  r=C["source_contract_audit"]["source_registry"]; self.assertFalse(r["row_level_source_observed_at"]); self.assertFalse(r["row_level_available_at"])
 def test_11_calendar_freeze_not_availability_timestamp(self): self.assertTrue(C["source_contract_audit"]["strict_calendar_day_freeze_reduces_same_day_leakage_but_does_not_create_missing_source_availability_metadata"])
 def test_12_reopen_requires_timestamped_collector(self): self.assertTrue(C["future_reopen_contract"]["timestamped_nonmarket_stat_collector_required"])
 def test_13_future_start_no_backfill(self): self.assertTrue(C["future_reopen_contract"]["future_enrollment_start_must_be_new_and_no_backfill"])
 def test_14_one_parameter_max(self): self.assertEqual(C["conditional_scientific_preregistration"]["maximum_free_parameter_count"],1)
 def test_15_tamper_weight_fails(self):
  b=copy.deepcopy(C); b["candidate"]["weight"]=0.01
  with self.assertRaises(v.ContractError): v.validate(b)
 def test_16_tamper_market_input_fails(self):
  b=copy.deepcopy(C); b["candidate"]["market_input_allowed"]=True
  with self.assertRaises(v.ContractError): v.validate(b)
 def test_17_tamper_available_at_fails(self):
  b=copy.deepcopy(C); b["source_contract_audit"]["source_registry"]["row_level_available_at"]=True
  with self.assertRaises(v.ContractError): v.validate(b)
 def test_18_formal_changes_forbidden(self): self.assertTrue(all(C["forbidden_changes"].values()))
if __name__=="__main__": unittest.main(verbosity=2)
