from __future__ import annotations
import unittest
from datetime import datetime,timezone,timedelta
from nova_n10_discipline_oof_v1 import summarize,pair,raw,blocks,metrics
P={"development_protocol":{"w5":5,"w10":10,"ewma_alpha":.35}}
class T(unittest.TestCase):
 def test_routes(self):
  h=[(1,0),(2,0),(3,1),(2,0),(4,0),(1,0)]
  self.assertEqual(len(summarize(h,"R1_W5_YELLOW",P)),2);self.assertEqual(len(summarize(h,"R2_W10_YELLOW_RED",P)),3);self.assertEqual(len(summarize(h,"R3_EWMA035",P)),3);self.assertEqual(len(summarize(h,"R4_W5_W10_VOLATILITY",P)),7)
 def test_pair(self):self.assertEqual(len(pair([1,2],[3,4],2)),6)
 def test_same_kickoff(self):
  rows=[{"fixture_id":"a","kickoff":"2022-01-01T00:00:00Z","release_at":"2022-01-01T03:00:00Z","home_team_id":"A","away_team_id":"B","home_yellow":5,"away_yellow":0,"home_red":0,"away_red":0},{"fixture_id":"b","kickoff":"2022-01-01T00:00:00Z","release_at":"2022-01-01T03:00:00Z","home_team_id":"A","away_team_id":"C","home_yellow":0,"away_yellow":0,"home_red":0,"away_red":0}]
  x=raw(rows,"R1_W5_YELLOW",P);self.assertIsNone(x[0][0]);self.assertIsNone(x[1][0])
 def test_prior_release(self):
  rows=[{"fixture_id":"a","kickoff":"2022-01-01T00:00:00Z","release_at":"2022-01-01T03:00:00Z","home_team_id":"A","away_team_id":"B","home_yellow":5,"away_yellow":1,"home_red":0,"away_red":0},{"fixture_id":"b","kickoff":"2022-01-02T00:00:00Z","release_at":"2022-01-02T03:00:00Z","home_team_id":"A","away_team_id":"C","home_yellow":0,"away_yellow":0,"home_red":0,"away_red":0}]
  self.assertEqual(raw(rows,"R1_W5_YELLOW",P)[1][0],5.0)
 def test_blocks(self):
  t=datetime(2022,1,1,tzinfo=timezone.utc);rows=[{"kickoff":(t+timedelta(days=i//2)).isoformat().replace("+00:00","Z")} for i in range(100)];w,b=blocks(rows,.2,5);self.assertEqual(w%2,0);self.assertTrue(all(x%2==0 and y%2==0 for x,y in b))
 def test_metrics(self):self.assertEqual(metrics([[.6,.2,.2],[.2,.3,.5]],[0,2])["top1"],1.0)
if __name__=="__main__":unittest.main()
