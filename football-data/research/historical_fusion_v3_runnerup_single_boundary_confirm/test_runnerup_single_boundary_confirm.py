#!/usr/bin/env python3
import importlib.util,json,pathlib,unittest
ROOT=pathlib.Path(__file__).parent
S=importlib.util.spec_from_file_location('ru',ROOT/'runnerup_single_boundary_confirm.py'); m=importlib.util.module_from_spec(S); S.loader.exec_module(m)
V324_PATH=ROOT.parent/'historical_fusion_v3_2_4_minimal_boundary_projection'/'v3_2_4_segment4.py'; V324=m.loadmod('ru_v324_test',V324_PATH)
class T(unittest.TestCase):
 def test_side_runnerup(self):
  old,t,a,s,r=m.classify([.45,.30,.25],[.29,.30,.41]); self.assertEqual((old,t,a,s,r),(0,2,True,True,False))
 def test_side_runnerup_true(self):
  old,t,a,s,r=m.classify([.45,.20,.35],[.29,.25,.46]); self.assertEqual((old,t,a,s,r),(0,2,True,True,True))
 def test_draw_to_side_not_candidate(self):
  old,t,a,s,r=m.classify([.34,.40,.26],[.42,.31,.27]); self.assertTrue(a); self.assertFalse(s); self.assertFalse(r)
 def test_draw_target_excluded(self):
  old,t,a,s,r=m.classify([.45,.30,.25],[.30,.42,.28]); self.assertFalse(a); self.assertFalse(s); self.assertFalse(r)
 def test_projection_exact(self):
  p,r=m.project(V324,[.45,.20,.35],2,1e-9); self.assertTrue(r['executed']); self.assertEqual(V324.top1(p),2)
 def test_weak_floor(self):
  b=[.45,.20,.35]; p,_=m.project(V324,b,2,1e-9); self.assertGreaterEqual(p[2]+1e-12,b[2])
 def test_contract_zero_parameter_clean(self):
  c=json.loads((ROOT/'RUNNERUP_SINGLE_BOUNDARY_CONFIRM_CONTRACT.json').read_text()); self.assertTrue(c['mechanism']['no_learning'] and c['mechanism']['no_parameter_fit'] and c['mechanism']['no_numeric_threshold'] and c['mechanism']['no_grid']); self.assertEqual({x['code'] for x in c['data']['leagues']},{'E2','E3','SC1','SC2','SC3'}); self.assertIn('confirm_unopened.txt',c['data']['cleanliness_guard'])
if __name__=='__main__':unittest.main()
