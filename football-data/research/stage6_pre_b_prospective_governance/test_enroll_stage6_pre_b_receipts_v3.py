from __future__ import annotations

import types
import unittest

import enroll_stage6_pre_b_receipts_v3 as m


class FrozenV2HelperBindingTests(unittest.TestCase):
    def test_install_prediction_helper_binds_exact_callable(self):
        def fn(*args):
            return {"sentinel": args}
        helper = types.SimpleNamespace(prediction_record=fn)
        old = getattr(m.base.legacy.formal, "prediction_record", None)
        try:
            m.install_prediction_helper(helper)
            self.assertIs(m.base.legacy.formal.prediction_record, fn)
        finally:
            if old is None:
                delattr(m.base.legacy.formal, "prediction_record")
            else:
                m.base.legacy.formal.prediction_record = old

    def test_missing_prediction_record_fails_closed(self):
        with self.assertRaises(m.EnrollmentError):
            m.install_prediction_helper(types.SimpleNamespace())


if __name__ == "__main__":
    unittest.main()
