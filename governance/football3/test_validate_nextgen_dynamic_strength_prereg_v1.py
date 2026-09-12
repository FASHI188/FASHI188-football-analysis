from __future__ import annotations
import copy,json,unittest
from pathlib import Path
import validate_nextgen_dynamic_strength_prereg_v1 as v
HERE=Path(__file__).resolve().parent
C=json.loads((HERE/"nextgen_dynamic_strength_prereg_contract_v1.json").read_text(encoding="utf-8"))
class T(unittest.TestCase):
 def test_01_contract(self): self.assertGreaterEqual(len(v.validate(C)),40)
 def test_02_stop_inactive(self): self.assertEqual((C["status"],C["candidate"]["status"],C["candidate"]["weight"],C["candidate"]["matrix_delta"]),("STOP_DATA_COVERAGE","NOT_AVAILABLE",0,0))
 def test_03_zero_label(self): self.assertTrue(all(x is False for x in C["zero_label_audit"].values()))
 def test_04_inventory_exists_but_is_descriptive(self): self.assertEqual(C["repository_evidence"]["team_strength_manifest"]["total_matches"],27616); self.assertIn("descriptive",C["repository_evidence"]["team_strength_manifest"]["role"])
 def test_05_no_fixture_replay(self): self.assertFalse(C["repository_evidence"]["team_strength_builder"]["historical_fixture_level_replay_state_present"])
 def test_06_comp_scoped_identity_not_enough(self): self.assertFalse(C["repository_evidence"]["platform_core"]["cross_competition_club_identity_preserved"])
 def test_07_no_lower_division_inventory(self): self.assertFalse(C["repository_evidence"]["processed_inventory"]["lower_division_directories_for_big5_cold_start"])
 def test_08_no_promotion_lineage(self): self.assertFalse(C["repository_evidence"]["active_identity_registry"]["cross_competition_promotion_lineage_registry"])
 def test_09_clubelo_not_general_ready(self): self.assertEqual(C["repository_evidence"]["legacy_clubelo_ingest"]["fully_passed_domains"],["ITA_SerieA"])
 def test_10_scope_cannot_be_downgraded(self): self.assertFalse(C["candidate"]["partial_established_team_only_scope_accepted"]); self.assertTrue(C["coverage_gate"]["partial_established_team_only_reduction_forbidden"])
 def test_11_promoted_default_1500_is_forbidden(self): self.assertIn("PROMOTED_TEAM_DEFAULT_1500_IS_NOT_EVIDENCE_BASED_COLD_START",C["coverage_gate"]["current_failures"])
 def test_12_preserve_total_goals(self): self.assertEqual(C["conditional_scientific_preregistration"]["total_goals_constraint"],"P_C2(T) equals current V2 P(T) exactly")
 def test_13_one_parameter_only(self): s=C["conditional_scientific_preregistration"]; self.assertEqual((s["parameter_count"],s["parameter"],s["parameter_range"]),(1,"beta",[0.0,0.5]))
 def test_14_no_special_patches(self): s=C["conditional_scientific_preregistration"]; self.assertFalse(s["league_specific_beta_allowed"] or s["promoted_team_special_beta_allowed"] or s["manual_team_patch_allowed"])
 def test_15_tamper_weight_fails(self):
  b=copy.deepcopy(C); b["candidate"]["weight"]=0.01
  with self.assertRaises(v.ContractError): v.validate(b)
 def test_16_tamper_scope_downgrade_fails(self):
  b=copy.deepcopy(C); b["candidate"]["partial_established_team_only_scope_accepted"]=True
  with self.assertRaises(v.ContractError): v.validate(b)
 def test_17_tamper_target_access_fails(self):
  b=copy.deepcopy(C); b["candidate"]["target_label_access_allowed"]=True
  with self.assertRaises(v.ContractError): v.validate(b)
 def test_18_formal_changes_forbidden(self): self.assertTrue(all(C["forbidden_changes"].values()))
if __name__=="__main__": unittest.main(verbosity=2)
