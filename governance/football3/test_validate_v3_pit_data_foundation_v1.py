from __future__ import annotations
from datetime import datetime, timezone, timedelta
import copy, json, unittest
from pathlib import Path
import validate_v3_pit_data_foundation_v1 as v
from v3_pit_data_provider_interface_v1 import AvailabilityBasis, ClubIdentityBinding, FixtureIdentity, PITObservationEnvelope, PITContractError, classify_static_archive_for_target, content_sha256, reject_event_time_as_availability
HERE=Path(__file__).resolve().parent
C=json.loads((HERE/'v3_pit_data_foundation_contract_v1.json').read_text(encoding='utf-8'))
I=json.loads((HERE/'v3_pit_data_source_inventory_v1.json').read_text(encoding='utf-8'))
UTC=timezone.utc

def fixture(): return FixtureIdentity('fx-1','ENG_PremierLeague','p-9',datetime(2026,9,20,15,tzinfo=UTC),'rev-2')
def env(**kw):
    base=dict(source='public',schema='s1',source_identity='src1',evidence_kind='lineage',fixture=fixture(),event_time=datetime(2026,7,1,tzinfo=UTC),observed_at=datetime(2026,7,6,tzinfo=UTC),available_at=datetime(2026,7,6,tzinfo=UTC),retrieved_at=datetime(2026,9,12,tzinfo=UTC),freeze_at=datetime(2026,9,20,14,45,tzinfo=UTC),availability_basis=AvailabilityBasis.ARCHIVE_RELEASE_AT,content_sha256='a'*64,revision_lineage='r1',license_state='CC0',payload_ref='blob://x')
    base.update(kw); return PITObservationEnvelope(**base)
class T(unittest.TestCase):
 def test_01_contract_checks(self): self.assertGreaterEqual(len(v.validate(C,I)),35)
 def test_02_fixture_key_deterministic(self): self.assertEqual(fixture().deterministic_key(),fixture().deterministic_key())
 def test_03_fixture_revision_changes_key(self): self.assertNotEqual(fixture().deterministic_key(),FixtureIdentity('fx-1','ENG_PremierLeague','p-9',datetime(2026,9,20,15,tzinfo=UTC),'rev-3').deterministic_key())
 def test_04_naive_kickoff_rejected(self):
  with self.assertRaises(PITContractError): FixtureIdentity('x','c','p',datetime(2026,1,1),'r').validate()
 def test_05_envelope_valid(self): env().validate()
 def test_06_pit_eligible(self): self.assertTrue(env().pit_eligible_at_freeze())
 def test_07_late_available_ineligible(self): self.assertFalse(env(available_at=datetime(2026,9,20,14,46,tzinfo=UTC)).pit_eligible_at_freeze())
 def test_08_retrieval_proxy_rejected(self):
  with self.assertRaises(PITContractError): env(availability_basis=AvailabilityBasis.RETRIEVAL_PROXY).validate()
 def test_09_unknown_availability_rejected(self):
  with self.assertRaises(PITContractError): env(availability_basis=AvailabilityBasis.UNKNOWN).validate()
 def test_10_observed_after_available_rejected(self):
  with self.assertRaises(PITContractError): env(observed_at=datetime(2026,7,7,tzinfo=UTC)).validate()
 def test_11_bad_sha_rejected(self):
  with self.assertRaises(PITContractError): env(content_sha256='x').validate()
 def test_12_archive_historical_safe(self): self.assertTrue(env().historical_backfill_safe())
 def test_13_collector_not_historical_backfill(self): self.assertFalse(env(availability_basis=AvailabilityBasis.COLLECTOR_FIRST_OBSERVED_AT).historical_backfill_safe())
 def test_14_collector_prospective_allowed(self): self.assertTrue(env(availability_basis=AvailabilityBasis.COLLECTOR_FIRST_OBSERVED_AT).prospective_capture_safe())
 def test_15_static_archive_future_target(self): self.assertEqual(classify_static_archive_for_target(archive_release_at=datetime(2026,7,6,tzinfo=UTC),target_freeze_at=datetime(2026,9,1,tzinfo=UTC)),'PIT_ELIGIBLE_FOR_FUTURE_TARGET')
 def test_16_static_archive_past_target_rejected(self): self.assertEqual(classify_static_archive_for_target(archive_release_at=datetime(2026,7,6,tzinfo=UTC),target_freeze_at=datetime(2026,6,1,tzinfo=UTC)),'NOT_PIT_ELIGIBLE')
 def test_17_event_time_cannot_fill_available(self):
  with self.assertRaises(PITContractError): reject_event_time_as_availability(datetime(2026,1,1,tzinfo=UTC),None)
 def test_18_content_hash(self): self.assertEqual(content_sha256(b'abc'),'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
 def test_19_receipt_sha_deterministic(self): self.assertEqual(env().receipt_sha256(),env().receipt_sha256())
 def test_20_tm_17_domains(self): self.assertEqual(I['public_sources']['transfermarkt_datasets']['formal_domain_mapping_count'],17)
 def test_21_tm_stale_guard(self): self.assertIn('2026/27 current squads or lineups',I['public_sources']['transfermarkt_datasets']['not_safe_to_claim'])
 def test_22_statsbomb_limited_guard(self): self.assertEqual(I['public_sources']['statsbomb_hudl_open_data']['coverage_state'],'SELECTED_COMPETITIONS_AND_SEASONS_ONLY')
 def test_23_c7_dependency_only(self): self.assertEqual(I['candidate_root_causes']['C7']['cost_or_credential_boundary'],'NO_DIRECT_PURCHASE_RECOMMENDED')
 def test_24_public_first(self): self.assertEqual([x['workstream'] for x in I['execution_priority'][:4]],['COMMON_PIT_ENVELOPE_AND_IDENTITY_CONTRACT','TRANSFERMARKT_CC0_IDENTITY_AND_LINEAGE_SEED','OPENFOOTBALL_CC0_BIG5_LOWER_DIVISION_PRIOR','PUBLIC_OPEN_EVENT_AND_LINEUP_COVERAGE_PROBES'])
 def test_25_tamper_activation_fails(self):
  b=copy.deepcopy(C); b['candidate_effect']['c1_to_c7_activation_changed']=True
  with self.assertRaises(v.ContractError): v.validate(b,I)
 def test_26_tamper_tm_scope_fails(self):
  b=copy.deepcopy(I); b['public_sources']['transfermarkt_datasets']['formal_domain_mapping_count']=16
  with self.assertRaises(v.ContractError): v.validate(C,b)
 def test_27_forbidden_all_true(self): self.assertTrue(all(C['forbidden_changes'].values()))
 def test_28_external_stop_is_after_public_phase(self): self.assertEqual(C['external_stop_boundaries']['first_unavoidable_boundary_after_public_phase'],'CURRENT_PERSONNEL_AND_T15_LINEUP_SOURCE')
 def test_29_openfootball_five_pins(self): self.assertEqual(len(I['public_sources']['openfootball_big5_lower_division']['pins']),5)
 def test_30_exact_crosswalk_binding(self):
  b=ClubIdentityBinding('openfootball','Burnley','club:burnley','2025-26','PREDECLARED_EXACT_CROSSWALK','b'*64); b.validate(); self.assertEqual(b.deterministic_key(),b.deterministic_key())
 def test_31_fuzzy_identity_rejected(self):
  with self.assertRaises(PITContractError): ClubIdentityBinding('openfootball','Man Utd','club:manchester-united','2025-26','FUZZY_NAME_MATCH','b'*64).validate()
if __name__=='__main__': unittest.main(verbosity=2)
