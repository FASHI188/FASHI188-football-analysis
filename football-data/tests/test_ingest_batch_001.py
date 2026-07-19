import importlib.util
import json
import sys
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "engine" / "ingest_batch_001.py"
spec = importlib.util.spec_from_file_location("ingest_batch_001", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


class IngestionTests(unittest.TestCase):
    def setUp(self):
        self.spec = mod.DownloadSpec(
            league_id="TEST_League",
            season="2025/26",
            source_code="T0",
            url="https://example.invalid/T0.csv",
            required=True,
            source_type="main",
        )

    def test_valid_csv_parses_and_normalizes_result(self):
        content = (
            "Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,B365H,B365D,B365A\n"
            "01/08/25,Alpha,Beta,2,1,H,1.80,3.50,4.40\n"
            "02/08/25,Gamma,Delta,0,0,D,2.20,3.10,3.20\n"
        ).encode()
        rows, fields = mod.parse_csv(content, self.spec)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["FTR"], "H")
        self.assertEqual(rows[1]["FTR"], "D")
        self.assertIn("B365H", fields)

    def test_duplicate_match_key_fails(self):
        content = (
            "Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n"
            "01/08/25,Alpha,Beta,2,1,H\n"
            "01/08/25,Alpha,Beta,3,1,H\n"
        ).encode()
        with self.assertRaises(mod.DataError):
            mod.parse_csv(content, self.spec)

    def test_result_mismatch_fails(self):
        content = (
            "Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n"
            "01/08/25,Alpha,Beta,2,1,A\n"
        ).encode()
        with self.assertRaises(mod.DataError):
            mod.parse_csv(content, self.spec)

    def test_profile_probability_conservation(self):
        content = (
            "Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,B365H,B365D,B365A\n"
            "01/08/25,Alpha,Beta,2,1,H,1.80,3.50,4.40\n"
            "02/08/25,Gamma,Delta,0,0,D,2.20,3.10,3.20\n"
            "03/08/25,Eta,Theta,1,3,A,2.00,3.20,3.80\n"
            "04/08/25,Iota,Kappa,5,2,H,1.40,4.60,7.20\n"
        ).encode()
        rows, _ = mod.parse_csv(content, self.spec)
        profile = mod.build_profile("TEST_League", rows, [])
        self.assertAlmostEqual(sum(profile["result_distribution"].values()), 1.0, places=7)
        self.assertAlmostEqual(sum(profile["total_goals_0_7plus"].values()), 1.0, places=7)
        self.assertEqual(profile["matches"], 4)
        self.assertEqual(profile["seven_plus_rate"], 0.25)

    def test_source_registry_has_ten_unique_leagues(self):
        config_path = Path(__file__).resolve().parents[1] / "config" / "league_sources_batch_001.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        ids = [item["league_id"] for item in config["leagues"]]
        self.assertEqual(len(ids), 10)
        self.assertEqual(len(set(ids)), 10)


if __name__ == "__main__":
    unittest.main()
