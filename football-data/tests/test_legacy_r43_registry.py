from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from components.legacy_r43_registry import (
    DeclaredLegacyScoreMatrixComponent,
    SPECS,
    migration_candidates,
    native_probability_components,
    native_score_matrix_components,
    unresolved_sources,
)


class LegacyR43RegistryTests(unittest.TestCase):
    def test_registry_has_exact_required_component_families(self):
        self.assertEqual(set(SPECS), {"R43Q", "R43R", "R43T", "R43U", "R43Y"})

    def test_every_component_is_disabled_by_default(self):
        self.assertTrue(all(not spec.enabled_by_default for spec in SPECS.values()))
        for key in SPECS:
            self.assertFalse(DeclaredLegacyScoreMatrixComponent(key).enabled)

    def test_every_source_is_resolved_and_migrated(self):
        self.assertEqual(unresolved_sources(), ())
        self.assertEqual(migration_candidates(), ())
        self.assertTrue(all(spec.source_resolved for spec in SPECS.values()))
        self.assertTrue(all(spec.implementation_migrated for spec in SPECS.values()))

    def test_source_lineage_is_exact(self):
        self.assertEqual(SPECS["R43Q"].source_blob_sha, "299b86ed07e49af0b9ec5c7632f519e91e836158")
        self.assertEqual(SPECS["R43R"].source_blob_sha, "8748e795bb92780c47af934c3187db14c254a415")
        self.assertEqual(SPECS["R43T"].source_blob_sha, "f6db4f0e6c0f544c058b15a7279731f55c5f6570")
        self.assertEqual(SPECS["R43U"].source_blob_sha, "4ad46cca4acb618068f6db2601cf96bad4109698")
        self.assertEqual(SPECS["R43Y"].source_blob_sha, "a342138bef97eb4acb0bcba015dea251a3280fdf")
        self.assertEqual(
            SPECS["R43Y"].source_path,
            "football-data/experiments/r43y0_draw_calibration_forward/run_r43y0.py",
        )

    def test_historical_gate_states_are_not_promoted_by_registry(self):
        self.assertFalse(SPECS["R43Q"].architecture_gate_passed)
        self.assertFalse(SPECS["R43R"].architecture_gate_passed)
        self.assertFalse(SPECS["R43T"].architecture_gate_passed)
        self.assertTrue(SPECS["R43U"].architecture_gate_passed)
        self.assertFalse(SPECS["R43U"].full_volume_53pct_met)
        self.assertIsNone(SPECS["R43Y"].architecture_gate_passed)
        self.assertIsNone(SPECS["R43Y"].full_volume_53pct_met)
        self.assertTrue(all(not spec.enabled_by_default for spec in SPECS.values()))

    def test_native_contract_split_is_explicit(self):
        self.assertEqual(native_score_matrix_components(), ("R43Q", "R43T", "R43U"))
        self.assertEqual(native_probability_components(), ("R43R", "R43Y"))
        self.assertEqual(SPECS["R43R"].native_output, "1x2_probabilities")
        self.assertEqual(SPECS["R43Y"].native_output, "1x2_probabilities")

    def test_declaration_cannot_execute_if_accidentally_called(self):
        component = DeclaredLegacyScoreMatrixComponent("R43U")
        with self.assertRaisesRegex(RuntimeError, "declaration-only"):
            component.apply([], None, {})


if __name__ == "__main__":
    unittest.main()
