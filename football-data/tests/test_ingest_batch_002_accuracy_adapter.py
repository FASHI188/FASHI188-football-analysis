import importlib.util
import sys
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "engine" / "ingest_batch_002_accuracy_adapter.py"
SPEC = importlib.util.spec_from_file_location("ingest_batch_002_accuracy_adapter_test", MODULE_PATH)
mod = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


class Batch002AccuracyAdapterTests(unittest.TestCase):
    def test_low_frequency_playoff_teams_are_excluded(self):
        rows = []
        for index in range(6):
            rows.append(
                {
                    "season": "2022",
                    "Date": f"0{index + 1}/01/2022",
                    "HomeTeam": "Regular A" if index % 2 == 0 else "Regular B",
                    "AwayTeam": "Regular B" if index % 2 == 0 else "Regular A",
                    "FTHG": "1",
                    "FTAG": "0",
                }
            )
        rows.append(
            {
                "season": "2022",
                "Date": "20/11/2022",
                "HomeTeam": "Regular A",
                "AwayTeam": "Playoff Visitor",
                "FTHG": "1",
                "FTAG": "1",
            }
        )
        competition = {"exclude_low_frequency_team_rows_below": 5}
        kept, excluded = mod._filter_low_frequency_teams(rows, competition)
        self.assertEqual(len(kept), 6)
        self.assertEqual(len(excluded), 1)
        self.assertEqual(excluded[0]["AwayTeam"], "Playoff Visitor")


if __name__ == "__main__":
    unittest.main()
