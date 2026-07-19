import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import build_team_strengths
import build_training_dataset
from platform_core import MatchRow


MATCHES = [
    MatchRow("TEST", "2024", "regular", datetime(2024, 1, 1, tzinfo=timezone.utc), "Alpha", "Beta", 2, 0, "a.csv"),
    MatchRow("TEST", "2024", "regular", datetime(2024, 1, 8, tzinfo=timezone.utc), "Beta", "Alpha", 1, 1, "a.csv"),
    MatchRow("TEST", "2024", "regular", datetime(2024, 1, 15, tzinfo=timezone.utc), "Alpha", "Beta", 0, 1, "a.csv"),
]
CONFIG = {
    "generated_feature_status": "descriptive_feature_only_weight_0_until_time_ordered_validation",
    "half_life_days": 180,
    "recent_windows": [2],
    "minimum_matches_for_stable_status": 2,
    "minimum_home_or_away_matches": 1,
    "elo": {"initial_rating": 1500.0, "home_advantage": 60.0, "k_factor": 20.0, "season_regression_fraction": 0.25},
    "hard_gates": {"identity_collision_is_failure": True},
}
COMPETITION = {"competition_id": "TEST", "name_zh": "测试", "current_season_status": "available"}


class TeamStrengthTests(unittest.TestCase):
    @patch.object(build_team_strengths, "read_processed_matches", return_value=MATCHES)
    @patch.object(build_team_strengths, "sha256_file", return_value="0" * 64)
    @patch.object(build_team_strengths.Path, "glob", return_value=[])
    def test_build_descriptive_snapshot(self, _glob, _hash, _read):
        snapshot = build_team_strengths.build_competition_snapshot(COMPETITION, CONFIG)
        self.assertEqual(snapshot["match_count"], 3)
        self.assertEqual(snapshot["team_count"], 2)
        alpha = next(item for item in snapshot["teams"] if item["team_name"] == "Alpha")
        self.assertEqual(alpha["overall"]["matches"], 3)
        self.assertIn("2", alpha["recent"])

    @patch.object(build_training_dataset, "read_processed_matches", return_value=MATCHES)
    def test_training_features_are_pre_match(self, _read):
        rows, audit = build_training_dataset.build_competition(COMPETITION, CONFIG)
        self.assertEqual(rows[0]["home_history_matches"], 0)
        self.assertEqual(rows[0]["away_history_matches"], 0)
        self.assertEqual(rows[1]["home_history_matches"], 1)
        self.assertTrue(audit["leakage_controls"]["features_use_only_prior_matches"])


if __name__ == "__main__":
    unittest.main()
