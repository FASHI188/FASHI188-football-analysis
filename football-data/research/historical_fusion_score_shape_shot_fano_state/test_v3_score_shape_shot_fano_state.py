#!/usr/bin/env python3
import importlib.util, math, pathlib, unittest
P=pathlib.Path(__file__).with_name('v3_score_shape_shot_fano_state.py')
spec=importlib.util.spec_from_file_location('shotfano',P);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class T(unittest.TestCase):
    def test_poisson_pmf(self):
        p=m.poisson_pmf(1.4,15);self.assertAlmostEqual(sum(p),1.0,12);self.assertTrue(all(x>=0 for x in p));self.assertGreater(p[1],p[4])
    def test_nb_pmf(self):
        p=m.nb_pmf(1.4,1.25,15);self.assertAlmostEqual(sum(p),1.0,12);self.assertTrue(all(math.isfinite(x) and x>=0 for x in p));self.assertGreater(p[-1],0.0)
    def test_fano_formula(self):
        z=m.fano_from_summary((3,3.0,3.5,2.1,'2018-01-01 00:00:00'));self.assertIsNotNone(z);self.assertGreater(z['fano'],0);self.assertEqual(z['n'],3)
    def test_prefix_excludes_same_kickoff(self):
        s=m.PrefixSeries([('2018-01-01 12:00:00',1.0,.8),('2018-01-02 12:00:00',2.0,1.2)]);a=s.summary('2018-01-02 12:00:00');self.assertEqual(a[0],1);self.assertEqual(a[-1],'2018-01-01 12:00:00')
    def test_iprojection_constraints(self):
        base=[[.12,.08,.04],[.07,.18,.09],[.05,.11,.26]];s=sum(map(sum,base));base=[[x/s for x in r] for r in base];ref=[[.14,.05,.03],[.09,.16,.08],[.04,.10,.31]];sr=sum(map(sum,ref));ref=[[x/sr for x in r] for r in ref];q,rec=m.iproject(ref,m.marginal_total(base),m.integrate(base));self.assertLess(rec['total_error'],1e-12);self.assertLess(rec['hda_error'],1e-12);self.assertLess(max(abs(x-y) for x,y in zip(m.marginal_total(q),m.marginal_total(base))),1e-12);self.assertLess(max(abs(x-y) for x,y in zip(m.integrate(q),m.integrate(base))),1e-12)
    def test_expected_goals(self):
        q=[[.25,.25],[.25,.25]];h,a=m.expected_goals(q);self.assertAlmostEqual(h,.5);self.assertAlmostEqual(a,.5)
    def test_total_marginal(self):
        q=[[.1,.2],[.3,.4]];self.assertEqual(m.marginal_total(q),[.1,.5,.4]);self.assertAlmostEqual(sum(m.integrate(q)),1.0)

if __name__=='__main__':unittest.main()
