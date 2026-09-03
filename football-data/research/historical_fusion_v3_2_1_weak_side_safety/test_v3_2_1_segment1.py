import importlib.util,sys,unittest
SPEC=importlib.util.spec_from_file_location('v321','v3_2_1_segment1.py')
m=importlib.util.module_from_spec(SPEC);sys.modules['v321']=m;SPEC.loader.exec_module(m)
class Core:
 @staticmethod
 def fragility_weight(g,sd):return 0.8
class Model:
 def __init__(self,r):self.r=r
 def residual(self,x):return self.r
R={'fusion':{'mean_home':1.5,'mean_away':.8}}
S={'valid':True,'gap':1.0,'gap_sd':.5,'home_attack':1.4,'away_attack':.8,'home_defence':.9,'away_defence':1.2,'home_process':.12,'away_process':.08,'home_advantage':.2,'uncertainty':.4}
class T(unittest.TestCase):
 def test_A_preserves_home_weak(self):
  b=[.15,.25,.60];q,d=m.predict_A(b,R,S,Core,Model(1),.35,.04);self.assertAlmostEqual(q[0],b[0],12)
 def test_A_preserves_away_weak(self):
  b=[.60,.25,.15];q,d=m.predict_A(b,R,S,Core,Model(1),.35,.04);self.assertAlmostEqual(q[2],b[2],12)
 def test_A_only_positive_draw_shift(self):
  b=[.60,.25,.15];q,d=m.predict_A(b,R,S,Core,Model(-10),.35,.04);self.assertAlmostEqual(q[1],b[1],12)
 def test_A_cap(self):
  b=[.60,.25,.15];q,d=m.predict_A(b,R,S,Core,Model(10),.35,.02);self.assertLessEqual(q[1]-b[1],.020000000001)
 def test_B_weak_never_decreases(self):
  b=[.60,.25,.15];q,d=m.predict_B(b,R,S,Core,Model(1),.35,.04,Model(1),.5,.02);self.assertGreaterEqual(q[2],b[2])
 def test_B_negative_signal_no_uplift(self):
  b=[.60,.25,.15];q,d=m.predict_B(b,R,S,Core,Model(1),.35,.04,Model(-1),.5,.02);self.assertAlmostEqual(q[2],b[2],12)
 def test_B_preserves_A_draw(self):
  b=[.60,.25,.15];a,_=m.predict_A(b,R,S,Core,Model(1),.35,.04);q,_=m.predict_B(b,R,S,Core,Model(1),.35,.04,Model(1),.5,.02);self.assertAlmostEqual(q[1],a[1],12)
 def test_simplex(self):
  for b in ([.60,.25,.15],[.15,.25,.60]):
   q,_=m.predict_B(b,R,S,Core,Model(1),.35,.04,Model(1),.5,.02);self.assertAlmostEqual(sum(q),1.0,12);self.assertTrue(all(x>0 for x in q))
 def test_fallback_identity(self):
  s=dict(S);s['valid']=False;b=[.60,.25,.15];q,_=m.predict_A(b,R,s,Core,Model(1),.35,.04);self.assertEqual(q,b)
if __name__=='__main__':unittest.main()
