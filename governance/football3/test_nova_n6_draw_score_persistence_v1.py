from __future__ import annotations
import unittest
from datetime import datetime, timedelta, timezone
from nova_n6_formal_v2_baseline_seal_v1 import mix
from nova_n6_draw_score_persistence_oof_v1 import build_raw,primitive,blocks,metrics
class T(unittest.TestCase):
    def test_mix_fallback(self):
        v={'p_home':.5,'p_draw':.3,'p_away':.2};x=dict(v);x['dynamic']={'fallback_exact_v1':True};p,fb=mix(v,x);self.assertTrue(fb);self.assertEqual(p,[.5,.3,.2])
    def test_mix_weighted(self):
        v={'p_home':.4,'p_draw':.3,'p_away':.3};x={'p_home':.6,'p_draw':.2,'p_away':.2,'dynamic':{'fallback_exact_v1':False}};p,fb=mix(v,x);self.assertFalse(fb);self.assertAlmostEqual(p[0],.55)
    def test_pit_release_and_same_kickoff(self):
        rows=[
            {'fixture_id':'f1','kickoff':'2020-01-01T12:00:00Z','release_at':'2020-01-01T15:00:00Z','home_team_id':'A','away_team_id':'B','is_draw':1,'low_total_le2':1,'tight_margin_le1':1},
            {'fixture_id':'f2','kickoff':'2020-01-04T12:00:00Z','release_at':'2020-01-04T15:00:00Z','home_team_id':'A','away_team_id':'C','is_draw':0,'low_total_le2':0,'tight_margin_le1':0},
            {'fixture_id':'f3','kickoff':'2020-01-04T12:00:00Z','release_at':'2020-01-04T15:00:00Z','home_team_id':'B','away_team_id':'D','is_draw':0,'low_total_le2':0,'tight_margin_le1':0},
            {'fixture_id':'f4','kickoff':'2020-01-04T14:00:00Z','release_at':'2020-01-04T17:00:00Z','home_team_id':'A','away_team_id':'B','is_draw':0,'low_total_le2':0,'tight_margin_le1':0}]
        raw=build_raw(rows);self.assertEqual(raw[1][0]['d10'],1.0);self.assertEqual(raw[2][0]['d10'],1.0);self.assertEqual(raw[3][0]['d10'],1.0);self.assertEqual(raw[3][1]['d10'],1.0)
    def test_fixed_routes(self):
        h={'d5':.2,'d10':.3,'l5':.4,'l10':.5,'t5':.6,'t10':.7,'n':1.};a={'d5':.3,'d10':.4,'l5':.5,'l10':.6,'t5':.7,'t10':.8,'n':1.2};pair=(h,a)
        self.assertEqual(len(primitive(pair,'R1_DRAW_W10')),5);self.assertEqual(len(primitive(pair,'R2_LOW_TOTAL_W10')),5);self.assertEqual(len(primitive(pair,'R3_TIGHT_MARGIN_W10')),5);self.assertEqual(len(primitive(pair,'R4_W5_W10_COMBINED')),20)
    def test_blocks_atomic(self):
        t=datetime(2020,1,1,tzinfo=timezone.utc);rows=[]
        for i in range(60):rows.append({'kickoff':(t+timedelta(days=i//2)).isoformat().replace('+00:00','Z')})
        w,b=blocks(rows,.2,5);self.assertEqual(w%2,0);self.assertTrue(all(a%2==0 and z%2==0 for a,z in b))
    def test_metrics(self):
        m=metrics([[.6,.2,.2],[.2,.3,.5]],[0,2]);self.assertEqual(m['n'],2);self.assertEqual(m['top1'],1.0)
if __name__=='__main__':unittest.main()
