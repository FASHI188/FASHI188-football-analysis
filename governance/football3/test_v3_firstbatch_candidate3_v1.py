import math
import unittest

import validate_v3_firstbatch_candidate3_v1 as v


def transform(total_mass, weak_mass, draw_mass, gamma, z):
    fav_mass = total_mass - weak_mass - draw_mass
    if total_mass < 0 or weak_mass < 0 or draw_mass < 0 or fav_mass < 0:
        raise ValueError
    if gamma == 0 or draw_mass == 0 or fav_mass == 0:
        return weak_mass, draw_mass, fav_mass
    logit = math.log(draw_mass / fav_mass) + gamma * z
    draw_new = (total_mass - weak_mass) / (1.0 + math.exp(-logit))
    fav_new = total_mass - weak_mass - draw_new
    return weak_mass, draw_new, fav_new


class ContractTests(unittest.TestCase):
    def test_contract_validator(self):
        out = v.validate(*v.load())
        self.assertEqual(out['status'], 'PASS')
        self.assertEqual(out['usable_n'], 0)

    def test_gamma_zero_exact_parent(self):
        w,d,f = transform(.40,.09,.12,0.0,.8)
        self.assertAlmostEqual(w,.09,places=15)
        self.assertAlmostEqual(d,.12,places=15)
        self.assertAlmostEqual(f,.19,places=15)

    def test_total_and_weak_mass_invariant(self):
        w,d,f = transform(.40,.09,.12,.7,.8)
        self.assertAlmostEqual(w, .09, places=15)
        self.assertAlmostEqual(w+d+f, .40, places=15)
        self.assertGreater(d, .12)

    def test_positive_tilt_moves_only_draw_favorite_partition(self):
        w0,d0,f0 = transform(.30,.05,.08,0.0,.5)
        w1,d1,f1 = transform(.30,.05,.08,1.0,.5)
        self.assertEqual(w0,w1)
        self.assertGreater(d1,d0)
        self.assertLess(f1,f0)

    def test_no_draw_or_favorite_is_noop(self):
        self.assertEqual(transform(.2,.1,0.0,.8,.9), (.1,0.0,.1))
        self.assertEqual(transform(.2,.1,.1,.8,.9), (.1,.1,0.0))

    def test_data_gate_is_fail_closed(self):
        c,a = v.load()
        self.assertFalse(c['coverage_decision']['data_ready'])
        self.assertEqual(c['coverage_decision']['terminal'], 'STOP_DATA_COVERAGE_PRE_DEVELOPMENT')
        self.assertEqual(a['result_values_read'], 0)

    def test_old_prototype_cannot_be_reused(self):
        c,_ = v.load()
        self.assertFalse(c['legacy_evidence']['code_reuse_allowed'])
        self.assertFalse(c['legacy_evidence']['parameter_reuse_allowed'])
        self.assertFalse(c['legacy_evidence']['result_reuse_as_confirmation_allowed'])


if __name__ == '__main__':
    unittest.main()
