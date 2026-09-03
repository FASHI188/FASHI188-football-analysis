#!/usr/bin/env python3
import importlib.util, math, pathlib, unittest
P=pathlib.Path(__file__).with_name('v3_score_shape_total_slice_iprojection.py')
spec=importlib.util.spec_from_file_location('slice_shape',P); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

def matrix5():
    raw=[[float((i+1)*(2*j+1)+1) for j in range(5)] for i in range(5)]
    s=sum(sum(r) for r in raw)
    return [[x/s for x in r] for r in raw]

class TestTotalSliceIProjection(unittest.TestCase):
    def setUp(self):
        self.b=matrix5(); self.bp=m.integrate(self.b)

    def test_identity_is_exact(self):
        q,rec=m.total_slice_transport(self.b,self.bp)
        self.assertTrue(rec['exact_identity'])
        self.assertEqual(q,self.b)

    def test_reaches_target_and_preserves_draw(self):
        t=[self.bp[0]+0.01,self.bp[1],self.bp[2]-0.01]
        q,rec=m.total_slice_transport(self.b,t)
        ip=m.integrate(q)
        self.assertLess(max(abs(ip[i]-t[i]) for i in range(3)),2e-12)
        self.assertGreater(rec['lambda'],0.0)
        self.assertAlmostEqual(ip[1],self.bp[1],places=12)

    def test_total_goal_marginal_and_diagonal_are_fixed(self):
        t=[self.bp[0]-0.01,self.bp[1],self.bp[2]+0.01]
        q,_=m.total_slice_transport(self.b,t)
        self.assertLess(max(abs(a-b) for a,b in zip(m.marginal_total(q),m.marginal_total(self.b))),1e-12)
        self.assertLess(max(abs(q[i][i]-self.b[i][i]) for i in range(5)),1e-12)

    def test_within_slice_side_shape_is_fixed(self):
        t=[self.bp[0]+0.015,self.bp[1],self.bp[2]-0.015]
        q,_=m.total_slice_transport(self.b,t)
        self.assertLess(m.within_slice_side_shape_delta(q,self.b),1e-12)

    def test_probabilities_remain_valid(self):
        t=[self.bp[0]+0.02,self.bp[1],self.bp[2]-0.02]
        q,_=m.total_slice_transport(self.b,t)
        self.assertAlmostEqual(sum(sum(r) for r in q),1.0,places=12)
        self.assertTrue(all(math.isfinite(x) and x>=0 for r in q for x in r))

    def test_target_draw_mismatch_fails_closed(self):
        t=[self.bp[0]-0.001,self.bp[1]+0.002,self.bp[2]-0.001]
        with self.assertRaises(m.ScoreShapeError): m.total_slice_transport(self.b,t)

    def test_invalid_matrix_fails_closed(self):
        with self.assertRaises(m.ScoreShapeError): m.total_slice_transport([[0.5,0.5],[0.0]], [0.5,0.0,0.5])

if __name__=='__main__': unittest.main()
