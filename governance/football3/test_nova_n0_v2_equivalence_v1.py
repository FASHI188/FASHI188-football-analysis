#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

import nova_n0_v2_equivalence_v1 as n0
import nova_stage0_foundation_v1 as stage0

HERE = Path(__file__).resolve().parent


def formal_fixture() -> dict:
    cells = [
        {"home_goals": 0, "away_goals": 0, "probability": 0.10},
        {"home_goals": 0, "away_goals": 1, "probability": 0.10},
        {"home_goals": 0, "away_goals": 2, "probability": 0.05},
        {"home_goals": 1, "away_goals": 0, "probability": 0.15},
        {"home_goals": 1, "away_goals": 1, "probability": 0.20},
        {"home_goals": 1, "away_goals": 2, "probability": 0.10},
        {"home_goals": 2, "away_goals": 0, "probability": 0.10},
        {"home_goals": 2, "away_goals": 1, "probability": 0.10},
        {"home_goals": 2, "away_goals": 2, "probability": 0.10}
    ]
    return {
        "schema_version": "fixture-only",
        "fixture_id": "fx-n0-001",
        "competition_id": "ENG_PremierLeague",
        "season": "2026/27",
        "kickoff": "2026-09-20T15:00:00+00:00",
        "home_team_id": "home-1",
        "away_team_id": "away-1",
        "score_matrix": cells,
        "p_home": 0.35,
        "p_draw": 0.40,
        "p_away": 0.25
    }


class NovaN0Test(unittest.TestCase):
    def test_contract_is_design_locked(self):
        contract = json.loads((HERE / "nova_n0_contract_v1.json").read_text(encoding="utf-8"))
        self.assertEqual(contract["project"], "N0")
        self.assertEqual(contract["status"], "DESIGN_LOCKED")
        self.assertEqual(contract["exact_base"]["head"], n0.STAGE0_HEAD)
        self.assertEqual(contract["max_experiment_batches"], 1)
        self.assertFalse(contract["tuning_allowed"])
        self.assertFalse(contract["data"]["new_label_reads_allowed"])

    def test_unified_outputs_close(self):
        out = n0.derive_unified_outputs(formal_fixture()["score_matrix"])
        self.assertAlmostEqual(out["probability_sum"], 1.0, places=14)
        self.assertAlmostEqual(out["one_x_two"]["home"], 0.35, places=14)
        self.assertAlmostEqual(out["one_x_two"]["draw"], 0.40, places=14)
        self.assertAlmostEqual(out["one_x_two"]["away"], 0.25, places=14)
        self.assertAlmostEqual(out["over_2_5"], 0.30, places=14)
        self.assertAlmostEqual(out["btts_yes"], 0.50, places=14)
        self.assertEqual(out["model_center"]["score"], "1-1")
        self.assertAlmostEqual(out["model_center"]["probability"], 0.20, places=14)

    def test_formal_payload_verification(self):
        out = n0.verify_formal_v2_payload(formal_fixture())
        self.assertLessEqual(out["max_abs_one_x_two_residual"], n0.MATRIX_TOLERANCE)
        self.assertEqual(len(out["formal_prediction_sha256"]), 64)

    def test_matrix_1x2_mismatch_fails_closed(self):
        bad = formal_fixture()
        bad["p_home"] = 0.36
        bad["p_draw"] = 0.39
        with self.assertRaises(n0.NovaN0Error):
            n0.verify_formal_v2_payload(bad)

    def test_matrix_mass_drift_fails_closed(self):
        bad = formal_fixture()
        bad["score_matrix"][0]["probability"] = 0.11
        with self.assertRaises(n0.NovaN0Error):
            n0.verify_formal_v2_payload(bad)

    def test_identity_missing_fails_closed(self):
        bad = formal_fixture()
        del bad["fixture_id"]
        with self.assertRaises(n0.NovaN0Error):
            n0.verify_formal_v2_payload(bad)

    def test_home_away_collision_fails_closed(self):
        bad = formal_fixture()
        bad["away_team_id"] = bad["home_team_id"]
        with self.assertRaises(n0.NovaN0Error):
            n0.verify_formal_v2_payload(bad)

    def test_inactive_expert_passthrough_same_object(self):
        payload = formal_fixture()
        experts = [stage0.empty_expert("n1"), stage0.empty_expert("n2")]
        out = n0.apply_inactive_experts_exact_passthrough(payload, adapter_key="ENG_PREMIER_LEAGUE", experts=experts)
        self.assertIs(out, payload)

    def test_nonzero_expert_rejected(self):
        payload = formal_fixture()
        bad = stage0.ExpertOutput(name="bad", status=stage0.NOT_AVAILABLE, weight=1, matrix_delta=0)
        with self.assertRaises(stage0.NovaStage0ContractError):
            n0.apply_inactive_experts_exact_passthrough(payload, adapter_key="ENG_PREMIER_LEAGUE", experts=[bad])

    def test_unknown_adapter_rejected(self):
        with self.assertRaises(stage0.FormalBaselineUnavailable):
            n0.apply_inactive_experts_exact_passthrough(formal_fixture(), adapter_key="UNKNOWN", experts=[])

    def test_frozen_v1_fallback_hash_exact(self):
        payload = formal_fixture()
        copied = copy.deepcopy(payload)
        out = n0.assert_frozen_v1_exact_fallback(
            frozen_v1_payload=payload,
            formal_payload=copied,
            audit={"route": "FROZEN_V1_EXACT_FALLBACK", "fallback_exact_v1": True},
        )
        self.assertEqual(out["frozen_v1_sha256"], out["formal_sha256"])

    def test_frozen_v1_fallback_value_change_rejected(self):
        payload = formal_fixture()
        changed = copy.deepcopy(payload)
        changed["engine"] = "changed"
        with self.assertRaises(n0.NovaN0Error):
            n0.assert_frozen_v1_exact_fallback(
                frozen_v1_payload=payload,
                formal_payload=changed,
                audit={"route": "FROZEN_V1_EXACT_FALLBACK", "fallback_exact_v1": True},
            )

    def test_receipt_is_score_blind_and_nonactivating(self):
        receipt = n0.build_n0_receipt(
            formal_payload=formal_fixture(),
            adapter_key="ENG_PREMIER_LEAGUE",
            experts=[stage0.empty_expert("n1")],
            route="FUSION_V2_ACTIVE",
        )
        stage0.assert_score_blind(receipt)
        self.assertFalse(receipt["training_performed"])
        self.assertFalse(receipt["new_labels_read"])
        self.assertFalse(receipt["model_activated"])
        self.assertFalse(receipt["formal_payload_mutated"])

    def test_all_seven_stage0_adapters_can_passthrough(self):
        for adapter_key in stage0.TARGET_ADAPTERS:
            payload = formal_fixture()
            out = n0.apply_inactive_experts_exact_passthrough(payload, adapter_key=adapter_key, experts=[])
            self.assertIs(out, payload)

    def test_hash_is_order_stable_for_object_keys(self):
        self.assertEqual(n0.canonical_json_sha256({"b": 1, "a": 2}), n0.canonical_json_sha256({"a": 2, "b": 1}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
