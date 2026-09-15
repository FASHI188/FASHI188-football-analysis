#!/usr/bin/env python3
import unittest
import nova_n1_common_v1 as c
import nova_n1_pit_replay_v1 as p
import nova_n1_residual_math_v1 as m

class NovaN1DevelopmentTests(unittest.TestCase):
    def test_season_and_outcome(self):
        self.assertEqual(c.season_label(2019), "2019/20")
        self.assertEqual([p.outcome(1,0),p.outcome(1,1),p.outcome(0,1)],[0,1,2])
    def test_dpi_vector_contract(self):
        raw={"D1":1.0,"D2":2.0,"P1":3.0,"P2":4.0}
        self.assertEqual(m.vectorize(raw,"DPI",[0]*4,[1]*4),[1,2,3,4,3,4,6,8])
    def test_zero_residual_preserves_formal_probabilities(self):
        q,_=m.softmax_offset([.5,.3,.2],[1.0,-1.0],[0.0]*6)
        self.assertLess(max(abs(a-b) for a,b in zip(q,[.5,.3,.2])),1e-12)
    def test_optimizer_exact_baseline_optimum(self):
        samples=[]
        for y,n in enumerate((50,30,20)):
            samples += [([0.0],[.5,.3,.2],y)]*n
        fit=m.fit_residual(samples)
        self.assertTrue(fit["converged"])
        self.assertEqual(fit["iterations"],0)
        self.assertLessEqual(fit["grad_inf"],c.GRAD_TOL)
    def test_stable_armijo_delta_matches_objective_change(self):
        samples=[]
        for y,n in enumerate((51,29,20)):
            samples += [([0.25],[.5,.3,.2],y)]*n
        theta=[0.02,-0.03,-0.01,0.04]
        _,g=m.objective_grad(samples,theta)
        cand=[v-1e-6*gg for v,gg in zip(theta,g)]
        loss,_=m.objective_grad(samples,theta)
        closs,_=m.objective_grad(samples,cand)
        delta=m.armijo_objective_delta(samples,theta,cand)
        self.assertAlmostEqual(delta,closs-loss,places=11)
        self.assertLess(delta,0.0)

    def test_matrix_projection_identity(self):
        mat=[(0,0,.3),(1,0,.4),(0,1,.3)]
        out=m.project_matrix(mat,[0.0,0.0,0.0])
        self.assertLess(max(abs(a[2]-b[2]) for a,b in zip(mat,out)),1e-12)
    def test_source_binding_constants(self):
        self.assertEqual(c.EXPECTED_ARTIFACT_ID,9798682425)
        self.assertEqual(c.EXPECTED_PRODUCER_RUN_ID,33503552079)
        self.assertEqual(c.ADOPTED_MECHANICS_HEAD,"5128b76a358b5c0ec5e02869be2f1e818fea3526")

if __name__=="__main__":
    unittest.main()
