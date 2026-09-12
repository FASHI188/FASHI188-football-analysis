from __future__ import annotations
import json, math, pathlib, tempfile, unittest
import v3_c3_score_matrix_development_v1 as d

HERE=pathlib.Path(__file__).resolve().parent
CONTRACT=json.loads((HERE/'v3_c3_score_matrix_development_contract_v1.json').read_text())

def cells(vals): return [{'home_goals':h,'away_goals':a,'probability':p} for h,a,p in vals]
V1=cells([(0,0,.15),(1,0,.20),(0,1,.10),(1,1,.20),(2,0,.10),(0,2,.10),(2,1,.06),(1,2,.05),(2,2,.04)])
XG=cells([(0,0,.12),(1,0,.13),(0,1,.17),(1,1,.18),(2,0,.07),(0,2,.13),(2,1,.05),(1,2,.09),(2,2,.06)])

class ContractTests(unittest.TestCase):
    def test_01_contract(self):
        with tempfile.TemporaryDirectory() as td:
            p=pathlib.Path(td)/'c.json'; p.write_text(json.dumps(CONTRACT)); self.assertEqual(d.load_contract(p)['status'],'POST_VIEW_DEVELOPMENT_LOCKED')
    def test_02_exact_base(self): self.assertEqual(CONTRACT['exact_base'],d.EXPECTED_BASE)
    def test_03_source_post_view(self): self.assertEqual(CONTRACT['source']['classification'],'POST_VIEW_DEVELOPMENT_ONLY')
    def test_04_protected_forbidden(self): self.assertTrue(CONTRACT['source']['protected_2023_cohort_forbidden']); self.assertTrue(CONTRACT['source']['historical_confirmation_2024_25_forbidden'])
    def test_05_counts_locked(self): self.assertEqual((CONTRACT['source']['expected_warmup_n'],CONTRACT['source']['expected_fit_n'],CONTRACT['source']['expected_development_eval_n']),(5477,3551,5478))
    def test_06_single_beta(self): self.assertEqual(CONTRACT['prereg']['parameter_count'],1); self.assertEqual(CONTRACT['prereg']['parameter'],'beta')
    def test_07_optimizer_locked(self):
        o=CONTRACT['optimizer']; self.assertEqual(o['candidate_beta_bounds'],[-1.0,1.0]); self.assertEqual(o['derivative_tolerance'],1e-12); self.assertFalse(o['grid_search']); self.assertFalse(o['restarts']); self.assertFalse(o['refit_on_development_eval'])
    def test_08_forbidden_all_true(self): self.assertTrue(all(CONTRACT['forbidden_changes'].values()))
    def test_09_fresh_not_enrolled(self): self.assertTrue(CONTRACT['required_n']['fresh_confirmation_not_enrolled_in_this_batch'])
    def test_10_inactive_zero(self): self.assertEqual(CONTRACT['inactive'],{'status':'NOT_AVAILABLE','weight':0,'matrix_delta':0,'data_ready':False})

class MathTests(unittest.TestCase):
    def test_11_mix_normalized(self): self.assertAlmostEqual(math.fsum(d._mix(V1,XG).values()),1,14)
    def test_12_support(self): self.assertEqual(set(d._mix(V1,XG)),set(d.tilt(V1,XG,.4)))
    def test_13_beta_zero_exact(self): self.assertEqual(d._mix(V1,XG),d.tilt(V1,XG,0))
    def test_14_pt_preserved(self):
        q=d._mix(V1,XG); c=d.tilt(V1,XG,.8)
        for t in {sum(k) for k in q}: self.assertLessEqual(abs(sum(v for k,v in q.items() if sum(k)==t)-sum(v for k,v in c.items() if sum(k)==t)),5e-12)
    def test_15_sign_only_differs_when_magnitude_not_one(self): self.assertNotEqual(d.tilt(V1,XG,.5),d.tilt(V1,XG,.5,sign_only=True))
    def test_16_fit_datum(self):
        x=d.fit_datum(V1,XG,1,1,'f','c'); self.assertEqual(x.actual_margin,0); self.assertTrue(math.isfinite(x.baseline_conditional_nll))
    def _synthetic(self,true_beta=.35,n=500):
        base=d.fit_datum(V1,XG,1,0,'f','c'); out=[]
        margins=[m for m,_ in base.conditional_margin_probs]; probs=[p for _,p in base.conditional_margin_probs]; s=base.signal
        raw=[p*math.exp(true_beta*s*m) for m,p in zip(margins,probs)]; z=sum(raw); tilted=[x/z for x in raw]
        # deterministic quantile-style replication avoids randomness in permanent tests
        acc=[]
        for m,p in zip(margins,tilted): acc.extend([m]*max(1,round(p*n)))
        for i,m in enumerate(acc): out.append(d.FitDatum(f'f{i}','c',s,m,base.conditional_margin_probs,base.baseline_conditional_nll))
        return out
    def test_17_bisection_recovers_interior(self):
        data=self._synthetic(.35,2000); f=d.fit_beta(data); self.assertFalse(f['boundary_hit']); self.assertLess(abs(f['beta']-.35),.08)
    def test_18_fit_improves_own_objective(self):
        data=self._synthetic(.45,1500); f=d.fit_beta(data); self.assertLessEqual(f['candidate_mean_nll'],f['baseline_mean_nll'])
    def test_19_boundary_detection(self):
        base=d.fit_datum(V1,XG,2,0,'f','c'); data=[base]*200; f=d.fit_beta(data); self.assertTrue(f['boundary_hit'])
    def test_20_metrics_finite(self):
        m=d.matrix_metrics(d._mix(V1,XG),1,1); self.assertTrue(all(math.isfinite(v) for v in m.values()))
    def test_21_actual_outside_support_rejected(self):
        with self.assertRaises(d.DevelopmentError): d.fit_datum(V1,XG,8,8,'f','c')
    def test_22_nonfinite_beta_rejected(self):
        with self.assertRaises(d.DevelopmentError): d.tilt(V1,XG,float('nan'))
    def test_23_support_mismatch_rejected(self):
        with self.assertRaises(d.DevelopmentError): d._mix(V1,XG[:-1])
    def test_24_required_n_formula_floor(self):
        sigma=.001; n=max(1000,math.ceil(((1.959964+.841621)*sigma/.003)**2)); self.assertEqual(n,1000)
    def test_25_competition_map_big5(self): self.assertEqual(set(d.COMPETITION_MAP),set(d.BIG5))
    def test_26_seasons_disjoint(self): self.assertFalse(set(d.WARMUP_SEASONS)&set(d.FIT_SEASONS)); self.assertFalse(set(d.FIT_SEASONS)&set(d.EVAL_SEASONS))
    def test_27_eval_after_fit(self): self.assertLess(max(d.FIT_SEASONS),min(d.EVAL_SEASONS))
    def test_28_no_2023(self): self.assertLess(max(d.WARMUP_SEASONS+d.FIT_SEASONS+d.EVAL_SEASONS),2023)

if __name__=='__main__': unittest.main(verbosity=2)
