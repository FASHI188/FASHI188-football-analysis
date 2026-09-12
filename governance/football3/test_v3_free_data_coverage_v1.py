from __future__ import annotations
from datetime import datetime, timezone
import copy, json, unittest
from pathlib import Path
import validate_v3_free_data_coverage_v1 as v
from v3_free_data_coverage_probe_v1 import candidate_receipt, full_probe
from v3_git_snapshot_adapter_v1 import GitSnapshotEvidence, SnapshotContractError, select_latest_snapshot_at_or_before_cutoff, reject_latest_archive_for_earlier_cutoff

HERE=Path(__file__).resolve().parent
C=json.loads((HERE/"v3_free_data_coverage_contract_v1.json").read_text(encoding="utf-8"))
R=json.loads((HERE/"v3_free_source_registry_v1.json").read_text(encoding="utf-8"))
UTC=timezone.utc
def snap(pub:str,commit="a"*40,blob="b"*40):
    published=datetime.fromisoformat(pub.replace("Z","+00:00"))
    observed=max(published,datetime(2026,9,12,tzinfo=UTC))
    return GitSnapshotEvidence("openfootball/test",commit,"x.txt",blob,published,observed,observed,"CC0","test")

class T(unittest.TestCase):
 def test_01_validator(self): self.assertGreaterEqual(len(v.validate(C,R)),25)
 def test_02_snapshot_valid(self): snap("2026-09-01T00:00:00Z").validate()
 def test_03_snapshot_receipt_basis(self): self.assertEqual(snap("2026-09-01T00:00:00Z").receipt_payload()["availability_basis"],"ARCHIVE_RELEASE_AT")
 def test_04_snapshot_deterministic(self): self.assertEqual(snap("2026-09-01T00:00:00Z").receipt_sha256(),snap("2026-09-01T00:00:00Z").receipt_sha256())
 def test_05_future_release_not_pit(self): self.assertFalse(snap("2026-09-10T00:00:00Z").pit_eligible(datetime(2026,9,1,tzinfo=UTC)))
 def test_06_past_release_pit(self): self.assertTrue(snap("2026-08-01T00:00:00Z").pit_eligible(datetime(2026,9,1,tzinfo=UTC)))
 def test_07_latest_select(self):
    rows=[snap("2026-08-01T00:00:00Z","1"*40,"2"*40),snap("2026-08-20T00:00:00Z","3"*40,"4"*40),snap("2026-09-20T00:00:00Z","5"*40,"6"*40)]
    self.assertEqual(select_latest_snapshot_at_or_before_cutoff(rows,datetime(2026,9,1,tzinfo=UTC)).commit_sha,"3"*40)
 def test_08_no_snapshot_fails(self):
    with self.assertRaises(SnapshotContractError): select_latest_snapshot_at_or_before_cutoff([snap("2026-09-20T00:00:00Z")],datetime(2026,9,1,tzinfo=UTC))
 def test_09_reject_backfill(self):
    with self.assertRaises(SnapshotContractError): reject_latest_archive_for_earlier_cutoff(snap("2026-09-20T00:00:00Z"),datetime(2026,9,1,tzinfo=UTC))
 def test_10_bad_commit_rejected(self):
    with self.assertRaises(SnapshotContractError): snap("2026-09-01T00:00:00Z","bad","b"*40).validate()
 def test_11_bad_blob_rejected(self):
    with self.assertRaises(SnapshotContractError): snap("2026-09-01T00:00:00Z","a"*40,"bad").validate()
 def test_12_observed_before_publication_rejected(self):
    s=GitSnapshotEvidence("r","a"*40,"x","b"*40,datetime(2026,9,10,tzinfo=UTC),datetime(2026,9,9,tzinfo=UTC),datetime(2026,9,12,tzinfo=UTC),"CC0","x")
    with self.assertRaises(SnapshotContractError): s.validate()
 def test_13_openfootball_partition(self):
    o=R["sources"]["openfootball"]; self.assertEqual(o["current_direct_path_count"]+o["current_gap_count"],17)
 def test_14_openfootball_current_eight(self): self.assertEqual(set(R["sources"]["openfootball"]["current_formal_domain_paths"]),{"ENG_PremierLeague","GER_Bundesliga","ESP_LaLiga","ITA_SerieA","FRA_Ligue1","POR_PrimeiraLiga","NED_Eredivisie","BRA_SerieA"})
 def test_15_korea_missing(self): self.assertEqual(R["sources"]["openfootball"]["historical_or_missing_formal_domains"]["KOR_KLeague1"]["reason"],"NO_KOREA_PATH_MECHANICALLY_FOUND_AT_PIN")
 def test_16_ucl_missing_current(self): self.assertIn("NO_2026_27",R["sources"]["openfootball"]["historical_or_missing_formal_domains"]["UEFA_ChampionsLeague"]["reason"])
 def test_17_tm_stale_current(self): self.assertTrue(R["sources"]["transfermarkt_public_dataset"]["updates_paused"])
 def test_18_wikidata_identity_only(self): self.assertEqual(R["sources"]["wikidata_identity"]["use"],"IDENTITY_ONLY_NOT_PERFORMANCE_FEATURE")
 def test_19_statsbomb_limited(self): self.assertEqual(R["sources"]["statsbomb_hudl_open_data"]["coverage"],"SELECTED_COMPETITIONS_AND_SEASONS_ONLY")
 def test_20_fd_not_selected(self): self.assertFalse(R["sources"]["football_data_uk"]["selected"])
 def test_21_c1_exhausted(self): self.assertEqual(R["candidate_coverage_matrix"]["C1"]["status"],"FREE_SOURCE_EXHAUSTED_FOR_REQUIRED_SURFACE")
 def test_22_c2_partial(self): self.assertTrue(R["candidate_coverage_matrix"]["C2"]["timestamp_or_pit_insufficient"])
 def test_23_c3_t15_gap(self): self.assertIn("T15",R["candidate_coverage_matrix"]["C3"]["first_authoritative_gap"])
 def test_24_c4_personnel_gap(self): self.assertIn("PERSONNEL",R["candidate_coverage_matrix"]["C4"]["first_authoritative_gap"])
 def test_25_c5_available_gap(self): self.assertTrue(R["candidate_coverage_matrix"]["C5"]["timestamp_or_pit_insufficient"])
 def test_26_c6_coverage_gap(self): self.assertTrue(R["candidate_coverage_matrix"]["C6"]["league_or_season_coverage_insufficient"])
 def test_27_c7_upstream(self): self.assertEqual(R["candidate_coverage_matrix"]["C7"]["first_authoritative_gap"],"UPSTREAM_ACTIVE_EXPERT_COUNT_0")
 def test_28_none_data_ready(self): self.assertEqual(full_probe(R)["data_ready_candidates"],[])
 def test_29_no_training(self): self.assertFalse(full_probe(R)["training_performed"])
 def test_30_no_labels(self): self.assertFalse(full_probe(R)["new_target_labels_read"])
 def test_31_candidate_receipt_categories(self): self.assertGreater(candidate_receipt(R,"C2")["unresolved_category_count"],0)
 def test_32_all_inactive_contract(self): self.assertTrue(all(x["status"]=="NOT_AVAILABLE" and x["weight"]==0 and x["matrix_delta"]==0 for x in C["candidate_state"].values()))
 def test_33_second_chain_forbidden(self): self.assertFalse(C["shared_design"]["second_loader_chain_allowed"])
 def test_34_runtime_network_forbidden(self): self.assertFalse(C["shared_design"]["runtime_network_calls_allowed"])
 def test_35_exact_allowed_files(self): self.assertEqual(len(C["allowed_files"]),7)
 def test_36_tamper_activation_fails(self):
    b=copy.deepcopy(C); b["candidate_state"]["C2"]["status"]="DATA_READY"
    with self.assertRaises(v.ContractError): v.validate(b,R)
 def test_37_tamper_of_count_fails(self):
    b=copy.deepcopy(R); b["sources"]["openfootball"]["current_direct_path_count"]=9
    with self.assertRaises(v.ContractError): v.validate(C,b)
 def test_38_tamper_fd_selected_fails(self):
    b=copy.deepcopy(R); b["sources"]["football_data_uk"]["selected"]=True
    with self.assertRaises(v.ContractError): v.validate(C,b)
 def test_39_c1_contract_not_relaxed(self): self.assertTrue(R["candidate_coverage_matrix"]["C1"]["data_source_missing"])
 def test_40_probe_selected_empty(self): self.assertEqual(full_probe(R)["selected_experts"],[])
if __name__=="__main__": unittest.main(verbosity=2)
