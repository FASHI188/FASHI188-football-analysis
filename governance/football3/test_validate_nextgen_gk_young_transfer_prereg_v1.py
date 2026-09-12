from __future__ import annotations
import copy,json,unittest
from pathlib import Path
import validate_nextgen_gk_young_transfer_prereg_v1 as v
HERE=Path(__file__).resolve().parent
C=json.loads((HERE/"nextgen_gk_young_transfer_prereg_contract_v1.json").read_text(encoding="utf-8"))
class T(unittest.TestCase):
 def test_01_contract(self): self.assertGreaterEqual(len(v.validate(C)),55)
 def test_02_stop_inactive(self): self.assertEqual((C["status"],C["candidate"]["status"],C["candidate"]["weight"],C["candidate"]["matrix_delta"]),("STOP_DATA_COVERAGE","NOT_AVAILABLE",0,0))
 def test_03_zero_label(self): self.assertTrue(all(x is False for x in C["zero_label_audit"].values()))
 def test_04_candidate3_not_silently_inherited(self): self.assertEqual(C["upstream_dependency"]["candidate_status"],"STOP_DATA_COVERAGE")
 def test_05_gk_old_not_fresh(self): self.assertFalse(C["repository_evidence"]["goalkeeper_replication_receipt"]["fresh_confirmation"])
 def test_06_dob_exists_but_not_availability(self):
  p=C["repository_evidence"]["player_age_and_value_readiness"]; self.assertIn("date_of_birth",p["player_fields_include"]); self.assertTrue(p["current_profile_fields_are_not_historical_availability_proof"])
 def test_07_transfer_time_gap(self):
  t=C["repository_evidence"]["dated_transfer_contract"]; self.assertFalse(t["has_historical_source_observed_at_contract"]); self.assertFalse(t["has_historical_available_at_contract"])
 def test_08_cross_league_disabled(self): self.assertFalse(C["repository_evidence"]["dated_transfer_contract"]["cross_competition_strength_borrowing"])
 def test_09_coverage_not_full(self):
  x=C["repository_evidence"]["dated_transfer_coverage_receipt"]; self.assertEqual((x["chronological_oof_ready_count"],x["stage_adapter_required_count"],x["partial_or_unavailable_count"]),(10,6,1)); self.assertFalse(x["all_17_directly_ready"])
 def test_10_jpn_gap(self): self.assertEqual(C["repository_evidence"]["dated_transfer_coverage_receipt"]["partial_or_unavailable"],["JPN_J1"])
 def test_11_no_partial_rescue(self): self.assertFalse(C["candidate"]["partial_league_rescue_allowed"])
 def test_12_old_dynamic_oof_not_fresh(self): self.assertFalse(C["repository_evidence"]["legacy_dynamic_strength_oof"]["reuse_as_fresh_confirmation"])
 def test_13_provider_requires_observed_available(self):
  r=set(C["provider_contract_gap"]["architecture_requires_per_source"]); self.assertIn("observed_at",r); self.assertIn("available_at",r)
 def test_14_one_parameter_max(self):
  s=C["conditional_scientific_preregistration"]; self.assertEqual((s["maximum_free_parameter_count"],s["single_scale_parameter_name"]),(1,"kappa"))
 def test_15_tamper_weight_fails(self):
  b=copy.deepcopy(C); b["candidate"]["weight"]=0.01
  with self.assertRaises(v.ContractError): v.validate(b)
 def test_16_tamper_partial_rescue_fails(self):
  b=copy.deepcopy(C); b["candidate"]["partial_league_rescue_allowed"]=True
  with self.assertRaises(v.ContractError): v.validate(b)
 def test_17_tamper_transfer_available_at_fails(self):
  b=copy.deepcopy(C); b["repository_evidence"]["dated_transfer_contract"]["has_historical_available_at_contract"]=True
  with self.assertRaises(v.ContractError): v.validate(b)
 def test_18_formal_changes_forbidden(self): self.assertTrue(all(C["forbidden_changes"].values()))
if __name__=="__main__": unittest.main(verbosity=2)
