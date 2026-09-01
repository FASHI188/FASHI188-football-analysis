from __future__ import annotations

import importlib.util
import json
import math
import os
import pathlib
import sys
import unittest
from datetime import datetime, timedelta, timezone

import v1_1_dynamic_base as d

V1_PATH = pathlib.Path(os.environ.get('V1_ENGINE_PATH', pathlib.Path(__file__).parents[1] / 'new_engine_v1' / 'pure_engine.py'))
spec = importlib.util.spec_from_file_location('football3_frozen_v1_for_v11_test', V1_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError('cannot load frozen V1')
v1 = importlib.util.module_from_spec(spec); sys.modules[spec.name] = v1; spec.loader.exec_module(v1)

V1_PARAMS = {
    'half_life_days':260.0,'competition_half_life_days':720.0,'prior_matches':10.0,
    'competition_prior_matches':28.0,'global_team_prior_matches':14.0,'cross_season_shrink':0.66,
    'global_team_weight':0.35,'strength_exponent':0.82,'min_rate':0.12,'max_rate':5.5,
}
DP = d.DynamicParameters(180.0, 8.0, 0.10, 0.70)
T0 = datetime(2023,8,12,15,tzinfo=timezone.utc)

def fx(fid='f1', home='A', away='B', kickoff=T0, season='2023-24', comp='ENG1'):
    return v1.Fixture(fid, comp, season, kickoff, home, away)

def state():
    return d.DynamicEngineState(v1, V1_PARAMS, DP)

class V11Tests(unittest.TestCase):
    def test_01_no_dynamic_information_exactly_falls_back_to_v1(self):
        s=state(); f=fx(); base=v1.EngineState(v1.Parameters(**V1_PARAMS)).predict(f); p=s.predict_batch([f])[0]
        self.assertTrue(p['dynamic']['fallback_exact_v1'])
        for k in ('mu_home','mu_away','p_home','p_draw','p_away'):
            self.assertEqual(p[k],base[k])
        self.assertEqual(p['score_matrix'],base['score_matrix'])

    def test_02_home_attack_increase_can_only_raise_home_mu(self):
        a,b=state(),state(); when=T0-timedelta(days=7)
        for m in (b.venue_attack,b.pooled_attack):
            key=('A','home') if m is b.venue_attack else 'A'; m[key]=d.ResidualState(5.0,5.0,when,'2023-24')
        pa,pb=a.predict_batch([fx('a')])[0],b.predict_batch([fx('b')])[0]
        self.assertGreater(pb['mu_home'],pa['mu_home']); self.assertEqual(pb['mu_away'],pa['mu_away'])

    def test_03_positive_defence_state_only_lowers_opponent_mu(self):
        a,b=state(),state(); when=T0-timedelta(days=7)
        b.venue_defence[('B','away')]=d.ResidualState(5.0,5.0,when,'2023-24'); b.pooled_defence['B']=d.ResidualState(5.0,5.0,when,'2023-24')
        pa,pb=a.predict_batch([fx('a')])[0],b.predict_batch([fx('b')])[0]
        self.assertLess(pb['mu_home'],pa['mu_home']); self.assertEqual(pb['mu_away'],pa['mu_away'])

    def test_04_home_and_away_local_states_are_separate_with_only_shrunk_pooled_borrow(self):
        s=state(); when=T0-timedelta(days=5)
        s.venue_attack[('A','home')]=d.ResidualState(6.0,6.0,when,'2023-24'); s.pooled_attack['A']=d.ResidualState(6.0,6.0,when,'2023-24')
        home=s._view_component('A','home','attack','2023-24',T0)[0]
        away=s._view_component('A','away','attack','2023-24',T0)[0]
        self.assertGreater(home,away); self.assertGreater(away,0.0)

    def test_05_same_goals_against_stronger_expectation_produce_larger_positive_residual(self):
        self.assertGreater(d.DynamicEngineState.residual_signal(2,0.8), d.DynamicEngineState.residual_signal(2,1.5))

    def test_06_time_decay_is_monotone(self):
        r=d.ResidualState(4.0,4.0,T0,'2023-24'); a=r.snapshot(T0+timedelta(days=30), '2023-24', DP); b=r.snapshot(T0+timedelta(days=180), '2023-24', DP)
        self.assertGreater(a[1],b[1]); self.assertGreater(a[0],b[0])

    def test_07_cross_season_shrink_is_applied_once_per_snapshot_not_compounded(self):
        r=d.ResidualState(4.0,4.0,T0,'2022-23'); a=r.snapshot(T0+timedelta(days=10),'2023-24',DP); b=r.snapshot(T0+timedelta(days=10),'2023-24',DP)
        no_shrink=math.exp(-math.log(2)*10/DP.dynamic_half_life_days)*4.0
        self.assertAlmostEqual(a[0],no_shrink*DP.dynamic_cross_season_shrink,places=14); self.assertEqual(a,b)

    def test_08_same_kickoff_predict_before_update_and_result_release(self):
        s=state(); batch=[fx('a','A','B'),fx('b','C','D')]; digest=s.state_digest(); rows=s.predict_batch(batch)
        self.assertEqual(len(rows),2); self.assertNotEqual(digest,s.state_digest())
        self.assertFalse(s.venue_attack)
        labs={'a':(2,0,T0+timedelta(hours=3)),'b':(1,1,T0+timedelta(hours=3))}
        with self.assertRaises(d.DynamicBaseError): s.apply_batch(batch,labs,as_of=T0+timedelta(hours=2))
        s.apply_batch(batch,labs,as_of=T0+timedelta(hours=3)); self.assertEqual(s.seen,{'a','b'})

    def test_09_future_duplicate_identity_and_same_team_fail_closed(self):
        s=state()
        with self.assertRaises(d.DynamicBaseError): s.predict_batch([fx('a','A','B'),fx('b','A','C')])
        with self.assertRaises(d.DynamicBaseError): s.predict_batch([fx('x'),fx('x','C','D')])
        s.predict_batch([fx('ok')])
        with self.assertRaises(d.DynamicBaseError): s.predict_batch([fx('past','C','D',T0-timedelta(days=1))])

    def test_10_illegal_labels_and_changed_identity_fail_closed(self):
        s=state(); f=fx('ok'); s.predict_batch([f]); av=T0+timedelta(hours=3)
        with self.assertRaises(d.DynamicBaseError): s.apply_batch([f],{'ok':(-1,0,av)},as_of=av)
        with self.assertRaises(d.DynamicBaseError): s.apply_batch([f],{'ok':(1.0,0,av)},as_of=av)
        with self.assertRaises(d.DynamicBaseError): s.apply_batch([fx('ok','A','C')],{'ok':(1,0,av)},as_of=av)

    def test_11_sparse_subthreshold_dynamic_state_exactly_falls_back(self):
        s=state(); when=T0-timedelta(days=2); s.pooled_attack['A']=d.ResidualState(1.0,1.0,when,'2023-24')
        p=s.predict_batch([fx()])[0]; self.assertTrue(p['dynamic']['fallback_exact_v1'])
        base=v1.EngineState(v1.Parameters(**V1_PARAMS)).predict(fx()); self.assertEqual(p['score_matrix'],base['score_matrix'])

    def test_12_deterministic_state_and_prediction_hashes(self):
        a,b=state(),state(); batch=[fx('a','A','B')]; pa=a.predict_batch(batch)[0]; pb=b.predict_batch(batch)[0]
        self.assertEqual(pa['prediction_sha256'],pb['prediction_sha256']); self.assertEqual(a.state_digest(),b.state_digest())
        av=T0+timedelta(hours=3); labs={'a':(2,1,av)}; a.apply_batch(batch,labs,as_of=av); b.apply_batch(batch,labs,as_of=av)
        self.assertEqual(a.state_digest(),b.state_digest())

    def test_13_score_matrix_nonnegative_normalized_and_1x2_from_same_matrix(self):
        s=state(); when=T0-timedelta(days=5)
        s.venue_attack[('A','home')]=d.ResidualState(5,5,when,'2023-24'); s.pooled_attack['A']=d.ResidualState(5,5,when,'2023-24')
        p=s.predict_batch([fx()])[0]; m=p['score_matrix']; total=sum(c['probability'] for c in m)
        self.assertAlmostEqual(total,1.0,places=12); self.assertTrue(all(math.isfinite(c['probability']) and c['probability']>=0 for c in m))
        h,dr,a=v1.one_x_two(m); self.assertAlmostEqual(h,p['p_home'],places=14); self.assertAlmostEqual(dr,p['p_draw'],places=14); self.assertAlmostEqual(a,p['p_away'],places=14)

if __name__=='__main__': unittest.main()
