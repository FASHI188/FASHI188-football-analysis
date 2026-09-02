from __future__ import annotations
import importlib.util, math, os, pathlib, sys, unittest
from datetime import datetime, timedelta, timezone

HERE=pathlib.Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('hxg',HERE/'historical_xg_challenger.py'); hxg=importlib.util.module_from_spec(spec); sys.modules[spec.name]=hxg; spec.loader.exec_module(hxg)
V1_PATH=pathlib.Path(os.environ.get('V1_ENGINE_PATH',HERE.parent/'new_engine_v1'/'pure_engine.py'))
spec2=importlib.util.spec_from_file_location('v1xg',V1_PATH); v1=importlib.util.module_from_spec(spec2); sys.modules[spec2.name]=v1; spec2.loader.exec_module(v1)
P=hxg.XGParams(180.0,8.0,0.10,0.70)
V1P=dict(hxg.EXPECTED_V1_PARAMS)
T0=datetime(2022,8,10,15,tzinfo=timezone.utc)

def fx(fid='f1',h='A',a='B',t=T0,s='2022',c='1'):
    return hxg.FixtureRow(fid,c,s,t,h,a,h,a)

def state(): return hxg.ChallengerState(v1,V1P,P)

def seed_component(st,team,venue,comp,value=4.0,weight=8.0):
    m=st.venue_attack if comp=='attack' else st.venue_defence
    pm=st.pooled_attack if comp=='attack' else st.pooled_defence
    when=T0-timedelta(days=5)
    m[(team,venue)]=hxg.ResidualState(value,weight,when,'2022'); pm[team]=hxg.ResidualState(value,weight,when,'2022')

