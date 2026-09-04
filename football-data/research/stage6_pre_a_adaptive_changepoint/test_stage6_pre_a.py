from __future__ import annotations
import math, pathlib, unittest
import run_stage6_pre_a as run_a

class Stage6PreATests(unittest.TestCase):
    def test_profile_frozen(self):
        self.assertEqual(run_a.PROFILE['half_life_days'],160.0)
        self.assertEqual(run_a.PROFILE['process_noise_per_day'],0.0012)
        self.assertEqual(run_a.PROFILE['prior_variance'],0.30)
    def test_moment_between_components(self):
        m,v=run_a.moment(-1.0,.2,1.0,.3,.25)
        self.assertAlmostEqual(m,-.5)
        self.assertGreater(v,0)
    def test_poisson_nll_finite(self):
        self.assertTrue(math.isfinite(run_a.pois_nll(2,1.7)))
    def test_hazard_zero_matches_continuation_update(self):
        from datetime import datetime, timezone
        a={}; b={}; now=datetime(2022,1,1,tzinfo=timezone.utc)
        run_a.update_cont(a,'h','a',now,2,1,1.5,1.2)
        wh,wa=run_a.update_adaptive(b,'h','a',now,2,1,1.5,1.2,0.0)
        self.assertAlmostEqual(wh,0.0); self.assertAlmostEqual(wa,0.0)
        for team in ('h','a'):
            self.assertAlmostEqual(a[team].attack_mean,b[team].attack_mean,places=12)
            self.assertAlmostEqual(a[team].defence_mean,b[team].defence_mean,places=12)
    def test_fixed_hazard_literal(self):
        source=pathlib.Path(run_a.__file__).read_text()
        self.assertNotIn('for hazard',source)
        self.assertNotIn('HAZARD_GRID',source)

if __name__=='__main__': unittest.main()
