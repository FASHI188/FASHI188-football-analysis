#!/usr/bin/env python3
import importlib.util,json,pathlib,unittest
ROOT=pathlib.Path(__file__).parent
S=importlib.util.spec_from_file_location('shc',ROOT/'side_head_consistency_external_confirm.py'); m=importlib.util.module_from_spec(S); S.loader.exec_module(m)
V324_PATH=ROOT.parent/'historical_fusion_v3_2_4_minimal_boundary_projection'/'v3_2_4_segment4.py'; V324=m.loadmod('shc_v324_test',V324_PATH)
class T(unittest.TestCase):
 def test_norm(self):
  p=m.norm([2,4,4]); self.assertAlmostEqual(sum(p),1); self.assertEqual(m.top1(p),0)
 def test_home_consistent(self): self.assertTrue(m.consistent([.35,.35,.30],[.42,.30,.28],0))
 def test_home_inconsistent(self): self.assertFalse(m.consistent([.40,.35,.25],[.38,.30,.32],0))
 def test_away_consistent(self): self.assertTrue(m.consistent([.40,.30,.30],[.30,.28,.42],2))
 def test_projection_exact(self):
  p,r=m.proj(V324,[.42,.38,.20],2,1e-9); self.assertTrue(r['executed']); self.assertEqual(V324.top1(p),2)
 def test_weak_floor(self):
  b=[.42,.38,.20]; p,_=m.proj(V324,b,2,1e-9); self.assertGreaterEqual(p[0]+1e-12,b[0])
 def test_contract(self):
  c=json.loads((ROOT/'SIDE_HEAD_CONSISTENCY_EXTERNAL_CONFIRM_CONTRACT.json').read_text()); self.assertTrue(c['proxy_mapping']['no_learning'] and c['proxy_mapping']['no_parameter_fit'] and c['proxy_mapping']['no_threshold_grid']); self.assertEqual({x['code'] for x in c['data']['leagues']},{'E2','E3','SC1','SC2','SC3'}); self.assertEqual(c['proxy_mapping']['draw_target'],'excluded from both always-side comparator and candidate because this diagnostic targets the non-draw side head and frozen V3.2.4 had zero D switch targets.')
if __name__=='__main__': unittest.main()
