from __future__ import annotations
import pathlib, unittest
import run_stage6_pre_b as run_b

class Stage6PreBTests(unittest.TestCase):
    def test_state_first_update(self):
        s=run_b.State(); s.update(2.0,3.0,.2)
        self.assertEqual(s.n,1); self.assertAlmostEqual(s.deep,2.0); self.assertAlmostEqual(s.press,3.0)
    def test_state_ewma(self):
        s=run_b.State(); s.update(2.0,3.0,.25); s.update(6.0,7.0,.25)
        self.assertAlmostEqual(s.deep,3.0); self.assertAlmostEqual(s.press,4.0); self.assertEqual(s.n,2)
    def test_predict_fallback_exact(self):
        base=[.4,.3,.3]; p,on=run_b.predict(base,{'active':False},.10)
        self.assertEqual(p,base); self.assertFalse(on)
    def test_positive_edge_preserves_draw_and_raises_home(self):
        base=[.4,.3,.3]; p,on=run_b.predict(base,{'active':True,'edge':2.0},.10)
        self.assertTrue(on); self.assertAlmostEqual(p[1],base[1],places=14); self.assertGreater(p[0],base[0]); self.assertLess(p[2],base[2]); self.assertAlmostEqual(sum(p),1.0,places=14)
    def test_no_grid_or_fit(self):
        source=pathlib.Path(run_b.__file__).read_text()
        self.assertNotIn('HALF_LIFE_GRID',source); self.assertNotIn('L2_GRID',source); self.assertNotIn('_fit_',source)

if __name__=='__main__': unittest.main()
