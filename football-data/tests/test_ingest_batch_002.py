import importlib.util
import json
import sys
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "engine" / "ingest_batch_002.py"
SPEC = importlib.util.spec_from_file_location("ingest_batch_002_test_module", MODULE_PATH)
mod = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


class Batch002Tests(unittest.TestCase):
    def test_config_has_six_unique_competitions(self):
        config = json.loads((Path(__file__).resolve().parents[1] / "config" / "league_sources_batch_002.json").read_text(encoding="utf-8"))
        ids = [item["competition_id"] for item in config["competitions"]]
        self.assertEqual(len(ids), 6)
        self.assertEqual(len(set(ids)), 6)
        self.assertIn("KOR_KLeague1", ids)
        self.assertIn("UEFA_ChampionsLeague", ids)

    def test_extra_aliases_and_filters(self):
        content = (
            "Country,League,Season,Date,Home,Away,HG,AG,Res,AvgCH,AvgCD,AvgCA\n"
            "Japan,J League,2024,01/03/2024,Alpha,Beta,2,1,H,1.8,3.4,4.2\n"
            "Japan,J League,2023,01/03/2023,Gamma,Delta,0,0,D,2.1,3.1,3.3\n"
            "Japan,J League 2,2024,02/03/2024,Eta,Theta,1,0,H,2.0,3.2,3.8\n"
            "Japan,J League,2024,03/03/2024,Iota,Kappa,,,\n"
        ).encode("utf-8")
        competition = {
            "competition_id": "JPN_J1",
            "source_code": "JPN",
            "country_candidates": ["Japan"],
            "league_candidates": ["J League"],
            "allowed_source_seasons": ["2024"],
            "season_label_map": {"2024": "2024"},
            "stage_policy": "regular",
        }
        rows, audit = mod.parse_extra_archive(content, competition)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["HomeTeam"], "Alpha")
        self.assertEqual(rows[0]["AwayTeam"], "Beta")
        self.assertEqual(rows[0]["FTR"], "H")
        self.assertEqual(rows[0]["stage"], "regular_league")
        self.assertEqual(audit["incomplete_rows_skipped"], 1)

    def test_extra_result_mismatch_fails(self):
        content = (
            "Country,League,Season,Date,Home,Away,HG,AG,Res\n"
            "Brazil,Serie A,2025,01/03/2025,Alpha,Beta,2,1,A\n"
        ).encode("utf-8")
        competition = {
            "competition_id": "BRA_SerieA",
            "source_code": "BRA",
            "country_candidates": ["Brazil"],
            "league_candidates": ["Serie A"],
            "allowed_source_seasons": ["2025"],
            "season_label_map": {"2025": "2025"},
            "stage_policy": "regular",
        }
        with self.assertRaises(mod.DataError):
            mod.parse_extra_archive(content, competition)

    def test_ucl_parses_regular_90_minute_line(self):
        content = (
            "= UEFA Champions League 2025/26\n\n"
            "▪ League, Matchday 1\n"
            "  Tue Sep 16 2025\n"
            "    18:45  Athletic Club (ESP) v Arsenal FC (ENG) 0-2 (0-0)\n"
            "           PSV (NED) v Ajax (NED) 1-1\n"
        ).encode("utf-8")
        rows, audit = mod.parse_ucl_text(content, "2025-26", "2025/26")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["stage"], "league_phase")
        self.assertEqual(rows[0]["FTR"], "A")
        self.assertEqual(rows[0]["HTHG"], "0")
        self.assertEqual(audit["parsed_90min_safe_rows"], 2)

    def test_ucl_excludes_extra_time_and_penalty_lines(self):
        content = (
            "= UEFA Champions League 2023/24\n\n"
            "▪ Final\n"
            "  Sat Jun 1 2024\n"
            "    Alpha FC v Beta FC 2-1 a.e.t.\n"
            "    Gamma FC v Delta FC 1-1 pen. 4-3\n"
            "    Safe FC v Normal FC 1-0 (0-0)\n"
        ).encode("utf-8")
        rows, audit = mod.parse_ucl_text(content, "2023-24", "2023/24")
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(audit["ambiguous_extra_time_or_penalty_lines_excluded"]), 2)
        self.assertEqual(rows[0]["stage"], "final")

    def test_profile_probability_conservation(self):
        rows = [
            {"FTHG": "2", "FTAG": "1", "FTR": "H", "season": "2025", "stage": "regular"},
            {"FTHG": "0", "FTAG": "0", "FTR": "D", "season": "2025", "stage": "regular"},
            {"FTHG": "1", "FTAG": "3", "FTR": "A", "season": "2025", "stage": "regular"},
            {"FTHG": "5", "FTAG": "2", "FTR": "H", "season": "2025", "stage": "regular"},
        ]
        profile = mod.CORE.build_profile("TEST_Competition", rows, [])
        profile["competition_id"] = profile.pop("league_id")
        mod.validate_profile(profile, 1e-6)
        self.assertEqual(profile["matches"], 4)
        self.assertAlmostEqual(sum(profile["result_distribution"].values()), 1.0, places=7)
        self.assertAlmostEqual(sum(profile["total_goals_0_7plus"].values()), 1.0, places=7)


if __name__ == "__main__":
    unittest.main()
