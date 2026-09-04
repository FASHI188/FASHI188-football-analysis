from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys
import types
import unittest

import enroll_stage6_pre_b_receipts_v3 as m


def canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


class FrozenV2HelperBindingTests(unittest.TestCase):
    def test_reconstruct_adapter_binds_exact_callable_and_preserves_result(self):
        def fn(*args):
            return {"sentinel": list(args)}
        helper = types.SimpleNamespace(prediction_record=fn)
        legacy = types.SimpleNamespace(formal=types.SimpleNamespace())
        expected = (legacy, "formal_state", "process_pack", "b_pack", "future", "league_provenance", "shot_transport", "b_receipt", "legacy_code")

        def original(*args, **kwargs):
            self.assertEqual(args, ("x",))
            self.assertEqual(kwargs, {"y": 1})
            return expected

        wrapped = m.wrap_reconstruct_cutoff_state(original, helper)
        got = wrapped("x", y=1)
        self.assertIs(got, expected)
        self.assertIs(got[0].formal.prediction_record, fn)
        self.assertEqual(got[0].formal.prediction_record(1, 2, 3, 0.75), fn(1, 2, 3, 0.75))

    def test_missing_prediction_record_fails_closed(self):
        with self.assertRaises(m.EnrollmentError):
            m.wrap_reconstruct_cutoff_state(lambda: None, types.SimpleNamespace())

    @unittest.skipUnless(os.environ.get("STAGE6_FROZEN_V2_HELPER"), "real frozen helper artifact not supplied")
    def test_real_frozen_helper_adapter_equals_direct_replay_record_bytes(self):
        path = pathlib.Path(os.environ["STAGE6_FROZEN_V2_HELPER"])
        spec = importlib.util.spec_from_file_location("stage6_v3_equivalence_frozen_helper", path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        helper = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = helper
        spec.loader.exec_module(helper)

        class XG:
            @staticmethod
            def fast_poisson(mu_home, mu_away):
                return {"mean_home": float(mu_home), "mean_away": float(mu_away)}

        base = {
            "p_home": 0.51, "p_draw": 0.27, "p_away": 0.22,
            "mu_home": 1.43, "mu_away": 1.08, "cold_start_bucket": "warm",
        }
        xp = {
            "p_home": 0.47, "p_draw": 0.29, "p_away": 0.24,
            "matrix_mean_home": 1.35, "matrix_mean_away": 1.14,
            "dynamic": {"fallback_exact_v1": False},
        }
        direct = helper.prediction_record(XG, base, xp, 0.75)
        legacy = types.SimpleNamespace(formal=types.SimpleNamespace())
        result = (legacy, None, None, None, None, None, None, None, None)
        wrapped = m.wrap_reconstruct_cutoff_state(lambda: result, helper)
        adapted_result = wrapped()
        adapted = adapted_result[0].formal.prediction_record(XG, base, xp, 0.75)
        self.assertIs(adapted_result[0].formal.prediction_record, helper.prediction_record)
        self.assertEqual(canon(adapted), canon(direct))


if __name__ == "__main__":
    unittest.main()
