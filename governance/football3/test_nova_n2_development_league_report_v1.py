import importlib.util
import pathlib
import unittest

HERE = pathlib.Path(__file__).resolve().parent
TARGET = HERE / "nova_n2_development_league_report_v1.py"
spec = importlib.util.spec_from_file_location("n2report", TARGET)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


class LeagueReportTests(unittest.TestCase):
    def test_report_groups_are_big5_plus_j1_k1(self):
        self.assertEqual(
            mod.ALL_REPORT_GROUPS,
            ["EPL", "Bundesliga", "La_liga", "Ligue_1", "Serie_A", "J1", "K1"],
        )

    def test_unavailable_group_is_zero_weight(self):
        row = mod.unavailable_group()
        self.assertEqual(row["status"], "NOT_AVAILABLE")
        self.assertEqual(row["n"], 0)
        self.assertEqual(row["coverage"], 0.0)
        self.assertEqual(row["weight"], 0)
        self.assertEqual(row["matrix_delta"], 0)

    def test_metric_compare_detects_drift(self):
        metric = {"n": 2, "logloss": 1.0, "brier": 0.6, "rps": 0.2, "top1": 0.5, "ece": 0.1}
        mod.compare_metrics(metric, dict(metric), "SAME")
        changed = dict(metric)
        changed["logloss"] = 1.01
        with self.assertRaises(mod.ReportError):
            mod.compare_metrics(metric, changed, "DRIFT")


if __name__ == "__main__":
    unittest.main()
