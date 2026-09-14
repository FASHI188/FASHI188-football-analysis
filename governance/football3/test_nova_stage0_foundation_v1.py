#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
import sys
import unittest
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import nova_stage0_foundation_v1 as nova


class NovaStage0FoundationTest(unittest.TestCase):
    def test_authority_identity_is_frozen(self) -> None:
        identity = nova.stage0_identity()
        self.assertEqual(identity["exact_base_head"], "475dedfd177b02208f550fe97a89bbd1efa52125")
        self.assertEqual(identity["formal_v2_head"], "e12f5d1193be5d81f60301cf34ab2140e11712a9")
        self.assertEqual(identity["current_sha256"], "71d54a6fd227ad2bbdddea9d0e42b88cea2022efef8faf26c7483cd99d5b8731")
        self.assertIsNone(identity["replay_coverage_start_at"])

    def test_target_adapters_are_exactly_seven(self) -> None:
        self.assertEqual(
            set(nova.TARGET_ADAPTERS),
            {"ENG_PREMIER_LEAGUE", "ESP_LALIGA", "DEU_BUNDESLIGA", "ITA_SERIE_A", "FRA_LIGUE_1", "JPN_J1", "KOR_K1"},
        )

    def test_j1_k1_are_in_current_formal_v2_scope(self) -> None:
        expected = {"JPN_J1": "JPN_J1", "KOR_K1": "KOR_KLeague1"}
        for key, formal_id in expected.items():
            adapter = nova.TARGET_ADAPTERS[key]
            self.assertTrue(adapter.formal_v2_baseline_supported)
            self.assertEqual(adapter.formal_v2_competition_id, formal_id)
            payload = {"adapter": key}
            self.assertIs(nova.formal_v2_passthrough(payload, key), payload)

    def test_five_major_adapters_map_to_existing_formal_ids(self) -> None:
        expected = {
            "ENG_PREMIER_LEAGUE": "ENG_PremierLeague",
            "ESP_LALIGA": "ESP_LaLiga",
            "DEU_BUNDESLIGA": "GER_Bundesliga",
            "ITA_SERIE_A": "ITA_SerieA",
            "FRA_LIGUE_1": "FRA_Ligue1",
        }
        self.assertEqual({k: nova.TARGET_ADAPTERS[k].formal_v2_competition_id for k in expected}, expected)

    def test_empty_expert_contract_is_exact(self) -> None:
        expert = nova.empty_expert("N1_PLACEHOLDER")
        self.assertEqual(expert.status, "NOT_AVAILABLE")
        self.assertEqual(expert.weight, 0)
        self.assertEqual(expert.matrix_delta, 0)

    def test_nonzero_stage0_expert_is_rejected(self) -> None:
        bad = nova.ExpertOutput(name="bad", weight=1)
        with self.assertRaisesRegex(nova.NovaStage0ContractError, "STAGE0_EXPERT_MUST_BE"):
            bad.validate_stage0()

    def test_matrix_passthrough_is_same_object_and_same_values(self) -> None:
        matrix = ((0.30, 0.20), (0.10, 0.40))
        out = nova.apply_stage0_experts(matrix, [nova.empty_expert("e1"), nova.empty_expert("e2")])
        self.assertIs(out, matrix)
        self.assertEqual(out, matrix)

    def test_formal_payload_passthrough_is_same_object(self) -> None:
        payload = {"one_x_two": {"H": 0.4, "D": 0.3, "A": 0.3}, "score_matrix": [[1.0]]}
        out = nova.formal_v2_passthrough(payload, "ENG_PREMIER_LEAGUE")
        self.assertIs(out, payload)

    def test_joint_matrix_derivations_close(self) -> None:
        matrix = ((0.25, 0.10, 0.05), (0.15, 0.20, 0.05), (0.05, 0.05, 0.10))
        d = nova.derive_matrix_outputs(matrix)
        self.assertAlmostEqual(d["home_win"] + d["draw"] + d["away_win"], 1.0, places=12)
        self.assertAlmostEqual(d["draw"], 0.55, places=12)
        self.assertAlmostEqual(d["over_2_5"], 0.20, places=12)
        self.assertAlmostEqual(d["btts"], 0.40, places=12)

    def test_authoritative_cutoff_is_exactly_minus_60m(self) -> None:
        kickoff = datetime(2026, 9, 14, 20, 0, tzinfo=timezone.utc)
        self.assertEqual(nova.authoritative_cutoff(kickoff), datetime(2026, 9, 14, 19, 0, tzinfo=timezone.utc))

    def test_observation_eligibility_uses_available_at_not_observed_at(self) -> None:
        cutoff = datetime(2026, 9, 14, 19, 0, tzinfo=timezone.utc)
        obs = [
            {"fixture_id": "f1", "kickoff_revision_id": "r1", "observed_at": datetime(2026,9,14,18,0,tzinfo=timezone.utc), "available_at": datetime(2026,9,14,18,59,tzinfo=timezone.utc), "id": "eligible"},
            {"fixture_id": "f1", "kickoff_revision_id": "r1", "observed_at": datetime(2026,9,14,18,0,tzinfo=timezone.utc), "available_at": datetime(2026,9,14,19,1,tzinfo=timezone.utc), "id": "late"},
            {"fixture_id": "f1", "kickoff_revision_id": "old", "observed_at": datetime(2026,9,14,18,0,tzinfo=timezone.utc), "available_at": datetime(2026,9,14,18,1,tzinfo=timezone.utc), "id": "wrong-revision"},
        ]
        selected = nova.eligible_observations(obs, fixture_id="f1", kickoff_revision_id="r1", cutoff=cutoff)
        self.assertEqual([x["id"] for x in selected], ["eligible"])

    def test_replay_ledger_key_is_deterministic_and_binding_sensitive(self) -> None:
        kwargs = dict(fixture_id="f1", kickoff_revision_id="r1", cutoff="2026-09-14T19:00:00Z", model_head="a"*40, state_sha="b"*64, input_sha="c"*64)
        a = nova.replay_ledger_key(**kwargs)
        b = nova.replay_ledger_key(**kwargs)
        self.assertEqual(a, b)
        changed = dict(kwargs)
        changed["state_sha"] = "d"*64
        self.assertNotEqual(a, nova.replay_ledger_key(**changed))

    def test_score_blind_rejects_nested_result_fields(self) -> None:
        nova.assert_score_blind({"fixture": {"home": "A", "away": "B"}, "inputs": [1, 2]})
        with self.assertRaisesRegex(nova.NovaStage0ContractError, "SCORE_BLIND_FIELD_FORBIDDEN"):
            nova.assert_score_blind({"nested": {"final_score": "2-1"}})

    def test_contract_jsons_are_self_consistent(self) -> None:
        stage0 = json.loads((HERE / "nova_stage0_contract_v1.json").read_text(encoding="utf-8"))
        registry = json.loads((HERE / "nova_research_registry_v1.json").read_text(encoding="utf-8"))
        replay = json.loads((HERE / "nova_t60_replay_contract_v1.json").read_text(encoding="utf-8"))
        self.assertEqual(stage0["legacy_v3_boundary"]["status"], "ABANDONED_NO_PROMOTION")
        self.assertFalse(stage0["stage0_science"]["training"])
        self.assertFalse(stage0["stage0_science"]["new_labels"])
        self.assertIsNone(stage0["replay_coverage_start_at"])
        self.assertEqual([p["id"] for p in registry["ordered_projects"]], ["N0","N1","N2","N3","N4","N5","N6","N7","N8","N9","N10","N11","N12"])
        self.assertEqual(registry["active_primary_project"], "N0")
        self.assertEqual(replay["route"], "NOVA_T60_FORMAL_REPLAY")
        self.assertIsNone(replay["replay_coverage_start_at"])
        self.assertIn("UNKNOWN_OR_OUTSIDE_FORMAL_SCOPE_ADAPTER_FAILS_CLOSED", replay["acceptance_tests"])

    def test_source_contains_no_dynamic_execution_or_network_imports(self) -> None:
        source = (HERE / "nova_stage0_foundation_v1.py").read_text(encoding="utf-8")
        for forbidden in ("importlib", "requests", "urllib", "subprocess", "eval(", "exec(", "compile(", "__import__"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
