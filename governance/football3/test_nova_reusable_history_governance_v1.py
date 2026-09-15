#!/usr/bin/env python3
from __future__ import annotations

import json
import unittest
from pathlib import Path

import nova_reusable_history_governance_v1 as g

SHA = "a" * 64


def event(role, seq, exposed=False, assigned=None):
    return {
        "candidate_id": "N1",
        "match_id": "m1",
        "role": role,
        "assigned_at": assigned or f"2026-01-0{seq}T00:00:00+00:00",
        "event_seq": seq,
        "research_label_exposed_before_assignment": exposed,
    }


def pred(origin="OOF"):
    return {
        "candidate_id": "N1",
        "match_id": "m1",
        "model_sha": "model-123",
        "fold_id": "outer-01",
        "prediction_origin": origin,
        "prediction_sha256": SHA,
        "outer_train_end": "2025-12-31T23:59:59+00:00",
        "outer_score_start": "2026-01-01T00:00:00+00:00",
    }


class ReusableHistoryGovernanceTests(unittest.TestCase):
    def test_roles_are_exact(self):
        self.assertEqual(
            g.ROLES,
            ("TRAIN", "DEVELOPMENT", "REUSABLE_BENCHMARK", "CANDIDATE_CONFIRMATION"),
        )

    def test_match_record_requires_stable_identity_and_sha(self):
        row = {
            "match_id": "m1",
            "competition_id": "ENG_PL",
            "season": "2024/25",
            "kickoff": "2024-08-16T19:00:00+00:00",
            "home_team_id": "h",
            "away_team_id": "a",
            "source_id": "source",
            "source_revision": "rev1",
            "input_sha256": SHA,
        }
        g.validate_match_record(row)

    def test_confirmation_rejects_prior_research_label_exposure(self):
        with self.assertRaises(g.GovernanceError):
            g.validate_role_event(event("CANDIDATE_CONFIRMATION", 1, exposed=True))

    def test_confirmation_is_one_time_and_demotes_after_scoring(self):
        history = [
            event("CANDIDATE_CONFIRMATION", 1),
            event("REUSABLE_BENCHMARK", 2),
        ]
        g.validate_confirmation_scoring(history, "2026-01-03T00:00:00+00:00")
        self.assertEqual(g.validate_candidate_role_history(history), "REUSABLE_BENCHMARK")

    def test_scored_confirmation_cannot_remain_confirmation(self):
        with self.assertRaises(g.GovernanceError):
            g.validate_confirmation_scoring(
                [event("CANDIDATE_CONFIRMATION", 1)],
                "2026-01-02T00:00:00+00:00",
            )

    def test_arbitrary_role_reassignment_is_forbidden(self):
        with self.assertRaises(g.GovernanceError):
            g.validate_candidate_role_history(
                [event("TRAIN", 1), event("DEVELOPMENT", 2)]
            )

    def test_outer_and_inner_folds_are_nested_and_chronological(self):
        folds = [{
            "fold_id": "o1",
            "outer_train_end": "2024-12-31T23:59:59+00:00",
            "outer_score_start": "2025-01-01T00:00:00+00:00",
            "outer_score_end": "2025-03-31T23:59:59+00:00",
            "inner_folds": [{
                "inner_train_end": "2024-06-30T23:59:59+00:00",
                "inner_score_start": "2024-07-01T00:00:00+00:00",
                "inner_score_end": "2024-09-30T23:59:59+00:00",
            }],
        }]
        g.validate_outer_fold_sequence(folds)

    def test_outer_fold_rejects_future_leakage(self):
        bad = {
            "fold_id": "o1",
            "outer_train_end": "2025-01-02T00:00:00+00:00",
            "outer_score_start": "2025-01-01T00:00:00+00:00",
            "outer_score_end": "2025-03-31T00:00:00+00:00",
            "inner_folds": [{
                "inner_train_end": "2024-06-30T00:00:00+00:00",
                "inner_score_start": "2024-07-01T00:00:00+00:00",
                "inner_score_end": "2024-08-01T00:00:00+00:00",
            }],
        }
        with self.assertRaises(g.GovernanceError):
            g.validate_outer_fold(bad)

    def test_inner_fold_must_stay_inside_outer_training_window(self):
        bad = {
            "fold_id": "o1",
            "outer_train_end": "2024-12-31T00:00:00+00:00",
            "outer_score_start": "2025-01-01T00:00:00+00:00",
            "outer_score_end": "2025-03-31T00:00:00+00:00",
            "inner_folds": [{
                "inner_train_end": "2024-11-01T00:00:00+00:00",
                "inner_score_start": "2024-12-01T00:00:00+00:00",
                "inner_score_end": "2025-01-01T00:00:00+00:00",
            }],
        }
        with self.assertRaises(g.GovernanceError):
            g.validate_outer_fold(bad)

    def test_reusable_benchmark_report_requires_oof(self):
        g.validate_prediction_record(pred("OOF"), "REUSABLE_BENCHMARK", reported=True)
        with self.assertRaises(g.GovernanceError):
            bad = pred("OOF")
            bad["prediction_origin"] = "FITTED_IN_SAMPLE"
            g.validate_prediction_record(bad, "REUSABLE_BENCHMARK", reported=True)

    def test_confirmation_report_requires_confirmation_oos(self):
        g.validate_prediction_record(
            pred("CONFIRMATION_OOS"), "CANDIDATE_CONFIRMATION", reported=True
        )
        with self.assertRaises(g.GovernanceError):
            g.validate_prediction_record(pred("OOF"), "CANDIDATE_CONFIRMATION", reported=True)

    def test_train_and_development_cannot_supply_primary_reported_score(self):
        with self.assertRaises(g.GovernanceError):
            g.validate_prediction_record(pred("OOF"), "TRAIN", reported=True)
        with self.assertRaises(g.GovernanceError):
            g.validate_prediction_record(pred("OOF"), "DEVELOPMENT", reported=True)

    def test_combined_expert_requires_component_oof_and_meta_oof(self):
        combo = {
            "match_id": "m1",
            "meta_training_origin": "OOF",
            "components": [
                {"match_id": "m1", "prediction_origin": "OOF", "model_sha": "a", "fold_id": "f1"},
                {"match_id": "m1", "prediction_origin": "OOF", "model_sha": "b", "fold_id": "f1"},
            ],
        }
        g.validate_combination_record(combo)

        bad = json.loads(json.dumps(combo))
        bad["components"][1]["prediction_origin"] = "FITTED_IN_SAMPLE"
        with self.assertRaises(g.GovernanceError):
            g.validate_combination_record(bad)

        bad2 = json.loads(json.dumps(combo))
        bad2["meta_training_origin"] = "FITTED_IN_SAMPLE"
        with self.assertRaises(g.GovernanceError):
            g.validate_combination_record(bad2)

    def test_small_stable_positive_gain_can_enter_result_library(self):
        g.validate_result_library_entry({
            "candidate_id": "N1",
            "evidence_role": "REUSABLE_BENCHMARK",
            "prediction_origin": "OOF",
            "stable_across_folds": True,
            "primary_metric": "LogLoss",
            "delta": -0.0005,
            "classification": "POSITIVE_SIGNAL",
        })

    def test_reusable_benchmark_cannot_grant_promotion(self):
        with self.assertRaises(g.GovernanceError):
            g.validate_result_library_entry({
                "candidate_id": "N1",
                "evidence_role": "REUSABLE_BENCHMARK",
                "prediction_origin": "OOF",
                "stable_across_folds": True,
                "primary_metric": "LogLoss",
                "delta": -0.003,
                "classification": "PROMOTION_CANDIDATE",
            })

    def test_duplicate_reported_prediction_for_same_candidate_match_model_rejected(self):
        p = pred("OOF")
        with self.assertRaises(g.GovernanceError):
            g.validate_unique_reported_predictions([p, dict(p)])

    def test_contract_self_check(self):
        contract_path = Path(__file__).with_name("nova_reusable_history_contract_v1.json")
        receipt = g.self_check_receipt(contract_path)
        self.assertEqual(receipt["status"], "PASS")
        self.assertTrue(receipt["historical_rows_are_permanent"])
        self.assertTrue(receipt["ensemble_requires_component_oof"])
        self.assertFalse(receipt["formal_v2_changed"])


if __name__ == "__main__":
    unittest.main()
