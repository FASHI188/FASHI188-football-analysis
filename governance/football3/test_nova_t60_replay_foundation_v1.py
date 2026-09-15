#!/usr/bin/env python3
from __future__ import annotations

import tempfile, unittest
from pathlib import Path
import nova_t60_replay_foundation_v1 as m

HERE=Path(__file__).resolve().parent
CONFIG=HERE/"nova_t60_replay_foundation_v1.json"

class FoundationTests(unittest.TestCase):
    def setUp(self): self.c=m.load_config(CONFIG)
    def fixture(self):
        r={}; m.register_fixture(self.c,r,fixture_id="fx:1",competition="EPL",season="2026",kickoff="2026-10-01T19:00:00Z",observed_at="2026-09-20T10:00:00Z"); return r
    def observations(self):
        return [{"field":"home_ppda","value":9.0,"source":"s","observed_at":"2026-10-01T17:30:00Z","available_at":"2026-10-01T17:50:00Z"},{"field":"away_ppda","value":11.0,"source":"s","observed_at":"2026-10-01T17:40:00Z","available_at":"2026-10-01T17:55:00Z"}]
    def test_foundation_is_not_enabled(self):
        self.assertFalse(self.c["activation"]["enabled"]); self.assertIsNone(self.c["activation"]["replay_coverage_start_at"]); self.assertEqual(self.c["formal_boundaries"]["candidate_weight"],0)
    def test_kickoff_revision_changes_cutoff_append_only(self):
        r=self.fixture(); self.assertEqual(m.latest_fixture(self.c,r,"fx:1")["cutoff"],"2026-10-01T18:00:00Z"); x=m.revise_kickoff(self.c,r,fixture_id="fx:1",kickoff="2026-10-01T19:30:00Z",observed_at="2026-09-30T12:00:00Z"); self.assertEqual(x["revision_no"],2); self.assertEqual(m.latest_fixture(self.c,r,"fx:1")["cutoff"],"2026-10-01T18:30:00Z"); self.assertEqual(len(r["fx:1"]["kickoff_revisions"]),2)
    def test_available_at_after_cutoff_is_excluded(self):
        r=self.fixture(); fx=m.latest_fixture(self.c,r,"fx:1"); obs=self.observations()+[{"field":"home_ppda","value":4.0,"source":"s","observed_at":"2026-10-01T18:01:00Z","available_at":"2026-10-01T18:01:30Z"}]; kind,z=m.build_state_or_gap(self.c,fx,obs,required_fields=["home_ppda","away_ppda"],model_head="m",current_sha="c",capture_observed_at="2026-10-01T18:00:00Z"); self.assertEqual(kind,"state"); self.assertEqual(z["state"]["payload"]["home_ppda"],9.0)
    def test_result_field_fail_closed_even_after_cutoff(self):
        r=self.fixture(); fx=m.latest_fixture(self.c,r,"fx:1")
        with self.assertRaisesRegex(m.FoundationError,"RESULT_FIELD_FORBIDDEN"):
            m.build_state_or_gap(self.c,fx,[{"field":"result","value":"H","source":"s","observed_at":"2026-10-01T19:10:00Z","available_at":"2026-10-01T19:10:00Z"}],required_fields=[],model_head="m",current_sha="c",capture_observed_at="2026-10-01T18:00:00Z")
    def test_missing_required_field_emits_gap(self):
        r=self.fixture(); fx=m.latest_fixture(self.c,r,"fx:1"); kind,g=m.build_state_or_gap(self.c,fx,self.observations()[:1],required_fields=["home_ppda","away_ppda"],model_head="m",current_sha="c",capture_observed_at="2026-10-01T18:00:00Z"); self.assertEqual(kind,"gap"); self.assertEqual(g["missing_required_fields"],["away_ppda"]); self.assertFalse(g["retroactive_fabrication"])
    def test_sealed_state_is_deterministic(self):
        r=self.fixture(); fx=m.latest_fixture(self.c,r,"fx:1"); a=m.build_state_or_gap(self.c,fx,self.observations(),required_fields=["home_ppda","away_ppda"],model_head="m",current_sha="c",capture_observed_at="2026-10-01T18:00:00Z")[1]; b=m.build_state_or_gap(self.c,fx,list(reversed(self.observations())),required_fields=["away_ppda","home_ppda"],model_head="m",current_sha="c",capture_observed_at="2026-10-01T18:00:00Z")[1]; self.assertEqual(a["state"]["state_sha256"],b["state"]["state_sha256"]); self.assertEqual(a["state"]["input_sha256"],b["state"]["input_sha256"])
    def test_ledgers_suppress_reserved_and_completed_duplicates(self):
        r=self.fixture(); fx=m.latest_fixture(self.c,r,"fx:1"); z=m.build_state_or_gap(self.c,fx,self.observations(),required_fields=["home_ppda","away_ppda"],model_head="m",current_sha="c",capture_observed_at="2026-10-01T18:00:00Z")[1]; ledger={}; first=m.reserve_dispatch(ledger,z["state"],"2026-10-01T18:01:00Z"); dup1=m.reserve_dispatch(ledger,z["state"],"2026-10-01T18:01:01Z"); done=m.complete_dispatch(ledger,z["state"],output_sha256="a"*64,completed_at="2026-10-01T18:02:00Z"); dup2=m.reserve_dispatch(ledger,z["state"],"2026-10-01T18:02:01Z"); self.assertEqual(first["status"],"RESERVED"); self.assertEqual(dup1["existing_status"],"RESERVED"); self.assertEqual(done["status"],"COMPLETED"); self.assertEqual(dup2["existing_status"],"COMPLETED")
    def test_late_revision_emits_gap(self):
        r=self.fixture(); x=m.revise_kickoff(self.c,r,fixture_id="fx:1",kickoff="2026-10-01T19:30:00Z",observed_at="2026-10-01T18:45:00Z"); self.assertTrue(x["late_revision_risk"]); fx=m.latest_fixture(self.c,r,"fx:1"); kind,g=m.build_state_or_gap(self.c,fx,self.observations(),required_fields=["home_ppda","away_ppda"],model_head="m",current_sha="c",capture_observed_at="2026-10-01T18:45:00Z"); self.assertEqual(kind,"gap"); self.assertTrue(g["late_kickoff_revision_risk"])
    def test_simulation_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            p=m.simulate(CONFIG,Path(td)); self.assertEqual(p["status"],"T60_REPLAY_FOUNDATION_SIMULATION_PASS"); self.assertTrue(p["deterministic_state_replay"]); self.assertTrue(p["gap_receipt_generated"]); self.assertEqual(p["result_fields_read"],0); self.assertFalse(p["activation_enabled"])

if __name__=="__main__": unittest.main()
