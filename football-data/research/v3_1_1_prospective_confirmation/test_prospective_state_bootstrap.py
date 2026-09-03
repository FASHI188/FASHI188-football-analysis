from __future__ import annotations
import importlib.util, json, pathlib, sys, unittest
from datetime import datetime, timezone

ROOT=pathlib.Path(__file__).resolve().parent
STATE=ROOT/'FROZEN_CANDIDATE_STATE.json'
SRC=ROOT/'prospective_state_bootstrap.py'

def load():
    s=importlib.util.spec_from_file_location('prospective_state_bootstrap',SRC)
    m=importlib.util.module_from_spec(s); sys.modules[s.name]=m; s.loader.exec_module(m); return m

class TestStateBootstrap(unittest.TestCase):
    def test_frozen_contract_and_state_bindings(self):
        c=json.loads((ROOT/'PROSPECTIVE_CONFIRMATION_CONTRACT.json').read_text()); s=json.loads(STATE.read_text())
        self.assertEqual(c['status'],'FROZEN_BEFORE_TARGET_ENROLLMENT')
        self.assertEqual(c['required_n'],1603)
        self.assertEqual(c['target_cutoff_utc'],'2026-09-02T17:00:00Z')
        self.assertEqual(s['canonical_payload_sha256'],'22332a2053451a1a749b6dbc2818fa12cb21d68295510097a87dbbdb20d9f8ea')

    def test_exact_process_shot_semantics(self):
        m=load()
        rows=[
          {'h_a':'h','xG':'0.30','situation':'OpenPlay'},
          {'h_a':'h','xG':'0.70','situation':'Penalty'},
          {'h_a':'h','xG':'0.20','situation':'FromCorner'},
          {'h_a':'a','xG':'0.10','situation':'DirectFreekick'},
          {'h_a':'a','xG':'0.40','situation':'OpenPlay'},
        ]
        r=m.aggregate_shots(rows)
        self.assertAlmostEqual(r['h']['npxg_for'],0.5)
        self.assertEqual(r['h']['npshots_for'],2.0)
        self.assertAlmostEqual(r['h']['open_share'],0.5)
        self.assertAlmostEqual(r['h']['set_share'],0.5)
        self.assertAlmostEqual(r['a']['npxg_against'],0.5)

    def test_penalty_only_side_fails_process_update(self):
        m=load()
        rows=[{'h_a':'h','xG':'0.7','situation':'Penalty'},{'h_a':'a','xG':'0.1','situation':'OpenPlay'}]
        self.assertIsNone(m.aggregate_shots(rows))

    def test_process_decay_matches_frozen_half_life(self):
        m=load(); s=m.ProcessTeamState()
        t=datetime(2026,1,1,tzinfo=timezone.utc)
        s.add(t,{'npxg_for':1.0})
        w,v=s.snapshot(datetime(2026,4,1,tzinfo=timezone.utc))
        self.assertAlmostEqual(w,0.5,places=12)
        self.assertAlmostEqual(v['npxg_for'],0.5,places=12)

    def test_target_ajax_is_hard_forbidden(self):
        m=load()
        row={'mid':999,'kickoff':m.CUTOFF,'fixture_id':'understat:999'}
        with self.assertRaises(RuntimeError):
            m.fetch_bridge_process([row],1)

    def test_future_identity_has_no_result_fields(self):
        m=load()
        r={'fixture_id':'understat:1','mid':1,'league':'EPL','competition_id':'understat-league:1','season':'2026',
           'kickoff':datetime(2026,9,4,tzinfo=timezone.utc),'home_team_id':'understat-team:1','away_team_id':'understat-team:2',
           'home_team':'A','away_team':'B','home_goals':9,'away_goals':9,'home_xg':9.0,'away_xg':9.0}
        p=m.public_future_row(r)
        self.assertNotIn('home_goals',p); self.assertNotIn('home_xg',p)
        self.assertEqual(p['fixture_id'],'understat:1')

if __name__=='__main__': unittest.main()
