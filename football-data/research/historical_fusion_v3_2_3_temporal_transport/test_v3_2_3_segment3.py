import unittest, importlib.util, pathlib, sys
P=pathlib.Path(__file__).with_name('v3_2_3_segment3.py')
spec=importlib.util.spec_from_file_location('seg',P); seg=importlib.util.module_from_spec(spec); sys.modules['seg']=seg; spec.loader.exec_module(seg)
class M: means=[0.0,0.0]; sds=[1.0,2.0]; cols=[0,1]
class TestV323(unittest.TestCase):
    def test_temporal_weight_monotone(self): self.assertGreater(seg.temporal_weight(1,2),seg.temporal_weight(2,2))
    def test_temporal_half_life_identity(self): self.assertAlmostEqual(seg.temporal_weight(2,2),0.5)
    def test_support_center(self): self.assertAlmostEqual(seg.rms_support(M(),[0,0],2.0),1.0)
    def test_support_distance(self): self.assertGreater(seg.rms_support(M(),[1,2],2.0),seg.rms_support(M(),[4,8],2.0))
    def test_support_tau(self): self.assertGreater(seg.rms_support(M(),[2,4],4.0),seg.rms_support(M(),[2,4],1.0))
    def test_grid_sizes(self):
        c={'frozen_grid':{'training_half_life_seasons':[1,2,4],'draw_ridge_lambda_multiplier':[8,16],'draw_residual_scale':[.25,.5],'side_ridge_lambda_multiplier':[4,8],'side_residual_scale':[.25,.5],'max_outcome_probability_abs_delta':[.02,.04],'support_tau_T2_only':[1.5,2.5,4]}}
        self.assertEqual(len(seg.params_grid(c,'T1')),96); self.assertEqual(len(seg.params_grid(c,'T2')),288)
    def test_transform(self): self.assertEqual(seg.model_transform(M(),[1,4]),[1.0,2.0])
if __name__=='__main__': unittest.main()
