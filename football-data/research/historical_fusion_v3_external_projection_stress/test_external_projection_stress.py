#!/usr/bin/env python3
import importlib.util,pathlib,sys,unittest
ROOT=pathlib.Path(__file__).parent
SPEC=importlib.util.spec_from_file_location('eps',ROOT/'external_projection_stress.py'); m=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(m)
V324_PATH=ROOT.parent/'historical_fusion_v3_2_4_minimal_boundary_projection'/'v3_2_4_segment4.py'
V324=m.loadmod('eps_v324_test',V324_PATH)
class T(unittest.TestCase):
    def test_norm(self):
        p=m.norm_odds([2,4,4]); self.assertAlmostEqual(sum(p),1.0); self.assertEqual(m.top1(p),0)
    def test_same_target_no_projection_needed(self):
        p=[0.5,0.3,0.2]; self.assertEqual(m.top1(p),0)
    def test_exact_v324_projection_creates_target(self):
        b=[0.45,0.35,0.20]; p,r=V324.minimum_boundary_projection(b,1,2,1e-9); self.assertTrue(r['executed']); self.assertEqual(V324.top1(p),1)
    def test_weak_floor(self):
        b=[0.45,0.35,0.20]; p,r=V324.minimum_boundary_projection(b,1,2,1e-9); self.assertGreaterEqual(p[2]+1e-12,b[2])
    def test_sum_one(self):
        b=[0.45,0.35,0.20]; p,_=V324.minimum_boundary_projection(b,1,2,1e-9); self.assertAlmostEqual(sum(p),1.0,places=10)
    def test_metric_identity(self):
        rows=[{'open':[0.5,0.3,0.2],'candidate':[0.5,0.3,0.2],'y':0}]; self.assertEqual(m.deltas(m.metric(rows,'open'),m.metric(rows,'candidate'))['logloss'],0.0)
    def test_contract_no_learning(self):
        import json; c=json.loads((ROOT/'EXTERNAL_PROJECTION_STRESS_CONTRACT.json').read_text()); self.assertTrue(c['mechanism']['no_learning']); self.assertTrue(c['mechanism']['no_parameter_fit']); self.assertTrue(c['mechanism']['no_threshold_grid'])
if __name__=='__main__': unittest.main()
