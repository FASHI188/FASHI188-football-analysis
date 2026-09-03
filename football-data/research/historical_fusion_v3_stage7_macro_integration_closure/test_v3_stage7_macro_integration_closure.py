#!/usr/bin/env python3
import unittest, tempfile, pathlib, json, sys, os
sys.path.insert(0,str(pathlib.Path(__file__).parent))
import v3_stage7_macro_integration_closure as m

ROOT=pathlib.Path(__file__).parent
class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract=ROOT/'V3_STAGE7_MACRO_INTEGRATION_CLOSURE_CONTRACT.json'
        cls.v311=pathlib.Path(os.environ.get('STAGE7_V311_ROOT','_inputs/v311'))
        cls.v324=pathlib.Path(os.environ.get('STAGE7_V324_ROOT','_inputs/v324'))
        cls.usr1=pathlib.Path(os.environ.get('STAGE7_USR1_ROOT','_inputs/usr1'))
        cls.v326=pathlib.Path(os.environ.get('STAGE7_V326_ROOT','_inputs/v326'))
    def run_close(self):
        td=tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup)
        r=m.close(self.contract,self.v311,self.v324,self.usr1,self.v326,td.name); return r,pathlib.Path(td.name)
    def test_contract_frozen(self):
        c=json.loads(self.contract.read_text()); self.assertEqual(c['status'],'FROZEN_BEFORE_STAGE7_CLOSURE_AUDIT')
    def test_no_new_science(self):
        c=json.loads(self.contract.read_text()); s=c['original_stage7_composition']; self.assertTrue(all(s[k]=='NONE' for k in ('new_parameters','new_thresholds','new_selector','new_score_shape_mechanism')))
    def test_usr1_embedded(self):
        c=json.loads((self.v311/'contracts/V3_1_1_JOINT_SCORE_CONTRACT.json').read_text()); self.assertEqual(c['frozen_inputs']['usr1']['artifact_id'],9848912219)
    def test_v324_uses_v311(self):
        c=json.loads((self.v324/'contracts/V3_2_4_MINIMAL_BOUNDARY_PROJECTION_CONTRACT.json').read_text()); self.assertEqual(c['lineage']['v3_1_1_head'],'a90762a97515f3edd564e8ad204db0d0d4231494')
    def test_v326_zero_weight_contract(self):
        c=json.loads(self.contract.read_text()); self.assertEqual(c['frozen_lineage']['v3_2_6']['prediction_weight'],0)
    def test_equivalence(self):
        r,_=self.run_close(); self.assertTrue(r['equivalent_to_v324']); self.assertEqual(r['status'],m.EQ_STATUS)
    def test_inherits_only_fold_ll_failure(self):
        r,_=self.run_close(); self.assertEqual(r['inherited_failed_gates'],['fold_ll_gate']); self.assertEqual(r['inherited_rolling_2021_2022']['deltas']['fold_logloss_nondegrade_n'],4)
if __name__=='__main__': unittest.main()
