import importlib.util, pathlib, sys, unittest, math
P=pathlib.Path(__file__).with_name('historical_fusion_v3_score_shape.py')
s=importlib.util.spec_from_file_location('ss_tested',str(P)); ss=importlib.util.module_from_spec(s); sys.modules['ss_tested']=ss; s.loader.exec_module(ss)

class ScoreShapeTests(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual([ss.goal_bucket(x) for x in (0,1,2,3,9)],[0,1,2,3,3])
        self.assertEqual([ss.total_bucket(x) for x in (0,1,2,3,4,5,9)],[0,0,1,2,3,4,4])

    def test_margin(self):
        self.assertEqual([ss.margin_bucket(x) for x in (-5,-1,0,1,8)],[0,1,2,3,4])

    def test_tilt_identity(self):
        b=[[1/225 for _ in range(15)] for _ in range(15)]
        z={'scored':[.1,-.1,0,0],'conceded':[0,.05,-.05,0],'total':[.02,-.02,0,0,0],'fts':.03,'cs':-.01,'margin':[.01,.02,-.03,0,0]}
        c=ss.tilt_matrix(b,z,z,.5)
        self.assertLessEqual(max(abs(a-bb) for a,bb in zip(ss.integrate(b),ss.integrate(c))),1e-12)
        self.assertLessEqual(abs(sum(map(sum,c))-1),1e-12)

    def test_residual_sums(self):
        b=[[1/225 for _ in range(15)] for _ in range(15)]
        r=ss.residual_record(b,2,1,True)
        self.assertAlmostEqual(sum(r['scored']),0,12)
        self.assertAlmostEqual(sum(r['conceded']),0,12)
        self.assertAlmostEqual(sum(r['total']),0,12)
        self.assertAlmostEqual(sum(r['margin']),0,12)

    def test_total_probs(self):
        b=[[1/225 for _ in range(15)] for _ in range(15)]
        self.assertAlmostEqual(sum(ss.total_probs(b)),1,12)

    def test_cell_score_finite(self):
        z={'scored':[0]*4,'conceded':[0]*4,'total':[0]*5,'fts':0,'cs':0,'margin':[0]*5}
        self.assertEqual(ss.cell_raw_score(0,0,z,z),0)
        self.assertTrue(math.isfinite(ss.cell_raw_score(4,2,z,z)))

    def test_grid_n(self):
        fr={'grid_and_selection':{'grid_cartesian':{'shrinkage':[.25,.5,.75],'lookback_seasons':[1,2,4],'min_effective_matches':[8,16]}}}
        self.assertEqual(len(ss.grid_params(fr)),18)

    def test_avg(self):
        r={'scored':[1,0,0,0],'conceded':[0,1,0,0],'total':[0,0,1,0,0],'fts':1,'cs':0,'margin':[0,0,1,0,0]}
        a=ss.avg_records([r,r]); self.assertEqual(a,r)

if __name__=='__main__': unittest.main()
