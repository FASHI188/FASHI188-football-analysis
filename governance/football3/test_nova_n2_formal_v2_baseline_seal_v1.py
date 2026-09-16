import importlib.util
import pathlib
import unittest

HERE = pathlib.Path(__file__).resolve().parent
TARGET = HERE / "nova_n2_formal_v2_baseline_seal_v1.py"
spec = importlib.util.spec_from_file_location("n2seal", TARGET)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


class BaselineSealTests(unittest.TestCase):
    def test_mix_normalizes(self):
        v = {"p_home": 0.4, "p_draw": 0.3, "p_away": 0.3}
        x = {"p_home": 0.5, "p_draw": 0.2, "p_away": 0.3, "dynamic": {"fallback_exact_v1": False}}
        q = mod.mix(v, x)
        self.assertAlmostEqual(sum(q.values()), 1.0, 12)
        self.assertAlmostEqual(q["p_home"], 0.475, 12)

    def test_fallback_must_be_exact(self):
        v = {"p_home": 0.4, "p_draw": 0.3, "p_away": 0.3}
        x = {**v, "dynamic": {"fallback_exact_v1": True}}
        self.assertEqual(mod.mix(v, x), v)
        bad = {"p_home": 0.41, "p_draw": 0.29, "p_away": 0.3, "dynamic": {"fallback_exact_v1": True}}
        with self.assertRaises(mod.SealError):
            mod.mix(v, bad)

    def test_target_result_fields_fail_closed(self):
        with self.assertRaises(mod.SealError):
            mod.validate_target_row({"season_start": 2022, "result": "H"})

    def test_join_key_normalizes_league_and_utc(self):
        row = {"league": "Serie A", "kickoff": "2023-01-01T10:00:00Z", "home_team_id": "h", "away_team_id": "a"}
        self.assertEqual(mod.join_key(row), ("2023-01-01T10:00:00+00:00", "h", "a", "Serie_A"))


if __name__ == "__main__":
    unittest.main()
