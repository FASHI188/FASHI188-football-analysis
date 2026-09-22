import unittest
from nova_n9_tactical_shape_source_v1 import shape
from nova_n9_tactical_shape_oof_v1 import pair_features,state_features,metrics

class T(unittest.TestCase):
    def test_shape_valid(self):
        s=shape(['GK','DC','DC','DL','DR','DMC','DMC','AMC','AML','AMR','FW'])
        self.assertTrue(s['available']); self.assertEqual(s['starter_n'],11); self.assertEqual(len(s['vector']),8)
    def test_shape_invalid(self):
        self.assertFalse(shape(['GK']*10)['available'])
    def test_last(self):
        h=[{'vector':[4.,1.,2.,1.,1.,2.,1.,0.],'signature':'a'}]
        self.assertEqual(state_features(h,'R1_LAST_SHAPE'),h[0]['vector'])
    def test_w5_stability(self):
        h=[{'vector':[4.,1.,2.,1.,1.,2.,1.,0.],'signature':'a'},{'vector':[3.,2.,2.,1.,1.,2.,1.,0.],'signature':'b'}]
        x=state_features(h,'R3_W5_STABILITY'); self.assertEqual(len(x),10); self.assertEqual(x[-2],1.0)
    def test_matchup(self):
        self.assertEqual(len(pair_features([1.]*10,[2.]*10,'R4_MATCHUP_INTERACTION')),35)
    def test_metrics(self):
        self.assertEqual(metrics([[.6,.2,.2],[.2,.3,.5]],[0,2])['top1'],1.0)

if __name__=='__main__': unittest.main()
