import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("nova_n3_finishing_keeper_residual_v1.py")
SPEC = importlib.util.spec_from_file_location("n3", MODULE_PATH)
n3 = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(n3)


class N3Tests(unittest.TestCase):
    def test_reciprocal_and_residual_semantics(self):
        h = {"npxg": 1.8, "npxga": 0.9, "scored": 2.0, "missed": 1.0}
        a = {"npxg": 0.9, "npxga": 1.8, "scored": 1.0, "missed": 2.0}
        n3.validate_reciprocal(h, a)
        self.assertAlmostEqual(h["scored"] - h["npxg"], 0.2)
        self.assertAlmostEqual(h["npxga"] - h["missed"], -0.1)

    def test_same_kickoff_atomic_and_release_delay(self):
        rows = [
            {"fixture_id": "a", "kickoff": "2020-01-01T12:00:00Z", "release_at": "2020-01-01T15:00:00Z", "home_team_id": "H", "away_team_id": "A", "home_finishing_residual": 1.0, "home_keeper_residual": 0.5, "away_finishing_residual": -1.0, "away_keeper_residual": -0.5, "league": "EPL"},
            {"fixture_id": "b", "kickoff": "2020-01-01T12:00:00Z", "release_at": "2020-01-01T15:00:00Z", "home_team_id": "H", "away_team_id": "B", "home_finishing_residual": 2.0, "home_keeper_residual": 1.0, "away_finishing_residual": 0.0, "away_keeper_residual": 0.0, "league": "EPL"},
            {"fixture_id": "c", "kickoff": "2020-01-01T14:00:00Z", "release_at": "2020-01-01T17:00:00Z", "home_team_id": "H", "away_team_id": "C", "home_finishing_residual": 0.0, "home_keeper_residual": 0.0, "away_finishing_residual": 0.0, "away_keeper_residual": 0.0, "league": "EPL"},
            {"fixture_id": "d", "kickoff": "2020-01-01T16:00:00Z", "release_at": "2020-01-01T19:00:00Z", "home_team_id": "H", "away_team_id": "D", "home_finishing_residual": 0.0, "home_keeper_residual": 0.0, "away_finishing_residual": 0.0, "away_keeper_residual": 0.0, "league": "EPL"},
        ]
        raw = n3.build_raw_features(rows, "R1_W5")
        self.assertIsNone(raw[0][0])
        self.assertIsNone(raw[1][0])
        self.assertIsNone(raw[2][0])
        self.assertAlmostEqual(raw[3][0], 1.5)

    def test_mix_fallback_and_weighted(self):
        v1 = {"p_home": 0.5, "p_draw": 0.3, "p_away": 0.2}
        xg = {"p_home": 0.5, "p_draw": 0.3, "p_away": 0.2, "dynamic": {"fallback_exact_v1": True}}
        self.assertEqual(n3.mix_formal(v1, xg), v1)
        xg2 = {"p_home": 0.4, "p_draw": 0.35, "p_away": 0.25, "dynamic": {"fallback_exact_v1": False}}
        mixed = n3.mix_formal(v1, xg2)
        self.assertAlmostEqual(sum(mixed.values()), 1.0)
        self.assertAlmostEqual(mixed["p_home"], 0.425)

    def test_metrics_perfect_better(self):
        outcomes = [0, 1, 2]
        perfect = [[0.9, 0.05, 0.05], [0.05, 0.9, 0.05], [0.05, 0.05, 0.9]]
        flat = [[1 / 3, 1 / 3, 1 / 3]] * 3
        self.assertLess(n3.metrics(perfect, outcomes)["logloss"], n3.metrics(flat, outcomes)["logloss"])
        self.assertEqual(n3.metrics(perfect, outcomes)["top1"], 1.0)

    def test_finalize_does_not_open_isolated_on_dev_negative(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dev = {"classification": "FAIL_RESEARCH_DIRECTION", "selected_route": None, "best_signal_route": "R1_W5", "best_formal_minus_candidate_logloss": -0.001}
            path = root / "dev.json"
            path.write_text(json.dumps(dev), encoding="utf-8")
            out = n3.finalize(path, None, root / "out")
            self.assertEqual(out["classification"], "FAIL_RESEARCH_DIRECTION")
            self.assertEqual(out["isolated_2021_labels_read"], 0)
            self.assertEqual(out["matrix_delta"], 0)

    def test_finalize_preserves_development_positive_signal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dev = {"classification": "POSITIVE_SIGNAL_DEVELOPMENT_ONLY", "selected_route": None, "best_signal_route": "R3_EWMA035", "best_formal_minus_candidate_logloss": 0.0001}
            path = root / "dev.json"
            path.write_text(json.dumps(dev), encoding="utf-8")
            out = n3.finalize(path, None, root / "out")
            self.assertEqual(out["classification"], "POSITIVE_SIGNAL")
            self.assertEqual(out["isolated_2021_labels_read"], 0)


if __name__ == "__main__":
    unittest.main()
