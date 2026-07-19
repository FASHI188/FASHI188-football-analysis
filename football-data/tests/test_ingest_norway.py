from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "engine" / "ingest_norway.py"
SPEC = importlib.util.spec_from_file_location("ingest_norway", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class NorwayIngestionTests(unittest.TestCase):
    def test_alias_filter_and_playoff_exclusion(self):
        config = {
            "competition_id": "NOR_Eliteserien",
            "source": {"source_code": "NOR"},
            "row_season_field": "Season",
            "allowed_source_seasons": ["2021", "2026"],
            "season_label_map": {"2021": "2021", "2026": "2026"},
            "complete_seasons": [],
            "minimum_rows_complete_season": 1,
            "exclude_low_frequency_team_rows_below": 2,
            "excluded_rows_meaning": "playoff",
            "aliases": {"Home": "HomeTeam", "Away": "AwayTeam", "HG": "FTHG", "AG": "FTAG", "Res": "FTR"},
        }
        csv_text = "Season,Date,Home,Away,HG,AG,Res\n2020,01/01/2020,Old,Old2,1,0,H\n2021,01/01/2021,A,B,2,1,H\n2021,02/01/2021,B,A,0,0,D\n2021,03/01/2021,A,C,1,0,H\n2026,01/01/2026,A,B,3,2,H\n"
        rows, fields, audit = MODULE.canonicalize_and_filter(csv_text.encode(), config)
        self.assertIn("HomeTeam", fields)
        self.assertEqual(audit["raw_archive_rows"], 5)
        self.assertEqual(audit["excluded_playoff_rows"], 2)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["league_id"] == "NOR_Eliteserien" for row in rows))

    def test_result_mismatch_fails(self):
        config = {
            "competition_id": "NOR_Eliteserien",
            "source": {"source_code": "NOR"},
            "row_season_field": "Season",
            "allowed_source_seasons": ["2021"],
            "season_label_map": {"2021": "2021"},
            "complete_seasons": [],
            "minimum_rows_complete_season": 1,
            "exclude_low_frequency_team_rows_below": 0,
            "excluded_rows_meaning": "playoff",
            "aliases": {"Home": "HomeTeam", "Away": "AwayTeam", "HG": "FTHG", "AG": "FTAG", "Res": "FTR"},
        }
        csv_text = "Season,Date,Home,Away,HG,AG,Res\n2021,01/01/2021,A,B,2,1,A\n"
        with self.assertRaises(MODULE.DataError):
            MODULE.canonicalize_and_filter(csv_text.encode(), config)


if __name__ == "__main__":
    unittest.main()
