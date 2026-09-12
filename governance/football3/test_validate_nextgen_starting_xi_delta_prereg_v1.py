from __future__ import annotations
import copy,json,unittest
from pathlib import Path
import validate_nextgen_starting_xi_delta_prereg_v1 as v
HERE=Path(__file__).resolve().parent
C=json.loads((HERE/"nextgen_starting_xi_delta_prereg_contract_v1.json").read_text(encoding="utf-8"))
class T(unittest.TestCase):
 def test_01_contract(self): self.assertGreaterEqual(len(v.validate(C)),55)
 def test_02_stop_inactive(self): self.assertEqual((C["status"],C["candidate"]["status"],C["candidate"]["weight"],C["candidate"]["matrix_delta"]),("STOP_DATA_COVERAGE","NOT_AVAILABLE",0,0))
 def test_03_zero_label(self): self.assertTrue(all(x is False for x in C["zero_label_audit"].values()))
 def test_04_historical_xi_not_prematch_input(self):
  e=C["repository_evidence"]["lineup_ingest"]; self.assertFalse(e["upstream_has_exact_kickoff"]); self.assertFalse(e["upstream_has_lineup_publication_timestamp"]); self.assertFalse(e["own_match_lineup_pre_kickoff_known_claimed"])
 def test_05_availability_zero(self): self.assertEqual(C["repository_evidence"]["data_readiness"]["availability_domain_count"],0)
 def test_06_only_five_shadow_domains(self): self.assertEqual(C["repository_evidence"]["data_readiness"]["trainable_lineup_domain_count"],5)
 def test_07_identity_not_availability(self): self.assertTrue(C["repository_evidence"]["lineup_match_identity"]["identity_bridge_is_not_pre_cutoff_availability_evidence"])
 def test_08_shadow_freeze_not_t15(self): self.assertEqual(C["repository_evidence"]["lineup_shadow_route"]["current_target_freeze_semantics"],"target kickoff, not canonical T-15 cutoff")
 def test_09_legacy_closed_zero(self): self.assertEqual((C["repository_evidence"]["legacy_player_xi_final_registry"]["status"],C["repository_evidence"]["legacy_player_xi_final_registry"]["formal_weight"]),("RESEARCH_LAYER_CLOSED_KEEP_FORMAL_WEIGHT_0",0))
 def test_10_old_outer_seasons_not_fresh(self): self.assertFalse(C["repository_evidence"]["legacy_player_xi_final_registry"]["reuse_as_fresh_confirmation"])
 def test_11_no_posthoc_five_league(self): self.assertTrue(C["coverage_gate"]["partial_five_league_scope_selection_after_old_results_forbidden"])
 def test_12_t15_contract(self): self.assertEqual(C["pit_contract_if_future_data_ready"]["master_cutoff"],"T-15m")
 def test_13_preserve_total_goals(self): self.assertEqual(C["conditional_scientific_preregistration"]["total_goals_constraint"],"P_C3(T) equals current V2 P(T) exactly")
 def test_14_one_parameter_only(self):
  s=C["conditional_scientific_preregistration"]; self.assertEqual((s["parameter_count"],s["parameter"],s["parameter_range"]),(1,"gamma",[0.0,0.4]))
 def test_15_tamper_weight_fails(self):
  b=copy.deepcopy(C); b["candidate"]["weight"]=0.01
  with self.assertRaises(v.ContractError): v.validate(b)
 def test_16_tamper_actual_xi_input_fails(self):
  b=copy.deepcopy(C); b["candidate"]["post_cutoff_actual_xi_allowed_as_input"]=True
  with self.assertRaises(v.ContractError): v.validate(b)
 def test_17_tamper_target_access_fails(self):
  b=copy.deepcopy(C); b["candidate"]["target_label_access_allowed"]=True
  with self.assertRaises(v.ContractError): v.validate(b)
 def test_18_formal_changes_forbidden(self): self.assertTrue(all(C["forbidden_changes"].values()))
if __name__=="__main__": unittest.main(verbosity=2)
