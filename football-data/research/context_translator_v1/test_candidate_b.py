from __future__ import annotations

import inspect
import pathlib
import unittest
from unittest.mock import patch

from candidate_b import CandidateBContractError, build_probability_mass_scenarios, capability_residual, candidate_contract, rolling_reference_lineups

CUTOFF='2026-01-10T12:00:00Z'


def expected(prefix,n=13,known='2026-01-09T12:00:00Z'):
    out=[]
    for i in range(n):
        starter=.95 if i<11 else (.30 if i==11 else .10)
        out.append({'player_id':f'{prefix}{i:02d}','starting_probability':starter,'availability_probability':.98,'expected_minutes_distribution':{'mean':82. if i<11 else (28. if i==11 else 12.)},'injury_status':'UNKNOWN','suspension_status':'UNKNOWN','return_status':'UNKNOWN','rotation_probability':1-starter,'role_distribution':{'MID':1.},'replacement_quality':0.,'uncertainty':.2,'known_at':known})
    return out


def usage(prefix,matches=4):
    out=[]
    for m in range(matches):
        known=f'2026-01-0{m+1}T12:00:00Z'
        players=[{'player_id':f'{prefix}{i:02d}','started':True,'appeared':True,'minutes':90.,'role':'MID','known_at':known} for i in range(11)]
        out.append({'players':players,'known_at':known,'match_id':m+1})
    return out


class CandidateBTests(unittest.TestCase):
    def test_b2_has_no_strength_input_and_normalizes(self):
        sig=inspect.signature(build_probability_mass_scenarios)
        self.assertNotIn('vectors',sig.parameters);self.assertNotIn('player_vectors',sig.parameters)
        sc=build_probability_mass_scenarios(expected('h'),expected('a'),cutoff=CUTOFF)
        self.assertGreaterEqual(len(sc),2);self.assertAlmostEqual(sum(x.probability for x in sc),1.,places=12);self.assertEqual(sc[0].source,'MODAL')

    def test_pit_violation_rejected(self):
        bad=expected('h');bad[0]['known_at']=CUTOFF
        with self.assertRaises(CandidateBContractError):build_probability_mass_scenarios(bad,expected('a'),cutoff=CUTOFF)

    def test_reference_strictly_prior(self):
        u={'T':usage('p',4)};self.assertEqual(len(rolling_reference_lineups(u,'T',cutoff=CUTOFF)),4)
        u['T'].append({'players':usage('p',1)[0]['players'],'known_at':CUTOFF,'match_id':99})
        with self.assertRaises(CandidateBContractError):rolling_reference_lineups(u,'T',cutoff=CUTOFF)

    def test_b1_residual_zero_if_current_equals_reference_and_fails_closed(self):
        u={'H':usage('h',4),'A':usage('a',4)}
        vectors={f'h{i:02d}':object() for i in range(13)}|{f'a{i:02d}':object() for i in range(13)}
        h=[f'h{i:02d}' for i in range(11)];a=[f'a{i:02d}' for i in range(11)]
        with patch('candidate_b.lineup_components',return_value=(.10,.08,.01,.20)):
            e=capability_residual(vectors=vectors,usage=u,home_team_id='H',away_team_id='A',home_player_ids=h,away_player_ids=a,cutoff=CUTOFF)
        self.assertTrue(e.active);self.assertAlmostEqual(e.log_mu_home_delta,0.);self.assertAlmostEqual(e.log_mu_away_delta,0.)
        with patch('candidate_b.lineup_components',return_value=(.10,.08,.01,.20)):
            e=capability_residual(vectors=vectors,usage={'H':usage('h',2),'A':usage('a',2)},home_team_id='H',away_team_id='A',home_player_ids=h,away_player_ids=a,cutoff=CUTOFF)
        self.assertFalse(e.active);self.assertEqual((e.log_mu_home_delta,e.log_mu_away_delta),(0.,0.))

    def test_contract_and_no_outcome_labels_in_core(self):
        c=candidate_contract();self.assertEqual(c['status'],'RESEARCH_ONLY_POST_VIEW_DIAGNOSTIC');self.assertIn('direct_1x2_probability_patch',c['forbidden'])
        text=pathlib.Path(__file__).with_name('candidate_b.py').read_text();self.assertNotIn('home_goals',text);self.assertNotIn('away_goals',text)


if __name__=='__main__':unittest.main()
