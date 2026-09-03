#!/usr/bin/env python3
import importlib.util,math,pathlib,unittest
P=pathlib.Path(__file__).with_name('v3_score_shape_low_score_role_association.py')
spec=importlib.util.spec_from_file_location('lowassoc',P);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class T(unittest.TestCase):
    def test_smoothed_or(self):
        c={(0,0):3,(1,0):1,(0,1):2,(1,1):4};x=m.smoothed_log_or(c,.5);self.assertAlmostEqual(math.exp(x),(3.5*4.5)/(1.5*2.5),12)
    def test_tilt_hits_reference_or(self):
        b=[[.1,.08,.02],[.12,.14,.05],[.03,.04,.42]];s=sum(sum(r) for r in b);b=[[x/s for x in r] for r in b];goal=math.log(2.4);q,z=m.local_association_reference(b,goal);self.assertAlmostEqual(m.matrix_log_or(q),goal,12)
    def test_tilt_leaves_other_relative_shape(self):
        b=[[.10,.08,.02],[.12,.14,.05],[.03,.04,.42]];s=sum(sum(r) for r in b);b=[[x/s for x in r] for r in b];q,z=m.local_association_reference(b,math.log(2));self.assertAlmostEqual(q[2][2]/q[2][1],b[2][2]/b[2][1],12)
    def test_identity_when_or_same(self):
        b=[[.10,.08,.02],[.12,.14,.05],[.03,.04,.42]];s=sum(sum(r) for r in b);b=[[x/s for x in r] for r in b];q,z=m.local_association_reference(b,m.matrix_log_or(b));self.assertLess(max(abs(q[i][j]-b[i][j]) for i in range(3) for j in range(3)),1e-14)
    def test_prefix_strict_cutoff(self):
        p=m.LowCellPrefix([('2019-01-01 10:00:00',(0,0)),('2019-01-02 10:00:00',(1,1))]);s=p.summary('2019-01-02 10:00:00');self.assertEqual(s['low_n'],1);self.assertEqual(s['last_source_kickoff'],'2019-01-01 10:00:00')
    def test_fallback_to_league(self):
        idx={'home_team':{},'away_team':{},'league':{'EPL':m.LowCellPrefix([('1',(0,0)),('2',(1,1))])},'big5':m.LowCellPrefix([('1',(1,0))])};z=m.resolve_source(idx,'home_team',99,'EPL','9',.5);self.assertEqual(z['level'],'league_pool')
    def test_iprojection_constraints(self):
        b=[[.10,.08,.02],[.12,.14,.05],[.03,.04,.42]];s=sum(sum(r) for r in b);b=[[x/s for x in r] for r in b];ref,z=m.local_association_reference(b,math.log(2.0));tot=m.marginal_total(b);hda=m.integrate(b);target=[hda[0]+.005,hda[1],hda[2]-.005];q,rec=m.iproject(ref,tot,target);self.assertLess(max(abs(a-c) for a,c in zip(m.marginal_total(q),tot)),1e-12);self.assertLess(max(abs(a-c) for a,c in zip(m.integrate(q),target)),1e-12)
if __name__=='__main__':unittest.main()
