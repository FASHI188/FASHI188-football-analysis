from __future__ import annotations
import copy,json,unittest
from pathlib import Path
import validate_nextgen_selected_publishing_prereg_v1 as v
HERE=Path(__file__).resolve().parent
C=json.loads((HERE/"nextgen_selected_publishing_prereg_contract_v1.json").read_text(encoding="utf-8"))
class T(unittest.TestCase):
 def test_01_contract(self): self.assertGreaterEqual(len(v.validate(C)),60)
 def test_02_layer_not_expert(self): self.assertFalse(C["publishing_layer"]["is_expert"])
 def test_03_candidate_inactive(self): self.assertEqual((C["publishing_layer"]["candidate_status"],C["publishing_layer"]["weight"],C["publishing_layer"]["matrix_delta"]),("NOT_AVAILABLE",0,0))
 def test_04_research_infra_ready(self): self.assertEqual(C["status"],"RESEARCH_INFRASTRUCTURE_READY_BASELINE_ONLY")
 def test_05_zero_label(self): self.assertTrue(all(x is False for x in C["zero_label_audit"].values()))
 def test_06_seven_upstream_all_inactive(self): self.assertEqual(len(C["upstream_candidate_registry"]),7); self.assertTrue(all((r["expert_status"],r["weight"],r["matrix_delta"])==("NOT_AVAILABLE",0,0) for r in C["upstream_candidate_registry"]))
 def test_07_no_selection(self): self.assertEqual(C["selection_audit"]["selected_experts"],[]); self.assertFalse(C["selection_audit"]["selection_performed"])
 def test_08_baseline_only(self): self.assertTrue(C["selection_audit"]["baseline_only_output_must_remain_exact_current_v2"])
 def test_09_no_formal_publish(self): self.assertFalse(C["publishing_layer"]["formal_production_publish_allowed"])
 def test_10_no_formal_receipt_mutation(self): self.assertFalse(C["publishing_layer"]["formal_receipt_mutation_allowed"])
 def test_11_receipt_blob_locked(self): self.assertFalse(C["formal_receipt_distribution_boundary"]["candidate8_may_edit_or_replace"])
 def test_12_research_surface_separate(self): self.assertEqual(C["research_publication_contract"]["surface"],"SEPARATE_RESEARCH_GOVERNANCE_RECEIPT_ONLY")
 def test_13_prediction_sha_identity(self): self.assertTrue(C["research_publication_contract"]["prediction_sha_must_equal_current_v2"])
 def test_14_no_production_chain(self): self.assertFalse(C["publishing_layer"]["production_chain_created"])
 def test_15_tamper_selection_fails(self):
  b=copy.deepcopy(C); b["publishing_layer"]["selected_experts"]=["V3-C1-PIT-MARKET-TOTALS"]
  with self.assertRaises(v.ContractError): v.validate(b)
 def test_16_tamper_formal_publish_fails(self):
  b=copy.deepcopy(C); b["publishing_layer"]["formal_production_publish_allowed"]=True
  with self.assertRaises(v.ContractError): v.validate(b)
 def test_17_tamper_weight_fails(self):
  b=copy.deepcopy(C); b["publishing_layer"]["weight"]=0.01
  with self.assertRaises(v.ContractError): v.validate(b)
 def test_18_formal_changes_forbidden(self): self.assertTrue(all(C["forbidden_changes"].values()))
if __name__=="__main__": unittest.main(verbosity=2)
