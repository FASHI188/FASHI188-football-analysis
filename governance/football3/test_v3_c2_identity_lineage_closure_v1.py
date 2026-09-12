from datetime import datetime, timezone
import json, unittest
from pathlib import Path
import validate_v3_c2_identity_lineage_closure_v1 as v
from v3_c2_identity_lineage_materializer_v1 import *
HERE=Path(__file__).resolve().parent
C=json.loads((HERE/"v3_c2_identity_lineage_closure_contract_v1.json").read_text())
I=json.loads((HERE/"v3_c2_identity_source_inventory_v1.json").read_text())
UTC=timezone.utc
class T(unittest.TestCase):
 def test_01_contract(self): self.assertGreaterEqual(len(v.validate(C,I)),18)
 def test_02_tm_gap(self): self.assertFalse(I["sources"]["transfermarkt_public_dvc"]["historical_cutoff_safe_membership_snapshot_proven"])
 def test_03_wd_cc0(self): self.assertEqual(I["sources"]["wikidata"]["license_state"],"CC0_STRUCTURED_DATA")
 def test_04_wd_incomplete(self): self.assertEqual(I["sources"]["wikidata"]["team_property_expected_completeness"],"always incomplete")
 def test_05_of_names_not_ids(self): self.assertFalse(I["sources"]["openfootball_git"]["provider_stable_club_id_in_fixture_files"])
 def test_06_no_data_ready(self): self.assertFalse(C["candidate"]["data_ready"])
 def test_07_weight_zero(self): self.assertEqual(C["candidate"]["weight"],0)
 def test_08_matrix_zero(self): self.assertEqual(C["candidate"]["matrix_delta"],0)
 def test_09_no_labels(self): self.assertFalse(C["zero_label_audit"]["target_labels_read"])
 def test_10_no_training(self): self.assertFalse(C["zero_label_audit"]["training_performed"])
 def test_11_exact_identity(self):
  a=IdentityAssertion("wd","P7223","31","club:31",datetime(2025,1,1,tzinfo=UTC),datetime(2025,1,2,tzinfo=UTC),"a"*64,"EXTERNAL_STABLE_ID_EQUIVALENCE")
  self.assertEqual(exact_identity_join([a],datetime(2025,2,1,tzinfo=UTC))["P7223:31"],"club:31")
 def test_12_late_identity_excluded(self):
  a=IdentityAssertion("wd","P7223","31","club:31",datetime(2025,2,1,tzinfo=UTC),datetime(2025,2,2,tzinfo=UTC),"a"*64,"EXTERNAL_STABLE_ID_EQUIVALENCE")
  self.assertEqual(exact_identity_join([a],datetime(2025,2,1,tzinfo=UTC)),{})
 def test_13_fuzzy_basis_rejected(self):
  with self.assertRaises(IdentityLineageError): IdentityAssertion("x","name","A","club:a",datetime(2025,1,1,tzinfo=UTC),datetime(2025,1,1,tzinfo=UTC),"a"*64,"FUZZY").validate()
 def test_14_identity_conflict(self):
  a=IdentityAssertion("x","id","1","a",datetime(2025,1,1,tzinfo=UTC),datetime(2025,1,1,tzinfo=UTC),"a"*64,"PROVIDER_STABLE_ID")
  b=IdentityAssertion("x","id","1","b",datetime(2025,1,1,tzinfo=UTC),datetime(2025,1,1,tzinfo=UTC),"b"*64,"PROVIDER_STABLE_ID")
  with self.assertRaises(IdentityLineageError): exact_identity_join([a,b],datetime(2025,2,1,tzinfo=UTC))
 def test_15_staying(self): self.assertEqual(classify_movement(club_id="a",prior_competition="T",target_competition="T",target_top="T",approved_lower="L"),"STAYING")
 def test_16_promoted(self): self.assertEqual(classify_movement(club_id="a",prior_competition="L",target_competition="T",target_top="T",approved_lower="L"),"PROMOTED")
 def test_17_returning(self): self.assertEqual(classify_movement(club_id="a",prior_competition="L",target_competition="T",target_top="T",approved_lower="L",earlier_top_membership=True),"RETURNING")
 def test_18_relegated(self): self.assertEqual(classify_movement(club_id="a",prior_competition="T",target_competition="L",target_top="T",approved_lower="L"),"RELEGATED")
 def test_19_unknown_conflict(self): self.assertEqual(classify_movement(club_id="a",prior_competition="T",target_competition="T",target_top="T",approved_lower="L",identity_conflict=True),"UNKNOWN")
 def test_20_membership_cutoff(self):
  r=MembershipAssertion("a","L","2024-25",datetime(2024,8,1,tzinfo=UTC),"src","a"*40,"b"*40)
  self.assertEqual(strict_membership_at_cutoff([r],datetime(2024,9,1,tzinfo=UTC))[("a","2024-25")],"L")
 def test_21_late_membership_excluded(self):
  r=MembershipAssertion("a","L","2024-25",datetime(2024,9,2,tzinfo=UTC),"src","a"*40,"b"*40)
  self.assertEqual(strict_membership_at_cutoff([r],datetime(2024,9,1,tzinfo=UTC)),{})
 def test_22_membership_conflict(self):
  r1=MembershipAssertion("a","L","2024-25",datetime(2024,8,1,tzinfo=UTC),"src","a"*40,"b"*40)
  r2=MembershipAssertion("a","X","2024-25",datetime(2024,8,1,tzinfo=UTC),"src","c"*40,"d"*40)
  with self.assertRaises(IdentityLineageError): strict_membership_at_cutoff([r1,r2],datetime(2024,9,1,tzinfo=UTC))
 def test_23_digest(self): self.assertEqual(evidence_digest(["b","a"]),evidence_digest(["a","b"]))
 def test_24_formal_unchanged(self): self.assertTrue(all(x is False for x in C["formal_boundaries"].values()))
if __name__=="__main__": unittest.main(verbosity=2)
