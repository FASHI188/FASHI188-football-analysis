#!/usr/bin/env python3
import importlib.util, json, pathlib, unittest
P=pathlib.Path(__file__).with_name('v3_2_4_segment4.py')
spec=importlib.util.spec_from_file_location('v324',P); v=importlib.util.module_from_spec(spec); spec.loader.exec_module(v)
class Tests(unittest.TestCase):
    def test_same_argmax_identity(self):
        b=[.45,.30,.25]; p,d=v.minimum_boundary_projection(b,0,2); self.assertEqual(p,b); self.assertFalse(d['executed'])
    def test_two_class_minimum_flip(self):
        b=[.40,.39,.21]; p,d=v.minimum_boundary_projection(b,1,2,1e-9); self.assertTrue(d['executed']); self.assertEqual(v.top1(p),1); self.assertAlmostEqual(p[2],b[2],14); self.assertAlmostEqual(sum(p),1,14)
    def test_three_class_binding(self):
        b=[.34,.335,.325]; p,d=v.minimum_boundary_projection(b,2,2,1e-9); self.assertTrue(d['executed']); self.assertEqual(v.top1(p),2); self.assertAlmostEqual(sum(p),1,14)
    def test_weak_floor_fallback(self):
        b=[.40,.25,.35]; p,d=v.minimum_boundary_projection(b,1,2,1e-9); self.assertFalse(d['executed']); self.assertEqual(p,b)
    def test_target_weak_can_rise(self):
        b=[.40,.31,.29]; p,d=v.minimum_boundary_projection(b,2,2,1e-9); self.assertTrue(d['executed']); self.assertGreaterEqual(p[2],b[2]); self.assertEqual(v.top1(p),2)
    def test_deterministic(self):
        b=[.39,.37,.24]; a=v.minimum_boundary_projection(b,1,2,1e-9); c=v.minimum_boundary_projection(b,1,2,1e-9); self.assertEqual(a,c)
    def test_contract_frozen(self):
        c=json.loads(pathlib.Path(__file__).with_name('V3_2_4_MINIMAL_BOUNDARY_PROJECTION_CONTRACT.json').read_text()); self.assertEqual(c['status'],'FROZEN_BEFORE_V3_2_4_TARGET_SCORING'); self.assertTrue(c['direction_generator']['no_parameter_selection_in_v3_2_4']); self.assertEqual(c['projection']['new_learned_gate'],'NONE'); self.assertIn('2024_25_or_2025_26_3504_scoring_in_this_segment',c['forbidden'])
if __name__=='__main__': unittest.main()
