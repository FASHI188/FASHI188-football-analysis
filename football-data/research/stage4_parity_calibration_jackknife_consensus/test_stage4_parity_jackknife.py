#!/usr/bin/env python3
import unittest
import stage4_parity_jackknife as m
class TestStage4(unittest.TestCase):
    def test_top1_tie_break(self): self.assertEqual(m.top1([0.4,0.4,0.2]),0)
    def test_projection_changes_top1_minimally(self):
        p,rec=m.minimum_boundary_projection([0.41,0.39,0.20],1,2,1e-9); self.assertTrue(rec['executed']); self.assertEqual(m.top1(p),1); self.assertAlmostEqual(sum(p),1.0,places=12); self.assertGreaterEqual(p[2],0.20-1e-12)
    def test_projection_respects_weak_floor(self):
        b=[0.45,0.35,0.20]; p,rec=m.minimum_boundary_projection(b,2,2,1e-9); self.assertGreaterEqual(p[2],b[2]-1e-12)
    def test_consensus_unanimous(self):
        ok,reason=m.jackknife_consensus(1,True,[1,1,1],[True,True,True],2); self.assertTrue(ok); self.assertEqual(reason,'unanimous')
    def test_consensus_disagreement_blocks(self):
        ok,reason=m.jackknife_consensus(1,True,[1,0,1],[True,True,True],2); self.assertFalse(ok); self.assertEqual(reason,'jackknife_direction_disagreement')
    def test_consensus_ineligible_blocks(self):
        ok,reason=m.jackknife_consensus(1,True,[1,1],[True,False],2); self.assertFalse(ok); self.assertEqual(reason,'jackknife_not_eligible')
    def test_insufficient_units_blocks(self):
        ok,reason=m.jackknife_consensus(1,True,[1],[True],2); self.assertFalse(ok); self.assertEqual(reason,'insufficient_training_seasons')
if __name__=='__main__': unittest.main()
