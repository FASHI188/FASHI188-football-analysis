#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import current_v2_retrospective_exact_history_v1 as exact
import current_v2_retrospective_receipt_contract_v1 as receipt_contract
import current_v2_retrospective_replay_v1 as replay

FOOTBALL3_GOVERNED_PRODUCTION_TRANSPORT = "football3-formal-gpt-request-transport-v1"


class ExactHistoryTrustBoundaryTests(unittest.TestCase):
    def test_history_upper_is_exact_target_kickoff(self):
        target = datetime(2026, 9, 7, 18, 45, tzinfo=timezone.utc)
        self.assertEqual(exact.exact_history_upper(target), target)

    def test_main_source_rejects_target_before_score_access(self):
        lower = datetime(2026, 9, 1, tzinfo=timezone.utc)
        upper = datetime(2026, 9, 7, 18, 45, tzinfo=timezone.utc)
        rows = [
            {"Date": "06/09/2026", "Time": "19:45", "HomeTeam": "Prior A", "AwayTeam": "Prior B", "FTHG": "1", "FTAG": "0"},
            {"Date": "07/09/2026", "Time": "19:45", "HomeTeam": "Udinese", "AwayTeam": "Lazio", "FTHG": "DO_NOT_READ", "FTAG": "DO_NOT_READ"},
        ]
        score_accesses: list[str] = []

        def guarded_goals(row):
            score_accesses.append(str(row.get("FTHG")))
            if row.get("FTHG") == "DO_NOT_READ":
                raise AssertionError("target score field was read")
            return int(row["FTHG"]), int(row["FTAG"])

        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(exact.live, "MAIN_EUROPE", {"ITA_SerieA": "I1"}), \
             mock.patch.object(exact.live, "_cross_year_starts", return_value=[2026]), \
             mock.patch.object(exact.live, "_fetch", return_value=(b"payload", "a" * 64)), \
             mock.patch.object(exact.live, "_decode_csv", return_value=rows), \
             mock.patch.object(exact.live, "_goals", side_effect=guarded_goals):
            out, _, unresolved = exact._main_rows_no_target_read(Path(td), lower, upper)
        self.assertEqual(len(out), 1)
        self.assertEqual(score_accesses, ["1"])
        self.assertEqual(unresolved, [])
        self.assertLess(out[0].kickoff, upper)

    def test_missing_same_day_time_is_excluded_before_score_access(self):
        lower = datetime(2026, 9, 1, tzinfo=timezone.utc)
        upper = datetime(2026, 9, 7, 18, 45, tzinfo=timezone.utc)
        rows = [{"Date": "07/09/2026", "Time": "", "HomeTeam": "Unknown A", "AwayTeam": "Unknown B", "FTHG": "DO_NOT_READ", "FTAG": "DO_NOT_READ"}]
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(exact.live, "MAIN_EUROPE", {"ITA_SerieA": "I1"}), \
             mock.patch.object(exact.live, "_cross_year_starts", return_value=[2026]), \
             mock.patch.object(exact.live, "_fetch", return_value=(b"payload", "b" * 64)), \
             mock.patch.object(exact.live, "_decode_csv", return_value=rows), \
             mock.patch.object(exact.live, "_goals", side_effect=AssertionError("score field must not be touched")):
            out, _, unresolved = exact._main_rows_no_target_read(Path(td), lower, upper)
        self.assertEqual(out, [])
        self.assertEqual(len(unresolved), 1)

    def test_install_changes_only_research_replay_hooks(self):
        module = SimpleNamespace()
        audit = exact.install(module)
        self.assertIs(module._safe_history_upper, exact.exact_history_upper)
        self.assertIs(module._research_v1_rows, exact.research_v1_rows)
        self.assertIs(module._current_xg_labels, exact.research_xg_labels)
        self.assertIs(module._ucl_history, exact.ucl_history)
        self.assertFalse(audit["production_source_changed"])
        self.assertFalse(audit["formal_scope_changed"])
        self.assertFalse(audit["strict_pit_claimed"])


class ReceiptContractTests(unittest.TestCase):
    def test_receipt_enrichment_records_required_provenance_without_prediction_change(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            original_prediction_sha = "c" * 64
            receipt = {
                "mode": replay.MODE,
                "strict_pit_claimed": False,
                "result_excluded": True,
                "target_fixture_excluded": True,
                "post_kickoff_events_excluded": True,
                "prediction_sha": original_prediction_sha,
                "receipt_sha": "d" * 64,
                "reconstruction_audit": {"history_upper_exclusive": "2026-09-07T18:45:00+00:00"},
                "formal_binding": {"runtime_current_sha256": "e" * 64, "runtime_formal_head": "f" * 40},
                "state_integrity_guard": {"status": "PASS"},
                "model_route": "FROZEN_V1_EXACT_FALLBACK",
                "fallback_exact_v1": True,
                "fusion_weights": {"xg": 0.75, "v1": 0.25},
            }
            (out / "prediction_receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
            result = receipt_contract._enrich(out, {"status": "PASS", "prediction_sha": original_prediction_sha})
            enriched = json.loads((out / "prediction_receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(enriched["prediction_sha"], original_prediction_sha)
            self.assertEqual(result["prediction_sha"], original_prediction_sha)
            self.assertEqual(enriched["request_mode"], replay.MODE)
            self.assertTrue(enriched["retrospective"])
            self.assertTrue(enriched["research_only"])
            self.assertFalse(enriched["strict_pit_claimed"])
            self.assertTrue(enriched["same_batch_predict_before_update"])
            self.assertEqual(enriched["history_event_cutoff"], "2026-09-07T18:45:00+00:00")
            self.assertEqual(enriched["actual_current_sha"], "e" * 64)
            self.assertEqual(enriched["formal_model_head"], "f" * 40)
            self.assertEqual(enriched["receipt_artifact_id"], "PENDING_TERMINAL_ARTIFACT_BINDING")
            self.assertNotEqual(enriched["receipt_sha"], "d" * 64)


if __name__ == "__main__":
    unittest.main()
