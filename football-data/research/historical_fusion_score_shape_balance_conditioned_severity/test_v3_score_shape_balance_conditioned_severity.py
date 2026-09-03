#!/usr/bin/env python3
import importlib.util,math,pathlib,unittest
P=pathlib.Path(__file__).with_name('v3_score_shape_balance_conditioned_severity.py')
spec=importlib.util.spec_from_file_location('bs',P);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class T(unittest.TestCase):
    def test_severity_geometry(self):
        self.assertAlmostEqual(m.normalized_severity(2,1),0.0,14)
        self.assertAlmostEqual(m.normalized_severity(3,0),1.0,14)
        self.assertAlmostEqual(m.normalized_severity(4,1),0.5,14)
        self.assertAlmostEqual(m.normalized_severity(2,2),0.0,14)
    def test_strength_signal_symmetry(self):
        q=[[0.0]*3 for _ in range(3)];q[1][0]=.4;q[0][0]=.2;q[0][1]=.4
        self.assertAlmostEqual(m.strength_signal(q,'H'),-m.strength_signal(q,'A'),14)
    def test_partition_mass_and_singletons(self):
        q=[[0.0]*5 for _ in range(5)];q[2][1]=.12;q[3][0]=.08;q[1][0]=.10;q[0][1]=.10;q[1][1]=.10;q[2][2]=.10;q[0][0]=.10;q[0][2]=.10;q[2][0]=.10;q[1][2]=.05;q[2][1]+=.05
        s=sum(sum(r) for r in q);q=[[x/s for x in r] for r in q]
        z,d=m.apply_severity(q,q,0.7);pe,se,de=m.partition_errors(z,q)
        self.assertLess(pe,1e-14);self.assertLess(se,1e-14);self.assertLess(de,1e-14)
    def test_balanced_signal_is_identity(self):
        q=[[0.0]*4 for _ in range(4)];q[2][1]=.2;q[3][0]=.2;q[1][2]=.2;q[0][3]=.2;q[1][1]=.2
        z,d=m.apply_severity(q,q,3.0)
        self.assertLess(max(abs(z[h][a]-q[h][a]) for h in range(4) for a in range(4)),1e-14)
    def test_positive_signal_tilts_to_severe_win(self):
        b=[[0.0]*4 for _ in range(4)];b[2][1]=.15;b[3][0]=.15;b[1][2]=.05;b[0][3]=.05;b[1][1]=.20;b[0][0]=.40
        z,d=m.apply_severity(b,b,1.0)
        self.assertGreater(z[3][0]/z[2][1],b[3][0]/b[2][1])
    def test_gamma_score_monotone(self):
        b=[[0.0]*4 for _ in range(4)];b[2][1]=.2;b[3][0]=.2;b[1][2]=.1;b[0][3]=.1;b[1][1]=.4
        cells=[(2,1),(3,0)];obs=[{'x':1.0,'sobs':1.0,'cells':cells,'matrix':b,'observed':(3,0),'fixture_id':'x','season':2018}]
        self.assertGreater(m.gamma_score(obs,-1)[1],m.gamma_score(obs,1)[1])
    def test_fit_gamma_finite(self):
        b=[[0.0]*4 for _ in range(4)];b[2][1]=.2;b[3][0]=.2;b[1][2]=.1;b[0][3]=.1;b[1][1]=.4
        cells=[(2,1),(3,0)];obs=[]
        for i in range(20):obs.append({'x':1.0 if i<10 else -1.0,'sobs':1.0 if i<10 else 0.0,'cells':cells,'matrix':b,'observed':(3,0) if i<10 else (2,1),'fixture_id':str(i),'season':2018})
        f=m.fit_gamma(obs);self.assertTrue(math.isfinite(f['gamma']));self.assertGreaterEqual(f['loglik_at_fit']+1e-12,f['loglik_at_gamma0'])

if __name__=='__main__':unittest.main()
