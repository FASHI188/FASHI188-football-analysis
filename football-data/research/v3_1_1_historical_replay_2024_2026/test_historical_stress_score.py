from __future__ import annotations
import importlib.util, json, pathlib, unittest

ROOT=pathlib.Path(__file__).resolve().parent
SRC=ROOT/'historical_stress_score.py'
CONTRACT=ROOT/'HISTORICAL_STRESS_TEST_CONTRACT.json'

def load():
    s=importlib.util.spec_from_file_location('stress',SRC); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m

class FakeState:
    def __init__(self,w=0.0,s=None): self.w=w; self.s=s or {}
    def snapshot(self,t): return self.w,dict(self.s)

class FakeUsr:
    MIN_PROFILE_WEIGHT=3.0
    PRIOR_MATCHES=12.0

class TestHistoricalStress(unittest.TestCase):
    def test_contract_post_view_no_future(self):
        c=json.loads(CONTRACT.read_text())
        self.assertEqual(c['status'],'FROZEN_BEFORE_2024_2026_HISTORICAL_STRESS_SCORING')
        self.assertEqual(c['classification'],'POST_VIEW_HISTORICAL_STRESS_TEST')
        self.assertFalse(c['fresh_confirmation_claim'])
        self.assertEqual(c['historical_data']['season_start_years'],[2024,2025])
        self.assertIn('future_fixture',c['forbidden'])

    def test_candidate_and_formal_frozen(self):
        c=json.loads(CONTRACT.read_text())
        self.assertEqual(c['candidate']['head'],'a90762a97515f3edd564e8ad204db0d0d4231494')
        self.assertEqual(c['candidate']['id'],'V3.1.1-A')
        self.assertEqual(c['candidate']['residual_scale'],0.25)
        self.assertEqual(c['formal_v2']['head'],'e12f5d1193be5d81f60301cf34ab2140e11712a9')
        self.assertEqual(c['formal_v2']['xg_weight'],0.75)

    def test_team_namespace_exact(self):
        m=load()
        self.assertEqual(m.team_num('understat-team:123'),'123')
        with self.assertRaises(m.StressError): m.team_num('123')

    def test_zero_shot_process_skip_retains_fixture(self):
        m=load()
        f={'fixture_id':'understat:1','kickoff':'2025-01-01T12:00:00+00:00','home_team_id':'understat-team:10','away_team_id':'understat-team:20'}
        u={'fixture_id':'understat:1','release_at':'2025-01-01T15:00:00+00:00','process_update_eligible':False}
        self.assertEqual(m.process_updates_from_row(f,u),[])

    def test_process_profile_min_weight_fallback(self):
        m=load(); states={'10':FakeState(2.9,{'npxg_for':1.0}),'20':FakeState(2.9,{'npxg_for':1.0})}
        f={'league':'EPL','kickoff':'2025-01-01T12:00:00+00:00','home_team_id':'understat-team:10','away_team_id':'understat-team:20'}
        pri={'EPL':{'npxg_for':1.0}}
        q=m.process_profile(FakeUsr,states,pri,f)
        self.assertFalse(q['valid'])

    def test_batch_same_kickoff_atomic(self):
        m=load()
        rows=[{'fixture_id':'b','kickoff':'2025-01-01T12:00:00+00:00'},{'fixture_id':'c','kickoff':'2025-01-01T12:00:00+00:00'},{'fixture_id':'d','kickoff':'2025-01-01T13:00:00+00:00'}]
        b=m.batches(rows)
        self.assertEqual([len(x) for x in b],[2,1])

    def test_bootstrap_deterministic(self):
        m=load(); a=m.bootstrap_pair([.1,.2,-.1],[.05,.1,-.02],100,7); b=m.bootstrap_pair([.1,.2,-.1],[.05,.1,-.02],100,7)
        self.assertEqual(a,b)

    def test_matrix_contract_is_15_by_15(self):
        c=json.loads(CONTRACT.read_text())
        self.assertEqual(c['matrix']['shape'],[15,15]); self.assertEqual(c['matrix']['cell_count'],225)
        self.assertEqual(c['matrix']['one_x_two_source'],'INTEGRATE_FINAL_MATRIX_ONLY')

if __name__=='__main__': unittest.main()
