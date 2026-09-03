#!/usr/bin/env python3
import importlib.util, math, pathlib, unittest
P=pathlib.Path(__file__).with_name('v3_score_shape_mirror_transport.py')
spec=importlib.util.spec_from_file_location('shape',str(P)); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

def base_matrix(n=5):
    z=[]
    for h in range(n):
        row=[]
        for a in range(n):
            row.append(math.exp(-0.75*(h+a+1))*(1.0+0.08*h+0.03*a))
        z.append(row)
    s=sum(sum(r) for r in z)
    return [[x/s for x in r] for r in z]

class TestMirrorTransport(unittest.TestCase):
    def setUp(self):
        self.b=base_matrix()
        self.bp=m.integrate(self.b)
        self.t=[self.bp[0]+0.015,self.bp[1],self.bp[2]-0.015]

    def test_01_target_hda(self):
        q,r=m.mirror_transport(self.b,self.t)
        self.assertLess(max(abs(x-y) for x,y in zip(m.integrate(q),self.t)),2e-12)
        self.assertGreater(r['lambda'],0)

    def test_02_diagonal_exact(self):
        q,_=m.mirror_transport(self.b,self.t)
        self.assertEqual([q[i][i] for i in range(len(q))],[self.b[i][i] for i in range(len(q))])

    def test_03_mirror_pair_sum_exact(self):
        q,_=m.mirror_transport(self.b,self.t)
        err=max(abs((q[h][a]+q[a][h])-(self.b[h][a]+self.b[a][h])) for h in range(len(q)) for a in range(h))
        self.assertLess(err,1e-15)

    def test_04_total_marginal_exact(self):
        q,_=m.mirror_transport(self.b,self.t)
        self.assertLess(max(abs(x-y) for x,y in zip(m.marginal_total(q),m.marginal_total(self.b))),1e-15)

    def test_05_absdiff_marginal_exact(self):
        q,_=m.mirror_transport(self.b,self.t)
        self.assertLess(max(abs(x-y) for x,y in zip(m.marginal_absdiff(q),m.marginal_absdiff(self.b))),1e-15)

    def test_06_identity_is_exact(self):
        q,r=m.mirror_transport(self.b,self.bp)
        self.assertEqual(q,self.b)
        self.assertTrue(r['exact_identity'])

    def test_07_draw_mismatch_fails_closed(self):
        bad=[self.bp[0]-0.01,self.bp[1]+0.01,self.bp[2]]
        with self.assertRaises(m.ScoreShapeError):
            m.mirror_transport(self.b,bad)

if __name__=='__main__': unittest.main()
