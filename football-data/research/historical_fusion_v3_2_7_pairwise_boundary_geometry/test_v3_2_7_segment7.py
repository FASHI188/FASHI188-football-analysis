import importlib.util, json, pathlib, unittest
P=pathlib.Path(__file__).with_name('v3_2_7_segment7.py')
spec=importlib.util.spec_from_file_location('v327',P); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

class TestV327(unittest.TestCase):
    def setUp(self):
        self.c=json.loads(pathlib.Path(__file__).with_name('V3_2_7_PAIRWISE_BOUNDARY_GEOMETRY_CONTRACT.json').read_text())

    def test_contract_frozen_before_scoring(self):
        self.assertEqual(self.c['status'],'FROZEN_BEFORE_V3_2_7_TARGET_SCORING')
        self.assertTrue(self.c['research_only']); self.assertFalse(self.c['promotion_allowed'])

    def test_no_numeric_threshold_or_grid(self):
        g=self.c['geometry_safety']
        self.assertEqual(g['numeric_risk_threshold'],'NONE'); self.assertEqual(g['threshold_grid'],'NONE')
        self.assertFalse(g['result_feedback']); self.assertFalse(g['temporal_consensus_gate'])

    def test_pairwise_geometry_definition(self):
        self.assertTrue(m.is_pairwise_local_projection({'executed':True,'binding_competitors':[0]},0))
        self.assertFalse(m.is_pairwise_local_projection({'executed':True,'binding_competitors':[0,2]},0))
        self.assertFalse(m.is_pairwise_local_projection({'executed':False,'binding_competitors':[0]},0))
        self.assertFalse(m.is_pairwise_local_projection({'executed':True,'binding_competitors':[2]},0))

    def test_frozen_v324_base_unchanged(self):
        p=self.c['frozen_base_candidate']
        self.assertEqual(p['direction_family'],'T2'); self.assertEqual(p['projection_epsilon'],1e-9)
        self.assertEqual(p['frozen_t2_params'],{'cap':0.02,'draw_lambda':16.0,'draw_scale':0.25,'half_life':1.0,'side_lambda':4.0,'side_scale':0.25,'support_tau':1.5})
        self.assertFalse(p['change_v3_2_4_direction_or_projection'])

    def test_hard_gates_not_lowered(self):
        g=self.c['hard_gates_2021_2022']
        self.assertEqual(g['global_top1_delta_min'],0.001); self.assertEqual(g['parity_top1_delta_min'],0.005)
        self.assertEqual(g['fold_logloss_nondegrade_min'],6); self.assertEqual(g['fold_top1_nondegrade_min'],5)
        self.assertEqual(g['global_logloss_delta_max'],0.0); self.assertEqual(g['global_brier_delta_max'],0.0); self.assertEqual(g['global_rps_delta_max'],0.0)

    def test_future_data_closed(self):
        self.assertTrue(self.c['data_roles']['2023'].startswith('CLOSED'))
        self.assertTrue(self.c['data_roles']['2024_25_and_2025_26_3504'].startswith('FORBIDDEN'))
        self.assertIn('2024_25_or_2025_26_3504_scoring_in_this_segment',self.c['forbidden'])

    def test_no_postview_rescue_paths(self):
        f=set(self.c['forbidden'])
        for x in ('tv_threshold','l2_threshold','baseline_margin_threshold','raw_t2_margin_threshold','threshold_grid','change_pairwise_cardinality_after_scoring','combine_v3_2_6_temporal_consensus_after_scoring','post_view_rescue_tuning'):
            self.assertIn(x,f)

if __name__=='__main__': unittest.main()
