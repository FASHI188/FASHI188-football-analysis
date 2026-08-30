from __future__ import annotations

import json
import math
import unittest
from datetime import datetime, timezone
from pathlib import Path

from engine import EngineState, Fixture, Parameters, joint_matrix, kl_project_to_1x2, matrix_1x2
from optional_layers import LineupScenario, LineupState, PlayerSnapshot, expected_lineup_mixture
from strict import GovernanceError, compute_forward_row_hash, strict_nonnegative_int, validate_forward_row_schema, verify_hash_chain

HERE = Path(__file__).resolve().parent


class StrictGovernanceTests(unittest.TestCase):
    def test_strict_score_types_reject_coercion(self) -> None:
        for bad in (1.9, 0.2, True, False, "1", float("nan"), float("inf"), None):
            with self.assertRaises(GovernanceError, msg=repr(bad)):
                strict_nonnegative_int(bad, "score")
        self.assertEqual(strict_nonnegative_int(0, "score"), 0)
        self.assertEqual(strict_nonnegative_int(7, "score"), 7)

    def test_parameter_validation(self) -> None:
        with self.assertRaises(GovernanceError):
            Parameters(min_rate=2.0, max_rate=1.0)
        with self.assertRaises(GovernanceError):
            Parameters(half_life_days=float("nan"))
        with self.assertRaises(GovernanceError):
            Parameters(max_rate=float("inf"))

    def test_forward_recursive_default_deny(self) -> None:
        row = {
            "schema_version": "x", "fixture_id": "f", "provider_event_id": "p", "competition_id": "c", "season": "s",
            "canonical_home": "h", "canonical_away": "a", "kickoff_utc": "2026-09-01T10:00:00+00:00",
            "observed_at_utc": "2026-09-01T08:00:00+00:00", "prediction_cutoff": "T-60",
            "source": {"provider": "public", "capture_sha256": "a"*64, "manifest_sha256": "b"*64, "observed_at_utc": "2026-09-01T08:00:00+00:00"},
            "model": {"candidate_head": "h", "engine_sha256": "c"*64, "config_sha256": "d"*64},
            "prediction": {"matrix": [{"home_goals": 0, "away_goals": 0, "probability": 1.0}],
                           "one_x_two": {"home": 0.0, "draw": 1.0, "away": 0.0}, "uncertainty": 0.5, "cold_start_bucket": "zero"},
            "labels_present": False, "outcomes_read": False, "previous_row_hash": "0"*64, "row_hash": "0"*64,
        }
        row["row_hash"] = compute_forward_row_hash(row)
        validate_forward_row_schema(row)
        bad = json.loads(json.dumps(row)); bad["prediction"]["nested"] = {"result": "H"}; bad["row_hash"] = compute_forward_row_hash(bad)
        with self.assertRaises(GovernanceError):
            validate_forward_row_schema(bad)
        bad2 = json.loads(json.dumps(row)); bad2["final_score_90"] = "1-0"; bad2["row_hash"] = compute_forward_row_hash(bad2)
        with self.assertRaises(GovernanceError):
            validate_forward_row_schema(bad2)

    def test_hash_chain_recomputed(self) -> None:
        def make(prev: str, fid: str):
            row = {
                "schema_version": "x", "fixture_id": fid, "provider_event_id": fid, "competition_id": "c", "season": "s",
                "canonical_home": "h", "canonical_away": "a", "kickoff_utc": "2026-09-01T10:00:00+00:00",
                "observed_at_utc": "2026-09-01T08:00:00+00:00", "prediction_cutoff": "T-60",
                "source": {"provider": "public", "capture_sha256": "a"*64, "manifest_sha256": "b"*64, "observed_at_utc": "2026-09-01T08:00:00+00:00"},
                "model": {"candidate_head": "h", "engine_sha256": "c"*64, "config_sha256": "d"*64},
                "prediction": {"matrix": [{"home_goals": 0, "away_goals": 0, "probability": 1.0}],
                               "one_x_two": {"home": 0.0, "draw": 1.0, "away": 0.0}, "uncertainty": 0.5, "cold_start_bucket": "zero"},
                "labels_present": False, "outcomes_read": False, "previous_row_hash": prev, "row_hash": "0"*64,
            }
            row["row_hash"] = compute_forward_row_hash(row)
            return row
        r1 = make("0"*64, "1"); r2 = make(r1["row_hash"], "2")
        self.assertEqual(verify_hash_chain([r1, r2], "0"*64), r2["row_hash"])
        r2["prediction"]["uncertainty"] = 0.4
        with self.assertRaises(GovernanceError):
            verify_hash_chain([r1, r2], "0"*64)


class ModelInvariantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.params = Parameters(); self.engine = EngineState(self.params)
        self.fixture = Fixture("f1", "TEST_LEAGUE", "2026", datetime(2026, 9, 1, tzinfo=timezone.utc), "team_h", "team_a", 1)
        self.features = self.engine.predict_features(self.fixture)

    def test_all_joint_families_are_normalized(self) -> None:
        families = [("INDEPENDENT_POISSON_FROZEN", 0.0), ("DIXON_COLES_LOW_SCORE", -0.04),
                    ("DIAGONAL_INFLATION_BIVARIATE", 0.3), ("DYNAMIC_NB_DIAGONAL", 0.3),
                    ("DYNAMIC_NB_MARCO", 0.15), ("DYNAMIC_NB_SARMANOV", 0.75)]
        for family, dep in families:
            m = joint_matrix(family, self.features, dispersion_home=8.0, dispersion_away=9.0, dependence=dep)
            self.assertAlmostEqual(sum(sum(row) for row in m), 1.0, places=10)
            self.assertTrue(all(p >= 0 and math.isfinite(p) for row in m for p in row))
            self.assertAlmostEqual(sum(matrix_1x2(m).values()), 1.0, places=10)

    def test_minimum_kl_partition_projection(self) -> None:
        m = joint_matrix("DYNAMIC_NB_DIAGONAL", self.features, dispersion_home=8.0, dispersion_away=9.0, dependence=0.2)
        target = {"home": 0.44, "draw": 0.31, "away": 0.25}
        out = matrix_1x2(kl_project_to_1x2(m, target))
        for k in target:
            self.assertAlmostEqual(out[k], target[k], places=10)

    def test_same_cutoff_atomicity_and_strict_labels(self) -> None:
        f2 = Fixture("f2", "TEST_LEAGUE", "2026", self.fixture.kickoff, "team_c", "team_d", 1)
        before = self.engine.predict_features(f2)
        self.engine.apply_batch([self.fixture, f2], {"f1": (1, 0), "f2": (0, 0)})
        after = self.engine.predict_features(Fixture("f3", "TEST_LEAGUE", "2026", datetime(2026, 9, 2, tzinfo=timezone.utc), "team_h", "team_a", 2))
        self.assertNotEqual(before["competition_evidence"], after["competition_evidence"])
        with self.assertRaises(GovernanceError):
            self.engine.apply_batch([Fixture("f4", "TEST_LEAGUE", "2026", datetime(2026, 9, 3, tzinfo=timezone.utc), "x", "y", 1)], {"f4": (1.0, 0)})

    def test_blind_predictor_static_label_isolation(self) -> None:
        source = (HERE / "blind_predict.py").read_text(encoding="utf-8").casefold()
        self.assertNotIn("final_labels.jsonl", source); self.assertNotIn("--label-file", source)
        pure = (HERE / "engine.py").read_text(encoding="utf-8").casefold()
        for token in ("odds", "closing", "v500", "bayesian_dynamic_state_oof", "market_assist"):
            self.assertNotIn(token, pure)


class OptionalLayerTests(unittest.TestCase):
    def test_expected_lineup_is_probability_mixture(self) -> None:
        cutoff = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
        p1 = PlayerSnapshot("p1", "FW", 0.3, 0.0, 0.0, 0.7, 80.0, True, "2026-09-01T09:00:00+00:00")
        p2 = PlayerSnapshot("p2", "FW", 0.1, 0.0, 0.0, 0.3, 70.0, True, "2026-09-01T09:00:00+00:00")
        s1 = LineupScenario(0.7, (p1,), 0.1, 0.1, 0.8); s2 = LineupScenario(0.3, (p2,), 0.0, 0.1, 0.6)
        mix = expected_lineup_mixture([s1, s2], cutoff)
        self.assertIn("lineup_uncertainty", mix); self.assertGreaterEqual(mix["lineup_uncertainty"], 0.0)
        self.assertEqual(LineupState.LINEUP_UNKNOWN.value, "LINEUP_UNKNOWN")


if __name__ == "__main__":
    unittest.main()
