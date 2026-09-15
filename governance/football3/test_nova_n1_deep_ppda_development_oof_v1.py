from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

import nova_n1_deep_ppda_development_oof_v1 as m


class DevelopmentOOFTests(unittest.TestCase):
    def test_read_jsonl_prefix_does_not_touch_isolated_sentinel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "labels.jsonl"
            path.write_text('{"fixture_id":"a","outcome":"home"}\n{"fixture_id":"b","outcome":"draw"}\nTHIS_IS_ISOLATED_AND_NOT_JSON\n', encoding="utf-8")
            rows = m.read_jsonl_prefix(path, 2)
            self.assertEqual([row["fixture_id"] for row in rows], ["a", "b"])

    def test_same_kickoff_rows_are_atomic_and_current_match_is_not_used(self) -> None:
        rows = [
            {"fixture_id":"m1","kickoff":"2024-01-01T12:00:00Z","release_at":"2024-01-01T15:00:00Z","home_team_id":"A","away_team_id":"B","home_ppda":2.0,"away_ppda":4.0,"home_deep":8.0,"away_deep":6.0},
            {"fixture_id":"m2","kickoff":"2024-01-01T12:00:00Z","release_at":"2024-01-01T15:00:00Z","home_team_id":"A","away_team_id":"C","home_ppda":20.0,"away_ppda":40.0,"home_deep":80.0,"away_deep":60.0},
            {"fixture_id":"m3","kickoff":"2024-01-02T12:00:00Z","release_at":"2024-01-02T15:00:00Z","home_team_id":"A","away_team_id":"B","home_ppda":3.0,"away_ppda":5.0,"home_deep":9.0,"away_deep":7.0},
        ]
        features = m.build_raw_features(rows, "R1_W5")
        self.assertIsNone(features[0][0])
        self.assertIsNone(features[1][0])
        self.assertAlmostEqual(features[2][0], 11.0)
        self.assertAlmostEqual(features[2][2], 44.0)
        self.assertAlmostEqual(features[2][4], math.log1p(2))

    def test_window_state_count_indicator_uses_total_prior_history(self) -> None:
        history = [(float(i), float(i * 2)) for i in range(1, 8)]
        ppda, deep, count = m.team_state(history, ("w", 5))
        self.assertAlmostEqual(ppda, 5.0)
        self.assertAlmostEqual(deep, 10.0)
        self.assertEqual(count, 7)

    def test_rps_is_normalized_for_three_classes(self) -> None:
        result = m.metrics([[1.0, 0.0, 0.0]], [2])
        self.assertAlmostEqual(result["rps"], 1.0)
        self.assertAlmostEqual(result["brier"], 2.0)

    def test_route_selection_qualification_contract(self) -> None:
        prereg = {"development_protocol":{"qualification":{"logloss_gain_gt":0.0,"candidate_minus_formal_brier_lte":0.0005,"candidate_minus_formal_rps_lte":0.0005,"candidate_minus_formal_ece_lte":0.005}}}
        formal = {"logloss":1.0,"brier":0.60,"rps":0.20,"ece":0.03}
        good = {"logloss":0.999,"brier":0.6004,"rps":0.2004,"ece":0.034}
        bad = {"logloss":1.001,"brier":0.59,"rps":0.19,"ece":0.01}
        self.assertTrue(m.qualify(prereg, formal, good))
        self.assertFalse(m.qualify(prereg, formal, bad))

    def test_softmax_offset_zero_beta_reproduces_formal(self) -> None:
        base = [0.52, 0.27, 0.21]
        beta = [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
        got = m.softmax_offset(base, [2.0, -1.0], beta)
        for a, b in zip(base, got):
            self.assertAlmostEqual(a, b, places=12)


if __name__ == "__main__":
    unittest.main()
