from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parents[1] / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import daily_single_match_v502 as daily

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "daily_single_match_v502_offline_demo.json"


class DailySingleMatchV502Tests(unittest.TestCase):
    def test_target_result_leakage_is_blocked(self):
        payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
        payload["actual_result"] = "H"
        with self.assertRaises(daily.DailyFlowError) as ctx:
            daily.normalize_input(payload)
        self.assertEqual(ctx.exception.failure_class, "TARGET_RESULT_LEAKAGE_BLOCKED")

    def test_repository_history_is_strictly_before_freeze(self):
        payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
        normalized = daily.normalize_input(payload)
        evidence = normalized["data_freshness_evidence"]
        self.assertGreater(evidence["expected_history_matches"], 0)
        self.assertEqual(evidence["latest_history_match_date"], "2026-07-20")
        self.assertLess(evidence["latest_history_match_date"], normalized["freeze_time_utc"][:10])
        self.assertIsNone(evidence["source_available_at_utc"])

    def test_live_mode_requires_verified_identity_and_freshness(self):
        payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
        payload["execution_mode"] = "live_user_supplied"
        with self.assertRaises(daily.DailyFlowError):
            daily.normalize_input(payload)
        payload["identity_evidence"]["status"] = "verified"
        payload.pop("repository_snapshot", None)
        payload["data_freshness_evidence"] = {
            "source_name": "official",
            "source_url": "https://example.invalid/official",
            "observed_at_utc": "2026-07-30T00:00:00Z",
            "expected_history_matches": 1,
            "latest_history_match_date": "2026-07-20"
        }
        normalized = daily.normalize_input(payload)
        self.assertEqual(normalized["identity_evidence"]["status"], "verified")

    def test_renderer_has_fixed_ah_structure(self):
        normalized = json.loads(SAMPLE.read_text(encoding="utf-8"))
        context = {
            "context_hash": "a" * 64,
            "match_identity": {
                "competition_id": "TEST", "competition_name_zh": "测试联赛",
                "season": "2026", "home_team": "A", "away_team": "B",
                "kickoff_utc": "2026-08-02T15:30:00+00:00",
                "freeze_time_utc": "2026-07-31T00:00:00+00:00",
                "venue": "demo", "neutral_venue": False, "two_legged": False,
            },
            "module_states": {
                "competition_identity_and_time": "通过", "synchronized_market": "不可用",
                "team_dynamic_features": "通过", "lineup_and_task": "不可用",
            },
            "market_assessment": {"status": "不可用", "source_count": 0, "ev_gate": False},
            "lineup_assessment": {"status": "不可用", "evidence_status": "unavailable"},
            "team_features": {"snapshot_sha256": "b" * 64},
        }
        calculation = {
            "rule_version": "V4.6.1", "formal_status": "TEST",
            "module_states": {
                "data_freshness": "通过", "direct_total_goals": "通过",
                "conditional_goal_difference": "通过", "unified_score_matrix": "通过",
                "market_coordination": "未启用", "price_ev_no_bet": "降级",
            },
            "probabilities": {
                "one_x_two": {"home": 0.4, "draw": 0.3, "away": 0.3},
                "total_goals": {"0": 0.3, "1": 0.7, "2": 0.0, "3": 0.0, "4": 0.0, "5": 0.0, "6": 0.0, "7+": 0.0},
                "score_matrix": [],
            },
            "model_audit": {
                "history_matches": 20, "latest_history_match_date": "2026-07-20",
                "team_sample": {"ess": 10, "home_raw_matches": 5, "away_raw_matches": 5},
                "top_scores": [
                    {"score": "1-0", "probability": 0.4},
                    {"score": "0-0", "probability": 0.3},
                    {"score": "0-1", "probability": 0.3},
                ],
                "audit": {"probability_sum": 1.0, "engine_sha256": "c" * 64, "tail_aggregation_probability": 0.0},
            },
            "data_freshness_audit": {"source_name": "test", "engine_history_matches": 20, "engine_latest_history_match_date": "2026-07-20"},
            "conclusions": {"confidence_grade": "D", "exact_gate": False},
        }
        report = daily.render_report(normalized, context, calculation, {"status": "通过", "errors": [], "warnings": []}, exact_head="d" * 40)
        for section in ("A. 比赛与冻结时点", "B. 模块状态表", "C. 数据及市场输入", "D. 计算与审计", "E. 反证及失效条件", "F. 三块正式结论", "G. EV或No Bet", "H. 最终一句话"):
            self.assertIn(section, report)
        self.assertEqual(len(report["B. 模块状态表"]), 9)
        self.assertEqual(report["H. 最终一句话"], "弃权；可信等级D；价格不可用。")

    def test_end_to_end_existing_formal_engine(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            result = subprocess.run(
                [sys.executable, str(ENGINE_DIR / "daily_single_match_v502.py"),
                 "--input", str(SAMPLE), "--output-dir", str(output), "--print-summary"],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stdout + "\n" + result.stderr)
            receipt = json.loads((output / "receipt.json").read_text(encoding="utf-8"))
            report = json.loads((output / "report.json").read_text(encoding="utf-8"))
            calculation = json.loads((output / "calculation.json").read_text(encoding="utf-8"))
            validation = json.loads((output / "validation.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["status"], "PASS")
            self.assertFalse(receipt["provider_network_used"])
            self.assertEqual(receipt["external_request_attempts"], 0)
            self.assertEqual(validation["status"], "通过")
            self.assertAlmostEqual(sum(calculation["probabilities"]["one_x_two"].values()), 1.0, places=8)
            self.assertAlmostEqual(sum(calculation["probabilities"]["total_goals"].values()), 1.0, places=8)
            self.assertEqual(report["H. 最终一句话"].split("；")[0], "弃权")
            self.assertTrue((output / "report.md").is_file())


if __name__ == "__main__":
    unittest.main()
