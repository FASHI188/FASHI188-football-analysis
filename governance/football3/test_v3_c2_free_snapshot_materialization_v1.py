from __future__ import annotations
from datetime import datetime, timezone, timedelta
import copy, json, unittest
from pathlib import Path
import validate_v3_c2_free_snapshot_materialization_v1 as v
from v3_c2_free_snapshot_materializer_v1 import C2MaterializationError, GitSourceSnapshot, materialization_status, select_latest_eligible_snapshot, validate_identity_binding
HERE=Path(__file__).resolve().parent
C=json.loads((HERE/'v3_c2_free_snapshot_materialization_contract_v1.json').read_text())
M=json.loads((HERE/'v3_c2_free_source_snapshot_manifest_v1.json').read_text())
I=json.loads((HERE/'v3_c2_club_identity_bridge_contract_v1.json').read_text())
UTC=timezone.utc

def snap(t='2026-09-08T10:18:06+00:00',commit='a'*40,blob='b'*40):
    return GitSourceSnapshot('eng','openfootball/england',commit,datetime.fromisoformat(t),'2025-26/2-championship.txt',blob,'CC0_PUBLIC_DOMAIN')
class T(unittest.TestCase):
 def test_01_contract(self): self.assertGreaterEqual(len(v.validate(C,M,I)),20)
 def test_02_valid_snapshot(self): snap().validate()
 def test_03_eligible_equal(self): self.assertTrue(snap().eligible_for_cutoff(datetime.fromisoformat('2026-09-08T10:18:06+00:00')))
 def test_04_ineligible_before(self): self.assertFalse(snap().eligible_for_cutoff(datetime.fromisoformat('2026-09-08T10:18:05+00:00')))
 def test_05_receipt_has_archive_basis(self): self.assertEqual(snap().receipt(datetime.fromisoformat('2026-09-09T00:00:00+00:00'))['availability_basis'],'ARCHIVE_RELEASE_AT')
 def test_06_receipt_deterministic(self):
  c=datetime.fromisoformat('2026-09-09T00:00:00+00:00'); self.assertEqual(snap().receipt(c)['receipt_sha256'],snap().receipt(c)['receipt_sha256'])
 def test_07_late_receipt_rejected(self):
  with self.assertRaises(C2MaterializationError): snap().receipt(datetime.fromisoformat('2026-09-01T00:00:00+00:00'))
 def test_08_naive_time_rejected(self):
  with self.assertRaises(C2MaterializationError): GitSourceSnapshot('x','r','a'*40,datetime(2026,1,1),'p','b'*40,'CC0').validate()
 def test_09_bad_commit_rejected(self):
  with self.assertRaises(C2MaterializationError): snap(commit='x').validate()
 def test_10_bad_blob_rejected(self):
  with self.assertRaises(C2MaterializationError): snap(blob='x').validate()
 def test_11_selector_latest_eligible(self):
  xs=[snap('2026-09-01T00:00:00+00:00','1'*40),snap('2026-09-07T00:00:00+00:00','2'*40),snap('2026-09-09T00:00:00+00:00','3'*40)]
  self.assertEqual(select_latest_eligible_snapshot(xs,datetime.fromisoformat('2026-09-08T00:00:00+00:00')).commit,'2'*40)
 def test_12_selector_no_eligible(self):
  with self.assertRaises(C2MaterializationError): select_latest_eligible_snapshot([snap()],datetime.fromisoformat('2026-01-01T00:00:00+00:00'))
 def test_13_identity_exact(self):
  b={'source':'tm','source_club_key':'31','canonical_club_id':'tm:31','competition_season':'2025-26','binding_basis':'PROVIDER_STABLE_ID','binding_sha256':'c'*64,'evidence_source':'tm','evidence_snapshot_sha':'d'*40}; validate_identity_binding(b)
 def test_14_identity_fuzzy_rejected(self):
  b={'source':'x','source_club_key':'Man Utd','canonical_club_id':'x','competition_season':'2025-26','binding_basis':'FUZZY_NAME_MATCH','binding_sha256':'c'*64,'evidence_source':'x','evidence_snapshot_sha':'d'*40}
  with self.assertRaises(C2MaterializationError): validate_identity_binding(b)
 def test_15_partial_without_lineage(self): self.assertEqual(materialization_status(historical_cutoff_snapshots=10,exact_identity_bindings=10,promotion_lineage_closed=False,cross_league_scale_frozen=True),'PARTIAL_DATA_FOUNDATION_READY_STOP_DATA_COVERAGE')
 def test_16_partial_without_scale(self): self.assertEqual(materialization_status(historical_cutoff_snapshots=10,exact_identity_bindings=10,promotion_lineage_closed=True,cross_league_scale_frozen=False),'PARTIAL_DATA_FOUNDATION_READY_STOP_DATA_COVERAGE')
 def test_17_ready_requires_all(self): self.assertEqual(materialization_status(historical_cutoff_snapshots=10,exact_identity_bindings=10,promotion_lineage_closed=True,cross_league_scale_frozen=True),'ZERO_LABEL_DATA_READY_PREREG_REQUIRED')
 def test_18_zero_snapshot_stop(self): self.assertEqual(materialization_status(historical_cutoff_snapshots=0,exact_identity_bindings=10,promotion_lineage_closed=True,cross_league_scale_frozen=True),'STOP_DATA_COVERAGE')
 def test_19_five_latest_pins(self): self.assertEqual(len(M['openfootball_big5_second_tier_latest_pins']),5)
 def test_20_latest_pin_guard(self): self.assertEqual(M['historical_selector_contract']['latest_pin_as_earlier_history'],'FORBIDDEN')
 def test_21_tm_identity_only(self): self.assertEqual(M['transfermarkt_identity_snapshot']['role'],'IDENTITY_AND_LINEAGE_SEED_ONLY')
 def test_22_wikidata_identity_only(self): self.assertEqual(I['wikidata_identity_only']['sports_feature_role'],'NONE')
 def test_23_c2_still_inactive(self): self.assertFalse(C['candidate']['data_ready'])
 def test_24_all_forbidden(self): self.assertTrue(all(C['forbidden_changes'].values()))
 def test_25_tamper_status_fails(self):
  x=copy.deepcopy(C); x['candidate']['status']='ACTIVE'
  with self.assertRaises(v.E): v.validate(x,M,I)
 def test_26_tamper_fuzzy_fails(self):
  x=copy.deepcopy(I); x['canonical_identity_strategy']['fuzzy_name_match']='ALLOW'
  with self.assertRaises(v.E): v.validate(C,M,x)
if __name__=='__main__': unittest.main(verbosity=2)
