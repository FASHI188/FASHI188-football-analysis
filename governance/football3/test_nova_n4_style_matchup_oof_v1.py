import unittest
import nova_n4_style_matchup_oof_v1 as n4
class T(unittest.TestCase):
    def test_expand_dimensions(self):
        z=[1,2,3,4,5,6]
        self.assertEqual(len(n4.expand(z,"R1_W10_LINEAR")),6)
        self.assertEqual(len(n4.expand(z,"R2_W10_CROSS")),8)
        self.assertEqual(len(n4.expand(z,"R3_W10_WITHIN")),8)
        self.assertEqual(len(n4.expand(z,"R4_W10_FULL")),11)
    def test_same_kickoff_atomic(self):
        rows=[{"fixture_id":"a","kickoff":"2022-01-01T10:00:00Z","release_at":"2022-01-01T13:00:00Z","home_team_id":"h","away_team_id":"a","home_ppda":10,"away_ppda":20,"home_deep":5,"away_deep":2},{"fixture_id":"b","kickoff":"2022-01-01T10:00:00Z","release_at":"2022-01-01T13:00:00Z","home_team_id":"h","away_team_id":"x","home_ppda":11,"away_ppda":21,"home_deep":6,"away_deep":3},{"fixture_id":"c","kickoff":"2022-01-02T10:00:00Z","release_at":"2022-01-02T13:00:00Z","home_team_id":"h","away_team_id":"x","home_ppda":12,"away_ppda":22,"home_deep":7,"away_deep":4}]
        raw=n4.raw_features(rows)
        self.assertIsNone(raw[0][0]); self.assertIsNone(raw[1][0]); self.assertAlmostEqual(raw[2][0],10.5)
    def test_softmax(self):
        p=n4.softmax([.4,.3,.3],[0,0,0,0,0,0],[[0]*7,[0]*7]); self.assertAlmostEqual(sum(p),1.0); self.assertAlmostEqual(p[0],.4)
    def test_metrics_perfect(self):
        m=n4.metrics([[1,0,0],[0,1,0],[0,0,1]],[0,1,2]); self.assertEqual(m["top1"],1.0); self.assertLess(m["logloss"],1e-12)
    def test_positive_floor(self):
        p={"development_protocol":{"positive_signal_floor":{"logloss_gain_gt":0,"candidate_minus_formal_brier_lte":.001,"candidate_minus_formal_rps_lte":.001,"candidate_minus_formal_top1_gte":-.005}}}; b={"logloss":1.0,"brier":.6,"rps":.2,"top1":.5}; c={"logloss":.999,"brier":.6005,"rps":.2005,"top1":.499}; self.assertTrue(n4.positive_floor(p,b,c))
    def test_blocks(self):
        rows=[]
        for i in range(100): rows.append({"kickoff":f"2022-01-{1+i//4:02d}T10:00:00Z"})
        w,b=n4.blocks(rows,.2,5); self.assertGreaterEqual(w,20); self.assertEqual(len(b),5); self.assertEqual(b[-1][1],100)
        source=[{"fixture_id":"f1","kickoff":"2022-01-01T10:00:00Z","home_team_id":"h1","away_team_id":"a1","league":"EPL"},{"fixture_id":"f2","kickoff":"2022-01-02T10:00:00Z","home_team_id":"h2","away_team_id":"a2","league":"EPL"}]
        baseline=[{"n2_fixture_id":"f2","kickoff":"2022-01-02T10:00:00+00:00","home_team_id":"h2","away_team_id":"a2","league":"EPL"},{"n2_fixture_id":"f1","kickoff":"2022-01-01T10:00:00+00:00","home_team_id":"h1","away_team_id":"a1","league":"EPL"}]
        aligned=n4.align_baseline(source,baseline)
        self.assertEqual([x["n2_fixture_id"] for x in aligned],["f1","f2"])
if __name__=="__main__": unittest.main()
