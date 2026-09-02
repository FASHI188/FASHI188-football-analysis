import importlib.util,sys,unittest,json,pathlib,math
P=str(pathlib.Path(__file__).with_name('historical_fusion_v3_1_1_joint_score.py'))
spec=importlib.util.spec_from_file_location('js_tested',P); js=importlib.util.module_from_spec(spec); sys.modules['js_tested']=js; spec.loader.exec_module(js)

class JointScoreTests(unittest.TestCase):
    def test_poisson_15x15_normalized(self):
        m=js.poisson_matrix(1.4,1.1); self.assertEqual(len(m),15); self.assertTrue(all(len(r)==15 for r in m)); self.assertTrue(js.matrix_valid(m)); self.assertLessEqual(abs(sum(sum(r) for r in m)-1),1e-12)
    def test_integrate_is_only_three_region_mass(self):
        m=js.poisson_matrix(1.5,.9); p=js.integrate(m); self.assertLess(abs(sum(p)-1),1e-12); self.assertTrue(all(x>0 for x in p))
    def test_projection_hits_target(self):
        m=js.poisson_matrix(1.3,1.2); t=[.44,.30,.26]; q=js.project_regions(m,t); self.assertTrue(js.matrix_valid(q)); got=js.integrate(q); self.assertLess(max(abs(a-b) for a,b in zip(got,t)),1e-12)
    def test_projection_preserves_within_region_ratios(self):
        m=js.poisson_matrix(1.6,1.0); q=js.project_regions(m,[.50,.27,.23]); a=m[2][0]/m[1][0]; b=q[2][0]/q[1][0]; self.assertAlmostEqual(a,b,12)
    def test_dc_single_mechanism_valid(self):
        m=js.poisson_matrix(1.4,1.1); q=js.dc_shape(m,1.4,1.1,-.08); self.assertTrue(js.matrix_valid(q)); self.assertNotEqual(q[0][0],m[0][0])
    def test_bivpois_zero_shared_equals_independent(self):
        a=js.poisson_matrix(1.4,1.1); b=js.bivpois_matrix(1.4,1.1,0.0); self.assertLess(max(abs(a[h][k]-b[h][k]) for h in range(15) for k in range(15)),1e-12)
    def test_bivpois_positive_shared_has_positive_covariance(self):
        m=js.bivpois_matrix(1.5,1.2,.10); hp=[sum(m[h][a] for a in range(15)) for h in range(15)]; ap=[sum(m[h][a] for h in range(15)) for a in range(15)]; mh=sum(h*hp[h] for h in range(15)); ma=sum(a*ap[a] for a in range(15)); cov=sum(h*a*m[h][a] for h in range(15) for a in range(15))-mh*ma; self.assertGreater(cov,0)
    def test_negative_probability_rejected(self):
        m=js.poisson_matrix(1.0,1.0); m[0][0]=-1e-3; self.assertFalse(js.matrix_valid(m))
    def test_fallback_candidate_matrix_is_exact_base(self):
        r={'v1_mu_home':1.2,'v1_mu_away':.8,'xg_mu_home':1.2,'xg_mu_away':.8,'fallback_exact_v1':True,'fusion':{'mean_home':1.2,'mean_away':.8}}; b=js.base_matrix(r); q=js.candidate_matrix('V3.1.1-A',{},r,[.5,.3,.2]); self.assertEqual(js.canon(b),js.canon(q))
    def test_active_base_is_cellwise_75_25(self):
        r={'v1_mu_home':1.1,'v1_mu_away':1.0,'xg_mu_home':1.6,'xg_mu_away':.8,'fallback_exact_v1':False}; b=js.base_matrix(r); v=js.poisson_matrix(1.1,1.0); x=js.poisson_matrix(1.6,.8); raw=js.normalize_matrix([[.25*v[h][a]+.75*x[h][a] for a in range(15)] for h in range(15)]); self.assertEqual(js.canon(b),js.canon(raw))
    def test_score_diagnostics_exposes_required_layers(self):
        m=js.poisson_matrix(1.3,1.0); r={'fixture_id':'f','home_goals':1,'away_goals':1}; d=js.score_diagnostics([r],{'f':m});
        for k in ['exact_score_logloss','home_goal_marginal','away_goal_marginal','total_goals','score_00','score_11','score_22','mean_draw_diagonal_mass','mean_predicted_conditional_covariance','low_total_le2','high_total_ge5']: self.assertIn(k,d)
    def test_contract_joint_hard_requirements_frozen(self):
        c=json.loads(pathlib.Path(__file__).with_name('V3_1_1_JOINT_SCORE_CONTRACT.json').read_text()); self.assertEqual(c['status'],'FROZEN_BEFORE_AUTHORITATIVE_JOINT_SCORE_RESEARCH'); self.assertEqual([x['id'] for x in c['candidates']],['V3.1.1-A','V3.1.1-B','V3.1.1-C']); self.assertEqual(c['matrix_contract']['cell_count'],225); self.assertEqual(c['matrix_contract']['one_x_two_source'],'INTEGRATE_FINAL_MATRIX_ONLY'); self.assertEqual(c['score_gates']['exact_score_logloss_delta_max'],0.0); self.assertTrue(c['one_x_two_gates']['fallback_exact_formal_v2'])

if __name__=='__main__': unittest.main()
