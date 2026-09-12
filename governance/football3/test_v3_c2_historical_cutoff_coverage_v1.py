from __future__ import annotations
from datetime import datetime, timezone
import copy, json, unittest
from pathlib import Path
import validate_v3_c2_historical_cutoff_coverage_v1 as v
from v3_c2_historical_cutoff_auditor_v1 import CommitSnapshot, HistoricalCutoffAuditError, audit_preseason_sources, classify_membership_movement, select_snapshot
HERE=Path(__file__).resolve().parent
C=json.loads((HERE/'v3_c2_historical_cutoff_coverage_contract_v1.json').read_text())
M=json.loads((HERE/'v3_c2_historical_commit_history_manifest_v1.json').read_text())
L=json.loads((HERE/'v3_c2_promotion_lineage_contract_v1.json').read_text())
UTC=timezone.utc

def s(t='2025-07-30T16:09:41+00:00', size=20000, commit='a'*40):
    return CommitSnapshot('x','openfootball/x','2025-26/2.txt',commit,datetime.fromisoformat(t),'b'*40,'c'*40,size,'CC0_PUBLIC_DOMAIN')
class T(unittest.TestCase):
 def test_01_contract(self): self.assertGreaterEqual(len(v.validate(C,M,L)),20)
 def test_02_valid(self): s().validate()
 def test_03_eligible(self): self.assertTrue(s().eligible(datetime.fromisoformat('2025-08-01T00:00:00+00:00')))
 def test_04_not_eligible(self): self.assertFalse(s().eligible(datetime.fromisoformat('2025-07-01T00:00:00+00:00')))
 def test_05_material(self): self.assertTrue(s(size=1024).nontrivial_blob_guard())
 def test_06_tiny(self): self.assertFalse(s(size=65).nontrivial_blob_guard())
 def test_07_bad_sha(self):
  with self.assertRaises(HistoricalCutoffAuditError): s(commit='x').validate()
 def test_08_naive(self):
  with self.assertRaises(HistoricalCutoffAuditError): CommitSnapshot('x','r','p','a'*40,datetime(2025,1,1),'b'*40,'c'*40,1,'CC0').validate()
 def test_09_selector_latest(self):
  xs=[s('2025-07-01T00:00:00+00:00',commit='1'*40),s('2025-07-30T00:00:00+00:00',commit='2'*40),s('2025-08-03T00:00:00+00:00',commit='3'*40)]
  self.assertEqual(select_snapshot(xs,datetime.fromisoformat('2025-08-01T00:00:00+00:00')).commit,'2'*40)
 def test_10_selector_skips_tiny(self):
  xs=[s('2025-07-01T00:00:00+00:00',2000,'1'*40),s('2025-07-30T00:00:00+00:00',65,'2'*40)]
  self.assertEqual(select_snapshot(xs,datetime.fromisoformat('2025-08-01T00:00:00+00:00')).commit,'1'*40)
 def test_11_selector_none(self):
  with self.assertRaises(HistoricalCutoffAuditError): select_snapshot([s()],datetime.fromisoformat('2025-01-01T00:00:00+00:00'))
 def test_12_preseason_counts(self):
  r=audit_preseason_sources([s(size=2000),s(size=65,commit='2'*40)],datetime.fromisoformat('2025-08-01T00:00:00+00:00')); self.assertEqual((r['preseason_snapshot_count'],r['materiality_pass_count'],r['materiality_fail_count']),(2,1,1))
 def test_13_promoted(self): self.assertEqual(classify_membership_movement(canonical_club_id='c',prior_competition='L2',target_competition='L1',target_top_division='L1',approved_lower_division='L2'),'PROMOTED')
 def test_14_returning(self): self.assertEqual(classify_membership_movement(canonical_club_id='c',prior_competition='L2',target_competition='L1',target_top_division='L1',approved_lower_division='L2',had_earlier_top_division=True),'RETURNING')
 def test_15_relegated(self): self.assertEqual(classify_membership_movement(canonical_club_id='c',prior_competition='L1',target_competition='L2',target_top_division='L1',approved_lower_division='L2'),'RELEGATED')
 def test_16_staying(self): self.assertEqual(classify_membership_movement(canonical_club_id='c',prior_competition='L1',target_competition='L1',target_top_division='L1',approved_lower_division='L2'),'STAYING')
 def test_17_conflict_unknown(self): self.assertEqual(classify_membership_movement(canonical_club_id='c',prior_competition='L2',target_competition='L1',target_top_division='L1',approved_lower_division='L2',identity_conflict=True),'UNKNOWN')
 def test_18_missing_unknown(self): self.assertEqual(classify_membership_movement(canonical_club_id='',prior_competition='L2',target_competition='L1',target_top_division='L1',approved_lower_division='L2'),'UNKNOWN')
 def test_19_manifest_five(self): self.assertEqual(len(M['sources']),5)
 def test_20_manifest_materiality(self): self.assertEqual(M['mechanical_summary']['materiality_pass_count'],4)
 def test_21_italy_tiny(self): self.assertEqual(M['sources']['ITA_SerieB']['blob_bytes'],65)
 def test_22_content_not_read(self): self.assertFalse(M['content_read'])
 def test_23_lineage_no_results(self): self.assertTrue(all(L['forbidden_inference'].values()))
 def test_24_c2_inactive(self): self.assertFalse(C['candidate']['data_ready'])
 def test_25_forbidden(self): self.assertTrue(all(C['forbidden_changes'].values()))
 def test_26_tamper_dataready(self):
  x=copy.deepcopy(C); x['candidate']['data_ready']=True
  with self.assertRaises(v.E): v.validate(x,M,L)
 def test_27_tamper_labels(self):
  x=copy.deepcopy(M); x['target_labels_read']=True
  with self.assertRaises(v.E): v.validate(C,x,L)
 def test_28_tamper_lineage(self):
  x=copy.deepcopy(L); x['forbidden_inference']['match_scores']=False
  with self.assertRaises(v.E): v.validate(C,M,x)
if __name__=='__main__': unittest.main(verbosity=2)
