#!/usr/bin/env python3
import importlib.util, math, pathlib, unittest
P=pathlib.Path(__file__).with_name('v3_score_shape_goaldiff_tilt.py');spec=importlib.util.spec_from_file_location('gdshape',P);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def matrix5():
    raw=[[float((i+1)*(2*j+1)+1) for j in range(5)] for i in range(5)];s=sum(sum(r) for r in raw);return [[x/s for x in r] for r in raw]

class TestGoalDiffTilt(unittest.TestCase):
    def setUp(self):self.b=matrix5();self.bp=m.integrate(self.b)
    def test_identity_exact(self):
        q,r=m.goaldiff_tilt(self.b,self.bp);self.assertTrue(r['exact_identity']);self.assertEqual(q,self.b)
    def test_reaches_target_and_preserves_draw(self):
        t=[self.bp[0]+0.01,self.bp[1],self.bp[2]-0.01];q,r=m.goaldiff_tilt(self.b,t);ip=m.integrate(q);self.assertLess(max(abs(ip[i]-t[i]) for i in range(3)),2e-12);self.assertGreater(r['lambda'],0);self.assertAlmostEqual(ip[1],self.bp[1],places=12)
    def test_total_marginal_and_diagonal_fixed(self):
        t=[self.bp[0]-0.01,self.bp[1],self.bp[2]+0.01];q,_=m.goaldiff_tilt(self.b,t);self.assertLess(max(abs(x-y) for x,y in zip(m.marginal_total(q),m.marginal_total(self.b))),1e-12);self.assertLess(max(abs(q[i][i]-self.b[i][i]) for i in range(5)),1e-12)
    def test_conditional_tilt_identity(self):
        t=[self.bp[0]+0.015,self.bp[1],self.bp[2]-0.015];q,r=m.goaldiff_tilt(self.b,t);self.assertLess(m.tilt_identity_error(q,self.b,r['lambda']),1e-11)
    def test_valid_probabilities(self):
        t=[self.bp[0]+0.02,self.bp[1],self.bp[2]-0.02];q,_=m.goaldiff_tilt(self.b,t);self.assertAlmostEqual(sum(sum(r) for r in q),1.0,places=12);self.assertTrue(all(math.isfinite(x) and x>=0 for r in q for x in r))
    def test_draw_mismatch_fails(self):
        t=[self.bp[0]-0.001,self.bp[1]+0.002,self.bp[2]-0.001]
        with self.assertRaises(m.ScoreShapeError):m.goaldiff_tilt(self.b,t)
    def test_invalid_matrix_fails(self):
        with self.assertRaises(m.ScoreShapeError):m.goaldiff_tilt([[0.5,0.5],[0.0]],[0.5,0.0,0.5])
if __name__=='__main__':unittest.main()