class Tests(unittest.TestCase):
    def test_01_grid_frozen(self): self.assertEqual(len(hxg.candidate_grid()),54)
    def test_02_no_information_exact_fallback(self):
        s=state(); f=fx(); x,b=s.predict_batch([f],include_matrix=True); x=x[0]; b=b[0]
        self.assertTrue(x['dynamic']['fallback_exact_v1'])
        for k in ('mu_home','mu_away','p_home','p_draw','p_away'): self.assertEqual(x[k],b[k])
        self.assertEqual(x['score_matrix'],b['score_matrix'])
    def test_03_attack_only_raises_own_mu(self):
        a,b=state(),state(); seed_component(b,'A','home','attack',4,8); seed_component(b,'B','away','defence',0,8); seed_component(b,'A','home','defence',0,8); seed_component(b,'B','away','attack',0,8)
        pa=a.predict_batch([fx('a')])[0][0]; pb=b.predict_batch([fx('b')])[0][0]
        self.assertGreater(pb['mu_home'],pa['mu_home']); self.assertEqual(pb['mu_away'],pa['mu_away'])
    def test_04_defence_positive_lowers_opponent_mu(self):
        a,b=state(),state();
        for st in (b,):
            seed_component(st,'A','home','attack',0,8); seed_component(st,'A','home','defence',0,8); seed_component(st,'B','away','attack',0,8); seed_component(st,'B','away','defence',4,8)
        pa=a.predict_batch([fx('a')])[0][0]; pb=b.predict_batch([fx('b')])[0][0]
        self.assertLess(pb['mu_home'],pa['mu_home']); self.assertEqual(pb['mu_away'],pa['mu_away'])
    def test_05_home_away_states_separate(self):
        s=state(); seed_component(s,'A','home','attack',4,8)
        h=s._view('A','home','attack','2022',T0)[0]; a=s._view('A','away','attack','2022',T0)[0]
        self.assertGreater(h,a)
    def test_06_opponent_adjusted_xg_residual(self):
        self.assertGreater(hxg.residual_signal(1.5,0.8,P),hxg.residual_signal(1.5,1.4,P))
    def test_07_decay_monotone_and_cross_season_once(self):
        r=hxg.ResidualState(4,4,T0,'2022'); a=r.snapshot(T0+timedelta(days=20),'2022',P); b=r.snapshot(T0+timedelta(days=100),'2022',P); self.assertGreater(a[1],b[1])
        x=r.snapshot(T0+timedelta(days=20),'2023',P); y=r.snapshot(T0+timedelta(days=20),'2023',P); self.assertEqual(x,y); self.assertAlmostEqual(x[0],a[0]*P.dynamic_cross_season_shrink,12)
    def test_08_same_kickoff_predict_before_update(self):
        s=state(); batch=[fx('a','A','B'),fx('b','C','D')]; s.predict_batch(batch); self.assertFalse(s.venue_attack)
        labs={f.fixture_id:hxg.ReleasedLabel(1,0,1.4,0.5,T0+timedelta(hours=3)) for f in batch}
        with self.assertRaises(hxg.XGError): s.apply_released_batch(batch,labs,T0+timedelta(hours=2,minutes=59))
        s.apply_released_batch(batch,labs,T0+timedelta(hours=3)); self.assertEqual(s.seen,{'a','b'})
    def test_09_duplicate_and_same_team_fail_closed(self):
        s=state()
        with self.assertRaises(hxg.XGError): s.predict_batch([fx('a','A','B'),fx('a','C','D')])
        with self.assertRaises(hxg.XGError): s.predict_batch([fx('a','A','B'),fx('b','A','C')])
    def test_10_identity_mutation_and_future_release_fail(self):
        s=state(); f=fx('a'); s.predict_batch([f]); lab=hxg.ReleasedLabel(1,0,1.2,0.5,T0+timedelta(hours=3))
        with self.assertRaises(hxg.XGError): s.apply_released_batch([fx('a','A','C')],{'a':lab},T0+timedelta(hours=3))
        with self.assertRaises(hxg.XGError): s.apply_released_batch([f],{'a':lab},T0+timedelta(hours=2))
    def test_11_invalid_labels_fail_closed(self):
        for lab in (hxg.ReleasedLabel(-1,0,1,1,T0+timedelta(hours=3)),hxg.ReleasedLabel(1,0,-0.1,1,T0+timedelta(hours=3))):
            s=state(); f=fx('a'); s.predict_batch([f]);
            with self.assertRaises(hxg.XGError): s.apply_released_batch([f],{'a':lab},T0+timedelta(hours=3))
    def test_12_fast_poisson_matches_frozen_v1_matrix(self):
        for mh,ma in ((0.7,0.9),(1.4,1.1),(2.2,1.8)):
            m=v1.score_matrix(mh,ma); h,d,a=v1.one_x_two(m); q=hxg.fast_poisson(mh,ma,v1.MAX_GOALS)
            self.assertAlmostEqual(q['p_home'],h,14); self.assertAlmostEqual(q['p_draw'],d,14); self.assertAlmostEqual(q['p_away'],a,14)
    def test_13_active_matrix_nonnegative_normalized_same_1x2(self):
        s=state();
        for team,venue,comp,val in [('A','home','attack',3),('A','home','defence',1),('B','away','attack',1),('B','away','defence',0)]: seed_component(s,team,venue,comp,val,8)
        p=s.predict_batch([fx()],include_matrix=True)[0][0]; m=p['score_matrix']; self.assertAlmostEqual(sum(c['probability'] for c in m),1.0,12); self.assertTrue(all(math.isfinite(c['probability']) and c['probability']>=0 for c in m)); h,d,a=v1.one_x_two(m); self.assertAlmostEqual(h,p['p_home'],14); self.assertAlmostEqual(d,p['p_draw'],14); self.assertAlmostEqual(a,p['p_away'],14)
    def test_14_state_digest_deterministic(self):
        a,b=state(),state(); fa=fx('a'); a.predict_batch([fa]); b.predict_batch([fa]); self.assertEqual(a.state_digest(),b.state_digest())

if __name__=='__main__': unittest.main()
