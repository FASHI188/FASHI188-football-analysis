from __future__ import annotations
from datetime import timezone
import gzip, io, json, unittest
from pathlib import Path
import c2_free_history_identity_v1 as m
import validate_c2_free_history_identity_v1 as v
HERE=Path(__file__).resolve().parent
C=json.loads((HERE/'c2_free_history_identity_contract_v1.json').read_text(encoding='utf-8'))
S=json.loads((HERE/'c2_openfootball_snapshot_manifest_v1.json').read_text(encoding='utf-8'))
L=json.loads((HERE/'c2_openfootball_identity_lineage_v1.json').read_text(encoding='utf-8'))
REG='''= Clubs\nReal Racing Santander, 1913\n  | Santander | Racing Santander | Real Racing Club de Santander\nRCD La Coruña\n  | Deportivo La Coruña | RC Deportivo La Coruña | Deportivo de La Coruña\nMálaga CF\nES Troyes AC\n  | ESTAC Troyes | Troyes\nLe Mans FC\nCoventry City FC\n  | Coventry City\n'''
FIX='''= Test\n# Teams 3\n  Sat Aug 1\n    15:00  Racing Santander        v Málaga CF                3-1 (1-0)\n           Deportivo La Coruña    v Coventry City FC\n'''
class T(unittest.TestCase):
 def test_01_contract(self): self.assertGreaterEqual(len(v.validate(C,S,L)),30)
 def test_02_git_blob_known(self): self.assertEqual(m.git_blob_sha(b'test content\n'),'d670460b4b4aece5915caf5c68d12f560a9fe3e4')
 def test_03_sha256(self): self.assertEqual(m.sha256(b'abc'),'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
 def test_04_verify_blob_pass(self): self.assertEqual(m.verify_snapshot_bytes(b'test content\n','d670460b4b4aece5915caf5c68d12f560a9fe3e4')['bytes'],13)
 def test_05_verify_blob_fail(self):
  with self.assertRaises(m.C2DataError): m.verify_snapshot_bytes(b'x','0'*40)
 def test_06_aware_parse(self): self.assertEqual(m.parse_iso_aware('2026-09-08T10:18:06Z').tzinfo,timezone.utc)
 def test_07_naive_rejected(self):
  with self.assertRaises(m.C2DataError): m.parse_iso_aware('2026-09-08T10:18:06')
 def test_08_eligible_equal(self): self.assertTrue(m.snapshot_eligible(published_at='2026-09-08T10:18:06Z',cutoff='2026-09-08T10:18:06Z'))
 def test_09_eligible_after(self): self.assertTrue(m.snapshot_eligible(published_at='2026-09-08T10:18:06Z',cutoff='2026-09-09T00:00:00Z'))
 def test_10_not_eligible_before(self): self.assertFalse(m.snapshot_eligible(published_at='2026-09-08T10:18:06Z',cutoff='2026-09-08T10:18:05Z'))
 def test_11_fixture_names_ignore_score(self): self.assertEqual(m.parse_fixture_team_names(FIX),{'Racing Santander','Málaga CF','Deportivo La Coruña','Coventry City FC'})
 def test_12_registry_count(self): self.assertEqual(len(m.parse_openfootball_club_registry(REG)),6)
 def test_13_alias_index(self): self.assertEqual(m.build_exact_alias_index(m.parse_openfootball_club_registry(REG))['Racing Santander'],'Real Racing Santander')
 def test_14_resolve_current_alias(self): self.assertEqual(m.resolve_exact('Real Racing Club de Santander',m.build_exact_alias_index(m.parse_openfootball_club_registry(REG))),'Real Racing Santander')
 def test_15_resolve_prior_alias(self): self.assertEqual(m.resolve_exact('Deportivo La Coruña',m.build_exact_alias_index(m.parse_openfootball_club_registry(REG))),'RCD La Coruña')
 def test_16_unknown_rejected(self):
  with self.assertRaises(m.C2DataError): m.resolve_exact('Racing Santanderr',m.build_exact_alias_index(m.parse_openfootball_club_registry(REG)))
 def test_17_case_fold_forbidden(self):
  with self.assertRaises(m.C2DataError): m.resolve_exact('racing santander',m.build_exact_alias_index(m.parse_openfootball_club_registry(REG)))
 def test_18_derive_promotions_ignores_unrelated_unresolved(self):
  idx=m.build_exact_alias_index(m.parse_openfootball_club_registry(REG)); self.assertEqual(m.derive_promotions({'Racing Santander','Málaga CF','Unknown Lower Club'},{'Real Racing Club de Santander','Málaga CF','Unknown Top Club'},idx),['Málaga CF','Real Racing Santander'])
 def test_19_ambiguous_alias_removed(self):
  r=m.parse_openfootball_club_registry('A FC\n | X\nB FC\n | X\n'); self.assertNotIn('X',m.build_exact_alias_index(r))
 def _gz(self):
  b=io.BytesIO()
  with gzip.GzipFile(fileobj=b,mode='wb') as g: g.write(b'club_id,name\n1,Racing Santander\n2,Deportivo La Coru\xc3\xb1a\n3,M\xc3\xa1laga CF\n4,ESTAC Troyes\n5,Le Mans FC\n6,Coventry City\n')
  return b.getvalue()
 def test_20_tm_read(self): self.assertEqual(len(m.read_transfermarkt_clubs_gz(self._gz())),6)
 def test_21_tm_schema_guard(self):
  b=io.BytesIO()
  with gzip.GzipFile(fileobj=b,mode='wb') as g: g.write(b'id,name\n1,X\n')
  with self.assertRaises(m.C2DataError): m.read_transfermarkt_clubs_gz(b.getvalue())
 def test_22_tm_binding_exact_alias(self):
  rec=m.parse_openfootball_club_registry(REG); out=m.bind_canonical_to_transfermarkt(rec,m.read_transfermarkt_clubs_gz(self._gz()),['Real Racing Santander','RCD La Coruña','Málaga CF','ES Troyes AC','Le Mans FC','Coventry City FC']); self.assertEqual(out['status'],'PASS'); self.assertEqual(out['binding_count'],6)
 def test_23_tm_unresolved_fail_closed(self):
  out=m.bind_canonical_to_transfermarkt(m.parse_openfootball_club_registry(REG),[],['Málaga CF']); self.assertEqual(out['status'],'STOP_DATA_COVERAGE'); self.assertEqual(out['binding_count'],0)
 def test_24_tm_conflict_fail_closed(self):
  rows=[{'club_id':'1','name':'Málaga CF'},{'club_id':'2','name':'Málaga CF'}]; out=m.bind_canonical_to_transfermarkt(m.parse_openfootball_club_registry(REG),rows,['Málaga CF']); self.assertEqual(out['status'],'STOP_DATA_COVERAGE'); self.assertEqual(len(out['conflicts']),1)
 def test_25_deterministic_sha(self): self.assertEqual(m.deterministic_json_sha256({'b':1,'a':2}),m.deterministic_json_sha256({'a':2,'b':1}))
 def test_26_snapshot_10(self): self.assertEqual(len(S['season_snapshots']),10)
 def test_27_registry_5(self): self.assertEqual(len(S['club_registries']),5)
 def test_28_lineage_14(self): self.assertEqual(len(L['promotion_lineage']),14)
 def test_29_country_counts(self): self.assertEqual(L['counts'],{'ENG':3,'GER':3,'ESP':3,'ITA':3,'FRA':2,'total':14})
 def test_30_three_declared_aliases(self): self.assertEqual(sum(x['basis']=='SOURCE_DECLARED_EXACT_ALIAS' for x in L['promotion_lineage']),3)
 def test_31_no_fuzzy(self): self.assertFalse(L['identity_policy']['fuzzy_matching'])
 def test_32_candidate_not_available(self): self.assertEqual(C['candidate']['status'],'NOT_AVAILABLE')
 def test_33_candidate_weight_zero(self): self.assertEqual(C['candidate']['weight'],0)
 def test_34_candidate_matrix_zero(self): self.assertEqual(C['candidate']['matrix_delta'],0)
 def test_35_no_training(self): self.assertFalse(C['candidate']['training_allowed'])
 def test_36_no_labels(self): self.assertFalse(C['candidate']['new_target_label_access_allowed'])
 def test_37_residual_blockers(self): self.assertGreaterEqual(len(C['residual_blockers_before_data_ready']),5)
 def test_38_shared_reuse_required(self): self.assertTrue(all(x['reuse_required'] for x in C['shared_dependencies'].values()))
 def test_39_scores_not_used(self): self.assertFalse(C['zero_label_boundary']['fixture_score_tokens_may_be_parsed_or_used'])
 def test_40_forbidden_all_true(self): self.assertTrue(all(C['forbidden_changes'].values()))
if __name__=='__main__': unittest.main(verbosity=2)
