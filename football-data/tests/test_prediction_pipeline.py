import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import match_pipeline
from platform_core import atomic_write_json, sha256_json


class PredictionPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.team_root = self.root / "team_strengths"
        snapshot = {
            "feature_status": "descriptive_feature_only_weight_0_until_time_ordered_validation",
            "data_as_of": "2026-07-01",
            "teams": [
                {"team_name": "Alpha", "normalized_token": "alpha", "status": "descriptive_features_available"},
                {"team_name": "Beta", "normalized_token": "beta", "status": "descriptive_features_available"},
            ],
        }
        atomic_write_json(self.team_root / "TEST" / "latest.json", snapshot)
        self.registry = {
            "TEST": {
                "competition_id": "TEST",
                "name_zh": "测试联赛",
                "profile_status": "available",
                "current_season_status": "available",
                "stage_status": "regular",
                "historical_market_status": "unavailable",
            }
        }

    def tearDown(self):
        self.temp.cleanup()

    def _input(self):
        return {
            "competition_id": "TEST",
            "home_team": "Alpha",
            "away_team": "Beta",
            "kickoff_utc": "2026-07-20T12:00:00Z",
            "freeze_time_utc": "2026-07-20T10:00:00Z",
            "settlement": "90_minutes_including_stoppage",
            "market_snapshot": {
                "observed_at_utc": "2026-07-20T09:55:00Z",
                "sources": [
                    {"name": "Book A", "group": "A", "observed_at_utc": "2026-07-20T09:55:00Z", "tradable": True},
                    {"name": "Book B", "group": "B", "observed_at_utc": "2026-07-20T09:58:00Z", "tradable": True},
                ],
                "one_x_two": {"home": 2.5, "draw": 3.2, "away": 2.9},
                "asian_handicap": {"line": 0.0, "home": 1.9, "away": 1.95},
                "total_goals": {"line": 2.5, "over": 1.95, "under": 1.9},
            },
            "lineup_evidence": {"status": "official", "observed_at_utc": "2026-07-20T09:50:00Z", "sources": ["official"]},
        }

    @patch.object(match_pipeline, "registry_map")
    def test_prepare_validate_freeze_and_audit(self, registry_map_mock):
        registry_map_mock.return_value = self.registry
        with patch.object(match_pipeline, "TEAM_STRENGTH_ROOT", self.team_root):
            context = match_pipeline.prepare_match_context(self._input())
        self.assertTrue(context["gates"]["ev_may_be_calculated"])

        matrix = [
            {"home_goals": 1, "away_goals": 0, "probability": 0.4},
            {"home_goals": 0, "away_goals": 0, "probability": 0.2},
            {"home_goals": 0, "away_goals": 1, "probability": 0.4},
        ]
        calculation = {
            "schema_version": "1.0",
            "freeze_context_hash": context["context_hash"],
            "rule_version": "TEST",
            "module_states": {
                "direct_total_goals": "通过",
                "conditional_goal_difference": "通过",
                "unified_score_matrix": "通过",
                "market_coordination": "未启用",
            },
            "probabilities": {
                "one_x_two": {"home": 0.4, "draw": 0.2, "away": 0.4},
                "total_goals": {"0": 0.2, "1": 0.8, "2": 0.0, "3": 0.0, "4": 0.0, "5": 0.0, "6": 0.0, "7+": 0.0},
                "btts_yes": 0.0,
                "score_matrix": matrix,
            },
            "conclusions": {
                "result_text": "主客并列",
                "total_goals_text": "1球为主",
                "score_text": "0-1",
                "top_score": "0-1",
                "second_score": "1-0",
                "final_line": "弃权；可信等级D；No Bet。",
            },
        }
        validation = match_pipeline.validate_calculation_output(context, calculation)
        self.assertEqual(validation["status"], "通过")
        freeze_path, freeze = match_pipeline.freeze_prediction(context, calculation, validation, self.root / "freezes")
        self.assertTrue(freeze_path.exists())
        audit_path, audit = match_pipeline.audit_prediction(
            freeze, {"home_goals": 1, "away_goals": 0, "source": "official"}, self.root / "audits"
        )
        self.assertTrue(audit_path.exists())
        self.assertTrue(audit["scores"]["exact_score"]["top3_hit"])

    def test_unavailable_fixed_text_is_enforced(self):
        context = {"context_hash": "a" * 64, "gates": {"ev_may_be_calculated": False}}
        calculation = {
            "freeze_context_hash": "a" * 64,
            "module_states": {
                "direct_total_goals": "不可用",
                "conditional_goal_difference": "不可用",
                "unified_score_matrix": "不可用",
                "market_coordination": "未启用",
            },
            "conclusions": {
                "result_text": "弃权",
                "total_goals_text": "总进球分布不可用。",
                "score_text": "精确比分不可用。",
                "final_line": "弃权；可信等级D；价格不可用。",
            },
        }
        report = match_pipeline.validate_calculation_output(context, calculation)
        self.assertEqual(report["status"], "通过")


if __name__ == "__main__":
    unittest.main()
