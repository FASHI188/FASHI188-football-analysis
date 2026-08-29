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
    unresolved_sources,
)


class LegacyR43RegistryTests(unittest.TestCase):
    def test_registry_has_exact_required_component_families(self):
        self.assertEqual(set(SPECS), {"R43Q", "R43R", "R43T", "R43U", "R43Y"})

    def test_every_component_is_disabled_by_default(self):
        self.assertTrue(all(not spec.enabled_by_default for spec in SPECS.values()))
        for key in SPECS:
            self.assertFalse(DeclaredLegacyScoreMatrixComponent(key).enabled)

    def test_source_lineage_is_exact_for_resolved_components(self):
        self.assertEqual(SPECS["R43Q"].source_blob_sha, "299b86ed07e49af0b9ec5c7632f519e91e836158")
        self.assertEqual(SPECS["R43R"].source_blob_sha, "8748e795bb92780c47af934c3187db14c254a415")
        self.assertEqual(SPECS["R43T"].source_blob_sha, "f6db4f0e6c0f544c058b15a7279731f55c5f6570")
        self.assertEqual(SPECS["R43U"].source_blob_sha, "4ad46cca4acb618068f6db2601cf96bad4109698")

    def test_gate_states_are_not_promoted_by_registry(self):
        self.assertFalse(SPECS["R43Q"].architecture_gate_passed)
        self.assertFalse(SPECS["R43R"].architecture_gate_passed)
        self.assertFalse(SPECS["R43T"].architecture_gate_passed)
        self.assertTrue(SPECS["R43U"].architecture_gate_passed)
        self.assertFalse(SPECS["R43U"].full_volume_53pct_met)
        self.assertTrue(SPECS["R43U"].implementation_migrated)
        self.assertFalse(SPECS["R43U"].enabled_by_default)
        self.assertFalse(SPECS["R43Q"].implementation_migrated)
        self.assertFalse(SPECS["R43T"].implementation_migrated)

    def test_r43r_is_not_misrepresented_as_native_score_matrix(self):
        self.assertEqual(SPECS["R43R"].native_output, "1x2_probabilities")
        self.assertNotIn("R43R", migration_candidates())

    def test_remaining_native_matrix_migration_candidates_are_q_and_t(self):
        self.assertEqual(migration_candidates(), ("R43Q", "R43T"))

    def test_r43y_provenance_remains_explicitly_unresolved(self):
        self.assertEqual(unresolved_sources(), ("R43Y",))
        self.assertFalse(SPECS["R43Y"].source_resolved)
        self.assertIsNone(SPECS["R43Y"].source_blob_sha)

    def test_declaration_cannot_execute_if_accidentally_called(self):
        component = DeclaredLegacyScoreMatrixComponent("R43U")
        with self.assertRaisesRegex(RuntimeError, "declaration-only"):
            component.apply([], None, {})


if __name__ == "__main__":
    unittest.main()
