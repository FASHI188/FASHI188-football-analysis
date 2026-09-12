from __future__ import annotations
import copy,json,unittest
from pathlib import Path
import validate_nextgen_dynamic_gating_prereg_v1 as v
HERE=Path(__file__).resolve().parent
C=json.loads((HERE/"nextgen_dynamic_gating_prereg_contract_v1.json").read_text(encoding="utf-8"))
class T(unittest.TestCase):
 def test_01_contract(self): self.assertGreaterEqual(len(v.validate(C)),60)
 def test_02_stop_inactive(self): self.assertEqual((C["status"],C["candidate"]["status"],C["candidate"]["weight"],C["candidate"]["matrix_delta"]),("STOP_DATA_COVERAGE","NOT_AVAILABLE",0,0))
 def test_03_stop_class(self): self.assertEqual(C["stop_class"],"UPSTREAM_ACTIVE_EXPERT_DEPENDENCY_EMPTY")
 def test_04_zero_label(self): self.assertTrue(all(x is False for x in C["zero_label_audit"].values()))
 def test_05_six_upstream_all_inactive(self):
  rows=C["upstream_candidate_registry"]; self.assertEqual(len(rows),6); self.assertTrue(all((r["expert_status"],r["weight"],r["matrix_delta"])==("NOT_AVAILABLE",0,0) for r in rows))
 def test_06_six_upstream_all_stopped(self): self.assertTrue(all(r["terminal_status"]=="STOP_DATA_COVERAGE" for r in C["upstream_candidate_registry"]))
 def test_07_active_count_zero(self): self.assertEqual(C["dependency_audit"]["active_expert_count"],0)
 def test_08_foundation_forbids_gate_weight(self): self.assertFalse(C["foundation_dependency"]["dynamic_gate_may_assign_nonzero_weight"])
 def test_09_baseline_identity(self): self.assertTrue(C["foundation_dependency"]["all_experts_inactive_returns_current_v2_object_unchanged"])
 def test_10_gate_not_defined(self): self.assertFalse(C["candidate"]["gate_function_defined_now"])
 def test_11_no_manual_activation(self): self.assertFalse(C["candidate"]["manual_expert_activation_allowed"])
 def test_12_legacy_pr_not_reusable(self): self.assertFalse(C["legacy_boundary"]["wholesale_cherry_pick_allowed"])
 def test_13_reopen_requires_promoted_experts(self): self.assertTrue(C["future_reopen_contract"]["at_least_two_upstream_experts_must_first_pass_their_own_independent_promotion_gates"])
 def test_14_reopen_requires_new_zero_label_amendment(self): self.assertTrue(C["future_reopen_contract"]["new_zero_label_DATA_READY_amendment_required_before_training"])
 def test_15_tamper_active_count_fails(self):
  b=copy.deepcopy(C); b["dependency_audit"]["active_expert_count"]=1
  with self.assertRaises(v.ContractError): v.validate(b)
 def test_16_tamper_weight_fails(self):
  b=copy.deepcopy(C); b["candidate"]["weight"]=0.01
  with self.assertRaises(v.ContractError): v.validate(b)
 def test_17_tamper_gate_defined_fails(self):
  b=copy.deepcopy(C); b["candidate"]["gate_function_defined_now"]=True
  with self.assertRaises(v.ContractError): v.validate(b)
 def test_18_formal_changes_forbidden(self): self.assertTrue(all(C["forbidden_changes"].values()))
if __name__=="__main__": unittest.main(verbosity=2)
