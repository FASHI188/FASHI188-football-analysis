import unittest, math, importlib.util, pathlib, sys
P=pathlib.Path(__file__).with_name('v3_2_2_segment2.py')
spec=importlib.util.spec_from_file_location('seg',P); seg=importlib.util.module_from_spec(spec); sys.modules['seg']=seg; spec.loader.exec_module(seg)

class Core:
    @staticmethod
    def parity_weight(gap,sd):
        z=abs(float(gap))/max(float(sd),1e-9); return math.exp(-0.5*z*z)
class Model:
    def __init__(self,r): self.r=r
    def residual(self,x): return self.r

C={'parity_route':{'v3_2_parity_weight_min':0.5,'frozen_v3_1_1_top1_margin_max':0.08,'abs_frozen_v3_1_1_home_minus_away_max':0.16}}
R={'fusion':{'mean_home':1.35,'mean_away':1.25}}
S={'valid':True,'gap':0.0,'gap_sd':0.25,'home_attack':1.3,'away_attack':1.28,'home_defence':1.2,'away_defence':1.22,'home_process':0.11,'away_process':0.105,'home_advantage':0.12,'uncertainty':0.25}
B=[0.36,0.34,0.30]

class Tests(unittest.TestCase):
    def test_route_eligible(self):
        f=seg.route_features(B,R,S,Core,C); self.assertTrue(f['eligible']); self.assertGreater(f['soft'],0)
    def test_route_margin_exclusion(self):
        b=[0.50,0.30,0.20]; f=seg.route_features(b,R,S,Core,C); self.assertFalse(f['eligible']); self.assertEqual(f['soft'],0)
    def test_A_weak_exact_positive_draw(self):
        p,d=seg.predict_A(B,R,S,Core,C,Model(2.0),{'draw_scale':.75,'cap':.04}); self.assertAlmostEqual(p[2],B[2],14); self.assertGreaterEqual(p[1],B[1]); self.assertAlmostEqual(sum(p),1,14)
    def test_A_weak_exact_negative_draw(self):
        p,d=seg.predict_A(B,R,S,Core,C,Model(-2.0),{'draw_scale':.75,'cap':.04}); self.assertAlmostEqual(p[2],B[2],14); self.assertLessEqual(p[1],B[1]); self.assertAlmostEqual(sum(p),1,14)
    def test_A_cap(self):
        p,d=seg.predict_A(B,R,S,Core,C,Model(20.0),{'draw_scale':1.0,'cap':.02}); self.assertLessEqual(max(abs(p[i]-B[i]) for i in range(3)),.02+1e-12)
    def test_B_weak_nondecrease(self):
        p,d=seg.predict_B(B,R,S,Core,C,Model(1.0),Model(8.0),{'draw_scale':.5,'side_scale':.5,'cap':.04}); self.assertGreaterEqual(p[2],B[2]-1e-12); self.assertAlmostEqual(sum(p),1,14)
    def test_B_cap(self):
        p,d=seg.predict_B(B,R,S,Core,C,Model(8.0),Model(-8.0),{'draw_scale':.5,'side_scale':.5,'cap':.02}); self.assertLessEqual(max(abs(p[i]-B[i]) for i in range(3)),.02+1e-12)
    def test_outside_exact_identity(self):
        s=dict(S); s['gap']=1.0; s['gap_sd']=.1
        p,d=seg.predict_A(B,R,s,Core,C,Model(10),{'draw_scale':1,'cap':.04}); self.assertEqual(p,B); self.assertFalse(d['eligible'])
    def test_fav_weak_orientation(self):
        self.assertEqual(seg.fav_weak([.36,.34,.30]),(0,2)); self.assertEqual(seg.fav_weak([.28,.32,.40]),(2,0))

if __name__=='__main__':unittest.main()
