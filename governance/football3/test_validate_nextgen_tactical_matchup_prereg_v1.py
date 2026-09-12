from __future__ import annotations
import copy,json,unittest
from pathlib import Path
import validate_nextgen_tactical_matchup_prereg_v1 as v
HERE=Path(__file__).resolve().parent
C=json.loads((HERE/"nextgen_tactical_matchup_prereg_contract_v1.json").read_text(encoding="utf-8"))
class T(unittest.TestCase):
 def test_01_contract(self): self.assertGreaterEqual(len(v.validate(C)),55)
 def test_02_stop_inactive(self): self.assertEqual((C["status"],C["candidate"]["status"],C["candidate"]["weight"],C["candidate"]["matrix_delta"]),("STOP_DATA_COVERAGE","NOT_AVAILABLE",0,0))
 def test_03_zero_label(self): self.assertTrue(all(x is False for x in C["zero_label_audit"].values()))
 def test_04_single_axis_only(self): self.assertFalse(C["candidate"]["multiple_tactical_experts_allowed"])
 def test_05_no_posthoc_axis(self): self.assertFalse(C["candidate"]["posthoc_axis_selection_allowed"])
 def test_06_understat_only_five_domains(self):
  s=C["current_repository_tactical_evidence"]["understat_panel_status"]; self.assertEqual((s["covered_domain_count"],s["registered_formal_domain_count"]),(5,17)); self.assertFalse(s["all_17_covered"])
 def test_07_old_development_is_viewed(self): self.assertFalse(C["legacy_stage6_b_development_evidence"]["may_be_reused_as_fresh_confirmation"])
 def test_08_old_positive_result_cannot_select_axis(self): self.assertFalse(C["legacy_stage6_b_development_evidence"]["may_define_current_axis_because_positive"])
 def test_09_legacy_prospective_fully_enrolled(self):
  q=C["legacy_stage6_b_prospective_evidence"]; self.assertEqual((q["required_n"],q["receipt_n"],q["active_n"],q["fallback_n"]),(1335,1335,1335,0))
 def test_10_legacy_labels_sealed(self):
  q=C["legacy_stage6_b_prospective_evidence"]; self.assertFalse(q["target_labels_opened"]); self.assertEqual(q["target_result_or_goal_values_read"],0); self.assertFalse(q["interim_scoring"])
 def test_11_legacy_baseline_not_current_nextgen(self): self.assertEqual(C["legacy_stage6_b_prospective_evidence"]["baseline"],"Frozen V3.1.1")
 def test_12_legacy_receipts_untouched(self):
  q=C["legacy_stage6_b_prospective_evidence"]; self.assertTrue(q["must_remain_untouched_by_current_candidate"]); self.assertTrue(q["may_not_be_rebound_to_current_nextgen_baseline"])
 def test_13_current_axis_not_selected(self): self.assertEqual(C["conditional_scientific_preregistration"]["axis_definition_now"],"NOT_SELECTED_DUE_TO_POSTHOC_GUARD")
 def test_14_future_requires_new_cutoff(self): self.assertTrue(C["future_reopen_contract"]["new_current_v2_bound_candidate_requires_new_future_cutoff"])
 def test_15_tamper_weight_fails(self):
  b=copy.deepcopy(C); b["candidate"]["weight"]=0.01
  with self.assertRaises(v.ContractError): v.validate(b)
 def test_16_tamper_legacy_labels_fails(self):
  b=copy.deepcopy(C); b["legacy_stage6_b_prospective_evidence"]["target_labels_opened"]=True
  with self.assertRaises(v.ContractError): v.validate(b)
 def test_17_tamper_posthoc_axis_fails(self):
  b=copy.deepcopy(C); b["candidate"]["posthoc_axis_selection_allowed"]=True
  with self.assertRaises(v.ContractError): v.validate(b)
 def test_18_formal_changes_forbidden(self): self.assertTrue(all(C["forbidden_changes"].values()))
if __name__=="__main__": unittest.main(verbosity=2)
