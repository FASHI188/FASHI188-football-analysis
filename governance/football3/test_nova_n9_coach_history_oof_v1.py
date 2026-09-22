from __future__ import annotations
import unittest
from datetime import datetime, timezone, timedelta
from nova_n9_coach_history_oof_v1 import raw_features, validate_source, blocks, metrics, align_baseline

P={'data':{'development_n':1},'development_protocol':{'new_manager_1_lte':1,'new_manager_3_lte':3,'established_manager_gte':8}}
def row(ht=1,at=8,hh=4,ah=12,hav=True,aav=True):
    return {'fixture_id':'f','kickoff':'2022-01-01T00:00:00Z','season_start':2022,'league':'EPL','home_team_id':'A','away_team_id':'B','tm_game_id':99,
            'home_manager_state':{'available':hav,'history_n':hh if hav else 0,'tenure_observation_n':ht if hav else 0,'source_game_id':1 if hav else None},
            'away_manager_state':{'available':aav,'history_n':ah if aav else 0,'tenure_observation_n':at if aav else 0,'source_game_id':2 if aav else None}}
class N9CoachOOFTests(unittest.TestCase):
    def test_source_rejects_target_manager_direct(self):
        r=row(); r['home_manager_state']['source_game_id']=99
        with self.assertRaises(Exception): validate_source([r],P)
    def test_fixed_routes(self):
        r=row(); self.assertEqual(len(raw_features(r,'R1_TENURE',P)),5); self.assertEqual(len(raw_features(r,'R2_CHANGE_BANDS',P)),8); self.assertEqual(len(raw_features(r,'R3_TENURE_DEPTH',P)),14); self.assertEqual(len(raw_features(r,'R4_FIXED_NONLINEAR',P)),19)
    def test_change_bands_are_deterministic(self):
        x=raw_features(row(ht=1,at=4),'R2_CHANGE_BANDS',P); self.assertEqual(x[:6],[1.0,0.0,1.0,1.0,0.0,1.0])
    def test_missing_state_is_not_zero_feature(self):
        x=raw_features(row(hav=False),'R1_TENURE',P); self.assertIsNone(x[0]); self.assertEqual(x[3],0.0)
    def test_baseline_alignment_label_free(self):
        r=row(); b={'n2_fixture_id':'f','kickoff':r['kickoff'],'home_team_id':'A','away_team_id':'B','league':'EPL','target_label_read':False}; self.assertEqual(align_baseline([r],[b])[0]['n2_fixture_id'],'f')
    def test_blocks_same_kickoff_atomic(self):
        rows=[]; t=datetime(2022,1,1,tzinfo=timezone.utc)
        for i in range(60): rows.append({'kickoff':(t+timedelta(days=i//2)).isoformat().replace('+00:00','Z')})
        warm,bs=blocks(rows,.2,5); self.assertEqual(warm%2,0); self.assertTrue(all(a%2==0 and b%2==0 for a,b in bs))
    def test_metrics_identity(self):
        p=[[.6,.2,.2],[.2,.3,.5]]; y=[0,2]; m=metrics(p,y); self.assertEqual(m['n'],2); self.assertEqual(m['top1'],1.0)
if __name__=='__main__': unittest.main()
