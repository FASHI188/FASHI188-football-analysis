#!/usr/bin/env python3
import importlib.util, math, pathlib, unittest

HERE=pathlib.Path(__file__).resolve().parent
MOD_PATH=HERE/'score_fplcache_pit_availability_dev_v1_1.py'
spec=importlib.util.spec_from_file_location('scorer',MOD_PATH)
scorer=importlib.util.module_from_spec(spec); spec.loader.exec_module(scorer)

class TestScorer(unittest.TestCase):
    def test_outcome_idx(self):
        self.assertEqual(scorer.outcome_idx(2,1),0)
        self.assertEqual(scorer.outcome_idx(1,1),1)
        self.assertEqual(scorer.outcome_idx(0,1),2)

    def test_iprojection_hits_target(self):
        m=[[0.20,0.10,0.05],[0.15,0.20,0.05],[0.10,0.05,0.10]]
        s=sum(map(sum,m)); m=[[v/s for v in r] for r in m]
        target=[0.45,0.30,0.25]
        q=scorer.iproject(m,target)
        got=scorer.integrate(q)
        for a,b in zip(got,target): self.assertAlmostEqual(a,b,12)

    def test_iprojection_preserves_within_region_ratio(self):
        m=[[0.20,0.05,0.05],[0.10,0.20,0.05],[0.04,0.06,0.25]]
        s=sum(map(sum,m)); m=[[v/s for v in r] for r in m]
        q=scorer.iproject(m,[0.35,0.40,0.25])
        self.assertAlmostEqual(q[1][0]/q[2][0],m[1][0]/m[2][0],12)

    def test_team_impairment_minutes_weighted_and_alias(self):
        snap={'teams':[{'id':1,'name':'Man Utd'}], 'players':[
            {'team':1,'minutes':900,'status':'a'},
            {'team':1,'minutes':900,'status':'i'},
            {'team':1,'minutes':0,'status':'u'}]}
        self.assertAlmostEqual(scorer.team_impairment(snap,'Man United',{'Man United':'Man Utd'}),0.5,12)

    def test_metric_identity(self):
        m=[[0.4,0.1],[0.2,0.3]]
        r={'y':0,'home_goals':1,'away_goals':0,'p':[0.6,0.2,0.2],'m':m}
        a=scorer.metric([r],'p','m'); b=scorer.metric([r],'p','m')
        self.assertEqual(a,b)

    def test_paired_required_no_effect(self):
        r={'y':1,'baseline_p':[0.3,0.4,0.3],'candidate_p':[0.3,0.4,0.3]}
        x=scorer.paired_required_n([r,r])
        self.assertAlmostEqual(x['effect'],0.0,15)
        self.assertIsNone(x['required_n'])

if __name__=='__main__': unittest.main()
