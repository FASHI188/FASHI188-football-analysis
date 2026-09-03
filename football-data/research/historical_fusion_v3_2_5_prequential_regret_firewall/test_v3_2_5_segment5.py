import importlib.util, json, pathlib, unittest
P=pathlib.Path(__file__).with_name('v3_2_5_segment5.py')
spec=importlib.util.spec_from_file_location('v325',P); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
C=json.loads(pathlib.Path(__file__).with_name('V3_2_5_PREQUENTIAL_REGRET_FIREWALL_CONTRACT.json').read_text())

class TestV325(unittest.TestCase):
    def test_contract_frozen(self):
        self.assertEqual(C['status'],'FROZEN_BEFORE_V3_2_5_TARGET_SCORING'); self.assertEqual(C['firewall']['threshold'],0.0); self.assertEqual(C['firewall']['threshold_grid'],'NONE')
    def test_future_forbidden(self):
        self.assertTrue(C['data_roles']['2024_25_and_2025_26_3504'].startswith('FORBIDDEN')); self.assertIn('fold_id_as_feature',C['forbidden'])
    def test_result_goals(self):
        self.assertEqual(m.result_index({'home_goals':2,'away_goals':1}),0); self.assertEqual(m.result_index({'home_goals':1,'away_goals':1}),1); self.assertEqual(m.result_index({'home_goals':0,'away_goals':1}),2)
    def test_time_key(self):
        self.assertEqual(m.time_key({'date':'2021-01-02'}),'2021-01-02')
    def test_same_kickoff_no_leakage(self):
        rows=[{'fixture_id':'a','date':'2021-01-01','home_goals':0,'away_goals':1},{'fixture_id':'b','date':'2021-01-01','home_goals':0,'away_goals':1}]
        b={'a':[.4,.3,.3],'b':[.4,.3,.3]}; s={'a':[.399999999,.3,.300000001],'b':[.399999999,.3,.300000001]}
        d={k:{'v324_executed_switch':True,'v324_projection_tv':1e-9} for k in b}
        p,dd,st=m.apply_firewall(rows,b,s,d)
        self.assertTrue(dd['a']['v325_executed_switch']); self.assertTrue(dd['b']['v325_executed_switch'])
    def test_harm_closes_next_group(self):
        rows=[{'fixture_id':'a','date':'2021-01-01','home_goals':1,'away_goals':0},{'fixture_id':'b','date':'2021-01-02','home_goals':1,'away_goals':0}]
        b={'a':[.6,.2,.2],'b':[.6,.2,.2]}; s={'a':[.59,.2,.21],'b':[.59,.2,.21]}
        d={k:{'v324_executed_switch':True,'v324_projection_tv':.01} for k in b}
        p,dd,st=m.apply_firewall(rows,b,s,d)
        self.assertTrue(dd['a']['v325_executed_switch']); self.assertFalse(dd['b']['v325_executed_switch']); self.assertEqual(st['firewall_blocked_switch_n'],1)
    def test_identity_when_no_shadow_switch(self):
        rows=[{'fixture_id':'a','date':'2021-01-01','home_goals':1,'away_goals':0}]; b={'a':[.6,.2,.2]}; s={'a':[.6,.2,.2]}; d={'a':{'v324_executed_switch':False,'v324_projection_tv':0.0}}
        p,dd,st=m.apply_firewall(rows,b,s,d); self.assertEqual(p['a'],b['a']); self.assertFalse(dd['a']['v325_executed_switch'])

if __name__=='__main__': unittest.main()
