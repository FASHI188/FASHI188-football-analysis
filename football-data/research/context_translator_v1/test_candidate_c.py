from __future__ import annotations

import unittest

from candidate_c import (
    CandidateCContractError,
    ComponentEffect,
    SideDelta,
    c1_availability_replacement,
    c2_possible_xi,
    deduplicated_c1_plus_lineup,
    evidence_grade,
    matchup_log_mu,
    probability_mass_supported,
)
from player_strength import DIMS, PlayerVector


def vec(pid: str, team: str, role: str, base: float, unc: float = 0.2) -> PlayerVector:
    vals={d:base for d in DIMS}
    vals['possession_retention_risk']=0.25*base
    return PlayerVector(pid,team,'GER1',role,{role:1.0},vals,8.0,unc,'2024-01-01T00:00:00Z','FULL_EVENT',[],f'{pid:0>64}'[-64:],0)


def lineup(team: str, start: int) -> list[dict]:
    return [{'player_id':str(start+i),'starting_probability':None,'expected_minutes':None} for i in range(11)]


class CandidateCTests(unittest.TestCase):
    def test_evidence_grade_possible_requires_full_identity(self):
        p={'pit_legal':True,'source':{'available_at':'2024-01-01T00:00:00Z'},'predicted_lineups':{'home':lineup('h',1),'away':lineup('a',101)},'confirmed_lineups':None,'bench':None,'status_records':[]}
        self.assertEqual(evidence_grade(p,'2024-01-02T00:00:00Z'),'POSSIBLE_XI_PIT')
        p['predicted_lineups']['home'][0]['player_id']=None
        p['status_records']=[{'player_id':'1','status_type':'SUSPENSION'}]
        self.assertEqual(evidence_grade(p,'2024-01-02T00:00:00Z'),'TEAM_NEWS_AVAILABILITY_PIT')

    def test_c1_only_uses_unambiguous_suspension_direction(self):
        vectors={'1':vec('1','H','FW',2.0),'2':vec('2','H','FW',1.0),'3':vec('3','A','FW',1.0),'4':vec('4','A','FW',1.0)}
        injury=c1_availability_replacement(vectors=vectors,home_team_id='H',away_team_id='A',status_records=[{'player_id':'1','status_type':'INJURY_OR_AVAILABILITY'}],evidence_uncertainty=0.65)
        self.assertFalse(injury.active)
        susp=c1_availability_replacement(vectors=vectors,home_team_id='H',away_team_id='A',status_records=[{'player_id':'1','status_type':'SUSPENSION'}],evidence_uncertainty=0.65)
        self.assertTrue(susp.active)
        self.assertLess(susp.home.delta_attack,0.0)
        self.assertEqual(susp.away.delta_attack,0.0)

    def test_c2_no_probability_invention_contract(self):
        vectors={}
        for i in range(1,12):vectors[str(i)]=vec(str(i),'H','MF',1.0+i/100)
        for i in range(101,112):vectors[str(i)]=vec(str(i),'A','MF',1.0+i/1000)
        usage={'H':[],'A':[]}
        for j in range(3):
            usage['H'].append({'known_at':f'2023-12-0{j+1}T00:00:00Z','match_id':f'h{j}','players':[{'player_id':str(i),'started':True} for i in range(1,12)]})
            usage['A'].append({'known_at':f'2023-12-0{j+1}T00:00:00Z','match_id':f'a{j}','players':[{'player_id':str(i),'started':True} for i in range(101,112)]})
        px={'home':lineup('h',1),'away':lineup('a',101)}
        e=c2_possible_xi(vectors=vectors,usage=usage,home_team_id='H',away_team_id='A',predicted_lineups=px,cutoff='2024-01-02T00:00:00Z')
        self.assertTrue(e.active)
        px['home'][0]['starting_probability']=0.8
        with self.assertRaises(CandidateCContractError):
            c2_possible_xi(vectors=vectors,usage=usage,home_team_id='H',away_team_id='A',predicted_lineups=px,cutoff='2024-01-02T00:00:00Z')

    def test_dedupe_absorbs_c1_when_lineup_active(self):
        c1=ComponentEffect('C1',True,SideDelta(.02,0,0,.5),SideDelta(0,0,0,.5),'ACTIVE',['1'],0,0,0,'a'*64)
        c2=ComponentEffect('C2',True,SideDelta(.03,0,0,.4),SideDelta(0,0,0,.4),'ACTIVE',[],0,3,3,'b'*64)
        e,d=deduplicated_c1_plus_lineup(c1,c2,grade='POSSIBLE_XI_PIT')
        self.assertTrue(e.active)
        self.assertAlmostEqual(e.home.delta_attack,.03)
        self.assertTrue(d['c1_absorbed_by_lineup_residual'])

    def test_uncertainty_shrinks_score_core_adapter(self):
        low=ComponentEffect('X',True,SideDelta(.1,0,0,0),SideDelta(0,0,0,0),'A',[],0,0,0,'c'*64)
        high=ComponentEffect('X',True,SideDelta(.1,0,0,2),SideDelta(0,0,0,2),'A',[],0,0,0,'d'*64)
        l=matchup_log_mu(low);h=matchup_log_mu(high)
        self.assertGreater(abs(l[0]),abs(h[0]))

    def test_probability_mass_stays_zero_without_real_input(self):
        p={'predicted_lineups':{'home':lineup('h',1),'away':lineup('a',101)},'confirmed_lineups':None,'bench':None}
        self.assertFalse(probability_mass_supported(p))
        p['predicted_lineups']['home'][0]['starting_probability']=0.7
        self.assertTrue(probability_mass_supported(p))


if __name__=='__main__':
    unittest.main()
