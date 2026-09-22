from __future__ import annotations
import unittest
from datetime import datetime,timezone,timedelta
from nova_n8_shot_quality_setpiece_source_v1 import summarize
from nova_n8_shot_quality_setpiece_oof_v1 import raw_features,primitive,align_baseline,blocks,metrics

class N8Tests(unittest.TestCase):
    def test_summarize_excludes_penalty_from_quality(self):
        s=summarize([(0.1,'OpenPlay'),(0.2,'FromCorner'),(0.8,'Penalty')],{'FromCorner','SetPiece','DirectFreekick'},0.2)
        self.assertAlmostEqual(s['openplay_xg_per_shot'],0.1); self.assertAlmostEqual(s['setpiece_xg_share'],2/3); self.assertAlmostEqual(s['highq_np_shot_share'],0.5)
    def test_same_kickoff_and_release_atomic(self):
        side={'openplay_xg_per_shot':.1,'setpiece_xg_share':.2,'highq_np_shot_share':.3,'np_shot_xg_sd':.05,'np_shots':10.,'setpiece_shots':2.,'openplay_shots':7.}
        rows=[
          {'fixture_id':'f1','kickoff':'2022-01-01T12:00:00Z','release_at':'2022-01-01T15:00:00Z','home_team_id':'A','away_team_id':'B','home':side,'away':side},
          {'fixture_id':'f2','kickoff':'2022-01-01T12:00:00Z','release_at':'2022-01-01T15:00:00Z','home_team_id':'C','away_team_id':'D','home':side,'away':side},
          {'fixture_id':'f3','kickoff':'2022-01-01T14:00:00Z','release_at':'2022-01-01T17:00:00Z','home_team_id':'A','away_team_id':'C','home':side,'away':side},
          {'fixture_id':'f4','kickoff':'2022-01-01T16:00:00Z','release_at':'2022-01-01T19:00:00Z','home_team_id':'A','away_team_id':'B','home':side,'away':side}]
        raw=raw_features(rows,10)
        self.assertEqual(raw[2][8],0.0); self.assertEqual(raw[3][8],1.0); self.assertEqual(raw[3][17],1.0)
    def test_routes_fixed(self):
        row=[.1,.2,.3,.4,.5,.6,.7,.8,5.,.2,.3,.4,.5,.6,.7,.8,.9,6.]
        self.assertEqual(len(primitive(row,'R1_OPENPLAY_QUALITY')),6)
        self.assertEqual(len(primitive(row,'R2_SETPIECE_SHARE')),6)
        self.assertEqual(len(primitive(row,'R3_HIGHQ_DISPERSION')),10)
        self.assertEqual(len(primitive(row,'R4_COMBINED_FIXED')),18)
    def test_baseline_label_free(self):
        s=[{'fixture_id':'f','kickoff':'2022-01-01T00:00:00Z','home_team_id':'A','away_team_id':'B','league':'EPL'}]
        b=[{'n2_fixture_id':'f','kickoff':'2022-01-01T00:00:00Z','home_team_id':'A','away_team_id':'B','league':'EPL','target_label_read':False}]
        self.assertEqual(align_baseline(s,b)[0]['n2_fixture_id'],'f')
    def test_blocks_atomic(self):
        rows=[]; t=datetime(2022,1,1,tzinfo=timezone.utc)
        for i in range(60): rows.append({'kickoff':(t+timedelta(days=i//2)).isoformat().replace('+00:00','Z')})
        warm,bs=blocks(rows,.2,5); self.assertEqual(warm%2,0); self.assertTrue(all(a%2==0 and b%2==0 for a,b in bs))
    def test_metrics(self):
        m=metrics([[.6,.2,.2],[.2,.3,.5]],[0,2]); self.assertEqual(m['n'],2); self.assertEqual(m['top1'],1.0)

if __name__=='__main__': unittest.main()
