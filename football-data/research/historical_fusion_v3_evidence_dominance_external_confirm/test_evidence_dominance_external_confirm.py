#!/usr/bin/env python3
import importlib.util,json,pathlib,unittest
ROOT=pathlib.Path(__file__).parent
SPEC=importlib.util.spec_from_file_location('ed',ROOT/'evidence_dominance_external_confirm.py'); m=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(m)
V324_PATH=ROOT.parent/'historical_fusion_v3_2_4_minimal_boundary_projection'/'v3_2_4_segment4.py'
V324=m.loadmod('ed_v324_test',V324_PATH)
class T(unittest.TestCase):
    def test_norm(self):
        p=m.norm_odds([2,4,4]); self.assertAlmostEqual(sum(p),1.0); self.assertEqual(m.top1(p),0)
    def test_dominance_true(self):
        op=[0.50,0.30,0.20]; cp=[0.20,0.31,0.49]; self.assertTrue(m.evidence_dominates(op,cp,0,2))
    def test_dominance_false(self):
        op=[0.45,0.35,0.20]; cp=[0.34,0.35,0.31]; self.assertFalse(m.evidence_dominates(op,cp,0,1))
    def test_exact_v324_projection(self):
        op=[0.45,0.35,0.20]; p,r=m.projected(V324,op,1,1e-9); self.assertTrue(r['executed']); self.assertEqual(V324.top1(p),1)
    def test_weak_floor(self):
        op=[0.45,0.35,0.20]; p,_=m.projected(V324,op,1,1e-9); self.assertGreaterEqual(p[2]+1e-12,op[2])
    def test_metric_identity(self):
        rows=[{'open':[0.5,0.3,0.2],'candidate':[0.5,0.3,0.2],'y':0}]; self.assertEqual(m.deltas(m.metric(rows,'open'),m.metric(rows,'candidate'))['logloss'],0.0)
    def test_contract_is_zero_parameter_and_untouched(self):
        c=json.loads((ROOT/'EVIDENCE_DOMINANCE_EXTERNAL_CONFIRM_CONTRACT.json').read_text()); mech=c['mechanism']; self.assertTrue(mech['no_learning'] and mech['no_parameter_fit'] and mech['no_threshold_grid'] and mech['no_tv_threshold']); self.assertEqual({x['code'] for x in c['data']['leagues']},{'E1','D2','I2','SP2','F2'}); self.assertFalse({x['code'] for x in c['data']['leagues']} & {'N1','P1','B1','SC0','T1','G1'})
if __name__=='__main__': unittest.main()
