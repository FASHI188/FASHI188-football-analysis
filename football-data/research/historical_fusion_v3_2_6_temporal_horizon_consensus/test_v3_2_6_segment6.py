import importlib.util, json, pathlib, unittest
P=pathlib.Path(__file__).with_name('v3_2_6_segment6.py'); spec=importlib.util.spec_from_file_location('v326',P); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
C=json.loads(pathlib.Path(__file__).with_name('V3_2_6_TEMPORAL_HORIZON_CONSENSUS_CONTRACT.json').read_text())
class TestV326(unittest.TestCase):
 def test_contract_frozen(self): self.assertEqual(C['status'],'FROZEN_BEFORE_V3_2_6_TARGET_SCORING'); self.assertEqual(C['consensus']['threshold_grid'],'NONE')
 def test_horizons_exact(self): self.assertEqual(C['consensus']['training_half_life_seasons'],[1.0,2.0,4.0]); self.assertFalse(C['consensus']['probability_averaging'])
 def test_unanimous_executes(self):
  rows=[{'fixture_id':'x'}]; b={'x':[.4,.3,.3]}; sp={'x':[.349999999,.3,.350000001]}; sd={'x':{'v324_executed_switch':True}}; q={h:{'x':[.34,.3,.36]} for h in (1.,2.,4.)}; d={h:{'x':{'eligible':True}} for h in (1.,2.,4.)}; p,g,s=m.apply_consensus(rows,b,sp,sd,q,d,(1.,2.,4.)); self.assertTrue(g['x']['v326_executed_switch']); self.assertEqual(s['unanimous_consensus_switch_n'],1)
 def test_disagreement_blocks(self):
  rows=[{'fixture_id':'x'}]; b={'x':[.4,.3,.3]}; sp={'x':[.349999999,.3,.350000001]}; sd={'x':{'v324_executed_switch':True}}; q={1.:{'x':[.34,.3,.36]},2.:{'x':[.36,.3,.34]},4.:{'x':[.34,.3,.36]}}; d={h:{'x':{'eligible':True}} for h in q}; p,g,s=m.apply_consensus(rows,b,sp,sd,q,d,(1.,2.,4.)); self.assertFalse(g['x']['v326_executed_switch']); self.assertEqual(p['x'],b['x'])
 def test_no_shadow_identity(self):
  rows=[{'fixture_id':'x'}]; b={'x':[.4,.3,.3]}; sp={'x':[.4,.3,.3]}; sd={'x':{'v324_executed_switch':False}}; q={h:{'x':[.4,.3,.3]} for h in (1.,2.,4.)}; d={h:{'x':{'eligible':True}} for h in q}; p,g,s=m.apply_consensus(rows,b,sp,sd,q,d,(1.,2.,4.)); self.assertEqual(p['x'],b['x']); self.assertFalse(g['x']['v326_executed_switch'])
 def test_draw_diagnostic(self):
  rows=[{'fixture_id':'x'}]; b={'x':[.4,.3,.3]}; sp={'x':[.333,.334,.333]}; sd={'x':{'v324_executed_switch':True}}; q={h:{'x':[.33,.34,.33]} for h in (1.,2.,4.)}; d={h:{'x':{'eligible':True}} for h in q}; p,g,s=m.apply_consensus(rows,b,sp,sd,q,d,(1.,2.,4.)); self.assertEqual(s['unanimous_draw_target_n'],1)
 def test_future_forbidden(self): self.assertTrue(C['data_roles']['2024_25_and_2025_26_3504'].startswith('FORBIDDEN')); self.assertIn('post_view_rescue_tuning',C['forbidden'])
if __name__=='__main__': unittest.main()
