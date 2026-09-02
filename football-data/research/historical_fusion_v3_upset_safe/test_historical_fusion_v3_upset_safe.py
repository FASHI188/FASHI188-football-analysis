import importlib.util, pathlib, sys, unittest
P=pathlib.Path(__file__).with_name('historical_fusion_v3_upset_safe.py')
spec=importlib.util.spec_from_file_location('usr',P); usr=importlib.util.module_from_spec(spec); sys.modules['usr']=usr; spec.loader.exec_module(usr)

def row(i=0,ph=.55,pd=.27,pa=.18,y=0,fallback=False,cold='established'):
    hg,ag=(2,0) if y==0 else (1,1) if y==1 else (0,2)
    return {'fixture_id':f'f{i}','fusion':{'p_home':ph,'p_draw':pd,'p_away':pa,'mean_home':1.5,'mean_away':1.0},'home_goals':hg,'away_goals':ag,'fallback_exact_v1':fallback,'cold_start_bucket':cold,'league':'EPL','season':2020}

def prof(k=0.0):
    return {'npxg_for':1.3+k,'npxg_against':1.2-k,'npshots_for':12+k,'npshots_against':11-k,'npxg_per_shot':.105+k*.001,'open_share':.72+k*.001,'set_share':.28-k*.001}

def proc_for(rows):
    return {r['fixture_id']:{'valid':True,'home':prof((j%7)*.03),'away':prof(-((j%5)*.02)),'home_weight':10,'away_weight':10} for j,r in enumerate(rows)}

class StubModel:
    def __init__(self,beta=None): self.beta=beta or [0.15]*12; self.cols=list(range(12)); self.means=[0.0]*14; self.sds=[1.0]*14
    def transform(self,v): return [v[j] for j in self.cols]

class Tests(unittest.TestCase):
    def test_01_home_weak_assignment(self):
        r=row(ph=.2,pd=.3,pa=.5); o=usr.oriented_feature_vector(r,proc_for([r])); self.assertEqual(o[:2],(2,0))
    def test_02_away_weak_assignment(self):
        r=row(ph=.55,pa=.18); o=usr.oriented_feature_vector(r,proc_for([r])); self.assertEqual(o[:2],(0,2))
    def test_03_home_weak_probability_invariant(self):
        r=row(ph=.2,pd=.3,pa=.5); p=usr.predict(StubModel(),r,proc_for([r])); self.assertEqual(p[0],.2)
    def test_04_away_weak_probability_invariant(self):
        r=row(ph=.55,pd=.27,pa=.18); p=usr.predict(StubModel(),r,proc_for([r])); self.assertEqual(p[2],.18)
    def test_05_fallback_exact(self):
        r=row(fallback=True); b=usr.pvec(r); self.assertEqual(usr.canon(b),usr.canon(usr.predict(StubModel(),r,proc_for([r]))))
    def test_06_tie_fails_closed(self):
        r=row(ph=.36,pd=.28,pa=.36); b=usr.pvec(r); self.assertEqual(usr.canon(b),usr.canon(usr.predict(StubModel(),r,proc_for([r]))))
    def test_07_missing_process_fails_closed(self):
        r=row(); b=usr.pvec(r); self.assertEqual(usr.canon(b),usr.canon(usr.predict(StubModel(),r,{})))
    def test_08_invalid_baseline_fails_closed(self):
        r=row(ph=.8,pd=.3,pa=.1); b=usr.pvec(r); self.assertEqual(usr.canon(b),usr.canon(usr.predict(StubModel(),r,proc_for([r]))))
    def test_09_fit_excludes_realized_weak_outcomes_from_trainable_factor(self):
        rows=[]
        for i in range(700):
            y=2 if i<100 else (0 if i%2 else 1)
            ph=.50+(i%17)*.004; pa=.20+(i%11)*.002; pd=1-ph-pa
            rows.append(row(i,ph,pd,pa,y))
        model,meta=usr.fit(rows,proc_for(rows)); self.assertEqual(meta['fit_n'],600); self.assertEqual(meta['lambda'],4.0*meta['feature_dim_active'])
    def test_10_weak_conditional_logloss_is_exactly_preserved(self):
        rows=[row(i,.55,.27,.18,2) for i in range(30)]+[row(100+i,.2,.3,.5,0) for i in range(30)]
        proc=proc_for(rows); m=StubModel(); c,n=usr.cond_ll(rows,lambda r:usr.predict(m,r,proc),'weak'); b,_=usr.cond_ll(rows,lambda r:usr.pvec(r),'weak'); self.assertEqual(c,b); self.assertEqual(n,60)
    def test_11_probability_simplex(self):
        rows=[row(i,.42+(i%5)*.02,.31-(i%3)*.01,.27-(i%5)*.02+(i%3)*.01,0) for i in range(20)]
        proc=proc_for(rows); m=StubModel()
        for r in rows:
            p=usr.predict(m,r,proc); self.assertTrue(all(0<x<1 for x in p)); self.assertAlmostEqual(sum(p),1.0,12)

if __name__=='__main__': unittest.main(verbosity=2)
