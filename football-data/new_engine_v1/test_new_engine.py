from __future__ import annotations

import inspect
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import market_assist
import pure_engine
from pure_engine import EngineError, EngineState, Fixture, Parameters


class PureEngineTests(unittest.TestCase):
    def setUp(self):
        self.t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def test_zero_sample_is_valid_and_uncertain(self):
        e = EngineState()
        p = e.predict(Fixture('f0', 'L0', '2026', self.t0, 'A', 'B'))
        self.assertAlmostEqual(p['p_home'] + p['p_draw'] + p['p_away'], 1.0, places=10)
        self.assertGreaterEqual(p['uncertainty'], 0.5)
        self.assertEqual(p['cold_start_bucket'], 'zero')
        self.assertGreater(len(p['score_matrix']), 100)

    def test_same_cutoff_batch_is_atomic(self):
        e = EngineState()
        f1 = Fixture('f1', 'L', '2026', self.t0, 'A', 'B')
        f2 = Fixture('f2', 'L', '2026', self.t0, 'C', 'D')
        p2_before = e.predict(f2)
        _ = e.predict(f1)
        p2_again = e.predict(f2)
        self.assertAlmostEqual(p2_before['p_home'], p2_again['p_home'], places=14)
        e.apply_batch([f1, f2], {'f1': (4, 0), 'f2': (0, 1)})
        p_after = e.predict(Fixture('f3', 'L', '2026', self.t0 + timedelta(days=7), 'A', 'B'))
        self.assertNotAlmostEqual(p_after['p_home'], p2_before['p_home'], places=5)

    def test_fail_closed_identity_time_and_labels(self):
        e = EngineState()
        with self.assertRaises(EngineError):
            e.predict(Fixture('', 'L', '2026', self.t0, 'A', 'B'))
        f = Fixture('ok', 'L', '2026', self.t0, 'A', 'B')
        e.apply_batch([f], {'ok': (1, 0)})
        with self.assertRaises(EngineError):
            e.predict(Fixture('past', 'L', '2026', self.t0 - timedelta(days=1), 'A', 'B'))
        with self.assertRaises(EngineError):
            e.apply_batch([Fixture('bad', 'L', '2026', self.t0 + timedelta(days=1), 'A', 'B')], {'bad': (-1, 0)})

    def test_cross_season_shrink(self):
        e = EngineState(params=Parameters(cross_season_shrink=0.5))
        f = Fixture('s1', 'L', '2025/26', self.t0, 'A', 'B')
        e.apply_batch([f], {'s1': (2, 0)})
        p_same = e.predict(Fixture('s2', 'L', '2025/26', self.t0 + timedelta(days=1), 'A', 'B'))
        p_new = e.predict(Fixture('s3', 'L', '2026/27', self.t0 + timedelta(days=1), 'A', 'B'))
        self.assertLess(p_new['effective_home_history'], p_same['effective_home_history'])

    def test_pure_source_is_independent(self):
        source = Path(inspect.getsourcefile(pure_engine)).read_text(encoding='utf-8').casefold()
        for token in ('bayesian_dynamic_state_oof_v500', 'v500_baseline_adapter', 'legacy_r43_registry', 'odds', 'market'):
            self.assertNotIn(token, source)


class AssistedLaneTests(unittest.TestCase):
    def test_verified_prematch_required(self):
        t = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
        pure = {'fixture_id': 'x', 'p_home': .45, 'p_draw': .30, 'p_away': .25}
        out = market_assist.assist(pure, (2.0, 3.4, 4.0), t - timedelta(minutes=60), t)
        self.assertAlmostEqual(out['p_home'] + out['p_draw'] + out['p_away'], 1.0, places=12)
        with self.assertRaises(market_assist.MarketAssistError):
            market_assist.assist(pure, (2.0, 3.4, 4.0), t, t)


if __name__ == '__main__':
    unittest.main()
