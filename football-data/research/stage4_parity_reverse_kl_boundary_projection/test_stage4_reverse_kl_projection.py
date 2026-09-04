#!/usr/bin/env python3
import importlib.util, json, math, pathlib, unittest
P=pathlib.Path(__file__).with_name('stage4_reverse_kl_projection.py')
spec=importlib.util.spec_from_file_location('stage4kl',P); v=importlib.util.module_from_spec(spec); spec.loader.exec_module(v)

class Tests(unittest.TestCase):
    def test_same_argmax_identity(self):
        b=[.45,.30,.25]; p,d=v.minimum_reverse_kl_boundary_projection(b,0,2); self.assertEqual(p,b); self.assertFalse(d['executed'])
    def test_pair_flip_respects_free_weak_floor(self):
        b=[.40,.39,.21]; p,d=v.minimum_reverse_kl_boundary_projection(b,1,2,1e-9); self.assertTrue(d['executed']); self.assertEqual(v.top1(p),1); self.assertAlmostEqual(p[2],b[2],12); self.assertAlmostEqual(sum(p),1,12)
    def test_three_class_binding(self):
        b=[.34,.335,.325]; p,d=v.minimum_reverse_kl_boundary_projection(b,2,2,1e-9); self.assertTrue(d['executed']); self.assertEqual(v.top1(p),2); self.assertAlmostEqual(sum(p),1,12)
    def test_weak_floor_infeasible_fallback(self):
        b=[.40,.25,.35]; p,d=v.minimum_reverse_kl_boundary_projection(b,1,2,1e-9); self.assertFalse(d['executed']); self.assertEqual(p,b)
    def test_target_weak_can_rise(self):
        b=[.40,.31,.29]; p,d=v.minimum_reverse_kl_boundary_projection(b,2,2,1e-9); self.assertTrue(d['executed']); self.assertGreaterEqual(p[2],b[2]); self.assertEqual(v.top1(p),2)
    def test_deterministic_and_nonnegative_kl(self):
        b=[.39,.37,.24]; a=v.minimum_reverse_kl_boundary_projection(b,1,2,1e-9); c=v.minimum_reverse_kl_boundary_projection(b,1,2,1e-9); self.assertEqual(a,c); self.assertGreaterEqual(a[1]['reverse_kl'],-1e-15)
    def test_contract_frozen(self):
        c=json.loads(pathlib.Path(__file__).with_name('STAGE4_PARITY_REVERSE_KL_BOUNDARY_PROJECTION_CONTRACT.json').read_text())
        self.assertEqual(c['status'],'FROZEN_BEFORE_STAGE4_TARGET_SCORING')
        self.assertEqual(c['projection']['name'],'DETERMINISTIC_MINIMUM_REVERSE_KL_TOP1_BOUNDARY_PROJECTION')
        self.assertEqual(c['projection']['numeric_threshold'],'NONE')
        self.assertFalse(c['projection']['result_feedback'])
        self.assertEqual(c['data_roles']['2023'],'CLOSED_IN_STAGE4')
        self.assertIn('2023_scoring_in_stage4',c['forbidden'])

if __name__=='__main__': unittest.main()
