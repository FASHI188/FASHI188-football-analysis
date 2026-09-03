#!/usr/bin/env python3
import importlib.util,pathlib,unittest
P=pathlib.Path(__file__).with_name('v3_score_shape_dynamic_share_concentration.py')
spec=importlib.util.spec_from_file_location('dynshare',P);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class T(unittest.TestCase):
    def test_beta_slice_normalized(self):
        z=m.beta_binomial_slice_weights(4,0.57,5.0,15);self.assertAlmostEqual(sum(x[2] for x in z),1.0,14);self.assertTrue(all(x[2]>0 for x in z))
    def test_kappa_changes_shape(self):
        lo=m.beta_binomial_slice_weights(4,0.5,4.0,15);hi=m.beta_binomial_slice_weights(4,0.5,40.0,15);dlo={h:w for h,a,w in lo};dhi={h:w for h,a,w in hi};self.assertGreater(dlo[0]+dlo[4],dhi[0]+dhi[4])
    def test_reference_preserves_total(self):
        tt=[0.02,0.08,0.20,0.28,0.20,0.12,0.06,0.025,0.01,0.004,0.001]+[0.0]*18;s=sum(tt);tt=[x/s for x in tt];q=m.build_reference(tt,.55,5.0,15);self.assertLess(max(abs(a-b) for a,b in zip(m.marginal_total(q),tt)),1e-14)
    def test_iprojection_constraints(self):
        n=5;tt=[.10,.20,.25,.20,.12,.07,.035,.02,.005];q=m.build_reference(tt,.55,5.0,n);hda=m.integrate(q);target=[hda[0]+.01,hda[1],hda[2]-.01];z,rec=m.iproject(q,tt,target);self.assertLess(max(abs(a-b) for a,b in zip(m.marginal_total(z),tt)),1e-12);self.assertLess(max(abs(a-b) for a,b in zip(m.integrate(z),target)),1e-12)
    def test_prefix_strict_cutoff(self):
        p=m.PrefixSeries([('2019-01-01 10:00:00',.4),('2019-01-02 10:00:00',.6)]);s=p.summary('2019-01-02 10:00:00');self.assertEqual(s[0],1);self.assertEqual(s[-1],'2019-01-01 10:00:00')
    def test_kappa_moments(self):
        p=m.PrefixSeries([('1',.2),('2',.4),('3',.6),('4',.8)]);z=m.kappa_from_summary(p.summary('9'));self.assertIsNotNone(z);self.assertGreater(z['kappa'],0)
    def test_fallback_source_resolution(self):
        idx={'home_team':{},'away_team':{},'league':{'EPL':m.PrefixSeries([('1',.3),('2',.5),('3',.7)])},'big5':m.PrefixSeries([('1',.2),('2',.4),('3',.6)])};z=m.resolve_source(idx,'home_team',999,'EPL','9');self.assertEqual(z['level'],'league_pool')
if __name__=='__main__':unittest.main()
