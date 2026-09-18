from __future__ import annotations
import unittest
from datetime import datetime, timezone, timedelta
from nova_n5_rest_congestion_source_v1 import project
from nova_n5_rest_congestion_oof_v1 import build_raw, primitive, align_baseline, blocks, metrics

class N5Tests(unittest.TestCase):
    def test_source_identity_ignores_result_fields(self):
        payload={"dates":[{"id":"1","datetime":"2022-08-01 12:00:00","h":{"id":"10","title":"A"},"a":{"id":"20","title":"B"},"isResult":True,"goals":{"h":"9","a":"8"},"xG":{"h":"7","a":"6"}}]}
        rows=project(payload,"EPL",2022,1,3); self.assertEqual(rows[0]["fixture_id"],"understat:1")
        self.assertEqual(set(rows[0]),{"fixture_id","league","season_start","kickoff","release_at","home_team_id","away_team_id"})
    def test_pit_excludes_unreleased_and_same_kickoff(self):
        rows=[
          {"fixture_id":"f1","kickoff":"2022-08-01T12:00:00Z","release_at":"2022-08-01T15:00:00Z","home_team_id":"A","away_team_id":"B"},
          {"fixture_id":"f2","kickoff":"2022-08-04T12:00:00Z","release_at":"2022-08-04T15:00:00Z","home_team_id":"A","away_team_id":"C"},
          {"fixture_id":"f3","kickoff":"2022-08-04T12:00:00Z","release_at":"2022-08-04T15:00:00Z","home_team_id":"B","away_team_id":"D"},
          {"fixture_id":"f4","kickoff":"2022-08-04T14:00:00Z","release_at":"2022-08-04T17:00:00Z","home_team_id":"A","away_team_id":"B"}]
        raw=build_raw(rows)
        self.assertAlmostEqual(raw[1][0],3.0); self.assertAlmostEqual(raw[2][0],3.0)
        self.assertAlmostEqual(raw[3][0],3.0+2.0/24.0); self.assertAlmostEqual(raw[3][1],3.0+2.0/24.0)
        self.assertEqual(raw[3][3],1.0); self.assertEqual(raw[3][4],1.0)
    def test_routes_are_fixed(self):
        row=[5.0,4.0,1.0,1.0,2.0,-1.0,2.0,3.0,-1.0,1.1,1.2]
        self.assertEqual(len(primitive(row,"R1_REST_DIFF")),5); self.assertEqual(len(primitive(row,"R2_CONGESTION_7_14")),6)
        self.assertEqual(len(primitive(row,"R3_REST_PLUS_CONGESTION")),11); self.assertEqual(len(primitive(row,"R4_RECOVERY_NONLINEAR")),16)
    def test_baseline_alignment_is_label_free(self):
        s=[{"fixture_id":"f","kickoff":"2022-01-01T00:00:00Z","home_team_id":"A","away_team_id":"B","league":"EPL"}]
        b=[{"n2_fixture_id":"f","kickoff":"2022-01-01T00:00:00Z","home_team_id":"A","away_team_id":"B","league":"EPL","target_label_read":False}]
        self.assertEqual(align_baseline(s,b)[0]["n2_fixture_id"],"f")
    def test_blocks_keep_same_kickoff_atomic(self):
        rows=[]; t=datetime(2022,1,1,tzinfo=timezone.utc)
        for i in range(60):
            k=(t+timedelta(days=i//2)).isoformat().replace("+00:00","Z"); rows.append({"kickoff":k})
        warm,bs=blocks(rows,.2,5); self.assertEqual(warm%2,0); self.assertTrue(all(a%2==0 and b%2==0 for a,b in bs))
    def test_metrics_identity(self):
        p=[[.6,.2,.2],[.2,.3,.5]]; y=[0,2]; m=metrics(p,y); self.assertEqual(m["n"],2); self.assertEqual(m["top1"],1.0)

if __name__=="__main__": unittest.main()
