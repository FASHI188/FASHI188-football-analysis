from __future__ import annotations
import unittest
from datetime import datetime,timedelta,timezone
from nova_n7_opponent_adjusted_strength_oof_v1 import score,expectation,raw_states,basis,blocks,metrics
class T(unittest.TestCase):
    def p(self):return {'model':{'elo_initial':1500.0,'elo_k':20.0,'elo_scale':400.0,'elo_home_advantage':60.0,'shock_window':5}}
    def test_score(self):self.assertEqual([score(x) for x in ('home','draw','away')],[1.0,.5,0.0])
    def test_expectation(self):self.assertGreater(expectation(1500,1500,60,400),.5)
    def test_release_and_same_kickoff_atomic(self):
        r=[{'kickoff':'2022-01-01T12:00:00Z','release_at':'2022-01-01T16:00:00Z','home_team_id':'A','away_team_id':'B','outcome':'home'},{'kickoff':'2022-01-01T12:00:00Z','release_at':'2022-01-01T16:00:00Z','home_team_id':'C','away_team_id':'A','outcome':'away'},{'kickoff':'2022-01-01T15:00:00Z','release_at':'2022-01-01T19:00:00Z','home_team_id':'A','away_team_id':'D','outcome':'home'},{'kickoff':'2022-01-01T16:00:00Z','release_at':'2022-01-01T20:00:00Z','home_team_id':'A','away_team_id':'E','outcome':'draw'}]
        x=raw_states(r,self.p());self.assertEqual(x[0]['home_n'],0.0);self.assertEqual(x[1]['away_n'],0.0);self.assertEqual(x[2]['home_n'],0.0);self.assertGreater(x[3]['home_n'],0.0)
    def test_basis_fixed(self):
        r={'overall_diff':100.0,'venue_diff':80.0,'shock_diff':.1,'home_n':1.0,'away_n':2.0};self.assertEqual([len(basis(r,x,400.0)) for x in ('R1_ELO_DIFF','R2_ELO_PLUS_SHOCK5','R3_ELO_PLUS_VENUE','R4_ELO_NONLINEAR')],[3,4,4,7])
    def test_blocks_atomic(self):
        t=datetime(2022,1,1,tzinfo=timezone.utc);r=[]
        for i in range(60):r.append({'kickoff':(t+timedelta(days=i//2)).isoformat().replace('+00:00','Z')})
        w,b=blocks(r,.2,5);self.assertEqual(w%2,0);self.assertTrue(all(a%2==0 and z%2==0 for a,z in b))
    def test_metrics(self):self.assertEqual(metrics([[.7,.2,.1],[.1,.2,.7]],[0,2])['top1'],1.0)
if __name__=='__main__':unittest.main()
