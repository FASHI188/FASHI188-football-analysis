#!/usr/bin/env python3
import importlib.util,json,pathlib,unittest
ROOT=pathlib.Path(__file__).parent
S=importlib.util.spec_from_file_location('tfa',ROOT/'transition_family_attribution.py'); m=importlib.util.module_from_spec(S); S.loader.exec_module(m)
V324_PATH=ROOT.parent/'historical_fusion_v3_2_4_minimal_boundary_projection'/'v3_2_4_segment4.py'; V324=m.loadmod('tfa_v324_test',V324_PATH)
class T(unittest.TestCase):
 def test_draw_to_side(self): self.assertEqual(m.proposal_family([.34,.40,.26],[.42,.31,.27])[0],'DRAW_TO_SIDE_ONLY')
 def test_side_to_side(self): self.assertEqual(m.proposal_family([.45,.30,.25],[.29,.30,.41])[0],'SIDE_TO_SIDE_ONLY')
 def test_draw_target_excluded(self): self.assertIsNone(m.proposal_family([.45,.30,.25],[.30,.42,.28])[0])
 def test_same_top1_excluded(self): self.assertIsNone(m.proposal_family([.45,.30,.25],[.44,.31,.25])[0])
 def test_projection_exact(self):
  p,r=m.project(V324,[.45,.30,.25],2,1e-9); self.assertTrue(r['executed']); self.assertEqual(V324.top1(p),2)
 def test_weak_floor(self):
  b=[.45,.30,.25]; p,_=m.project(V324,b,2,1e-9); self.assertGreaterEqual(p[2]+1e-12,b[2])
 def test_contract_two_stage_disjoint(self):
  c=json.loads((ROOT/'TRANSITION_FAMILY_ATTRIBUTION_CONTRACT.json').read_text()); d={x['code'] for x in c['data']['development_leagues']}; q={x['code'] for x in c['data']['confirmation_leagues']}; self.assertFalse(d&q); self.assertEqual(c['development_protocol']['candidate_families'],['DRAW_TO_SIDE_ONLY','SIDE_TO_SIDE_ONLY']); self.assertTrue(c['development_protocol']['no_rescue_after_development']); self.assertEqual(c['confirmation_protocol']['chronological_folds'],12)
if __name__=='__main__':unittest.main()
