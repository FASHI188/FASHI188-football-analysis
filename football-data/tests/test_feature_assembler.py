from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from assembly.feature_assembler import FeatureAssembler, FeatureFamilyPolicy
from pit.feature_store import PITFeatureRecord, PointInTimeFeatureStore


UTC = timezone.utc


def active_pit(family="lineup_pstart"):
    record = PITFeatureRecord(
        feature_family=family,
        entity_type="team",
        canonical_entity_id="team:a",
        fixture_id="fx1",
        value={"x": 1},
        source_name="test",
        source_record_id="r1",
        source_hash="sha256:test",
        observed_at=datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
        known_at=datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
        effective_at=datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
        expires_at=None,
        leakage_class="prematch",
        historical_use_allowed=True,
        adapter_version="test-v1",
    )
    return PointInTimeFeatureStore([record]).read(family, "fx1", datetime(2026, 1, 1, 10, 0, tzinfo=UTC), "team:a")


class FeatureAssemblerTests(unittest.TestCase):
    def test_probable_lineup_numeric_effect_is_disabled_by_default(self):
        assembler = FeatureAssembler()
        self.assertFalse(assembler.probable_lineup_numeric_effect_enabled)
        activation = assembler.assemble_family(
            "lineup_pstart",
            active_pit(),
            numerical_values={"expected_xi_strength": 1.2},
            numerical_feature_names=["expected_xi_strength"],
            component_input_hash="a",
            component_output_hash="b",
        )
        self.assertTrue(activation.recognized)
        self.assertTrue(activation.pit_legal)
        self.assertTrue(activation.assembled)
        self.assertTrue(activation.experiment_passed)
        self.assertFalse(activation.numeric_effect_enabled)
        self.assertFalse(activation.numeric_effect)
        self.assertEqual(activation.inactive_reason, "numeric_effect_disabled_by_governance")

    def test_recognized_does_not_imply_pit_or_assembly(self):
        assembler = FeatureAssembler()
        activation = assembler.assemble_family("player_technical", None)
        self.assertTrue(activation.recognized)
        self.assertFalse(activation.pit_legal)
        self.assertFalse(activation.assembled)
        self.assertFalse(activation.numeric_effect)

    def test_pit_legal_does_not_imply_assembled(self):
        assembler = FeatureAssembler()
        activation = assembler.assemble_family("lineup_pstart", active_pit())
        self.assertTrue(activation.pit_legal)
        self.assertFalse(activation.assembled)
        self.assertEqual(activation.inactive_reason, "not_wired_into_numerical_feature_input")

    def test_numeric_effect_requires_enabled_policy_and_hash_delta(self):
        assembler = FeatureAssembler({
            "demo": FeatureFamilyPolicy(True, True, True)
        })
        pit = active_pit("demo")
        same = assembler.assemble_family(
            "demo", pit, {"x": 1}, ["x"], component_input_hash="same", component_output_hash="same"
        )
        changed = assembler.assemble_family(
            "demo", pit, {"x": 1}, ["x"], component_input_hash="before", component_output_hash="after"
        )
        self.assertFalse(same.numeric_effect)
        self.assertEqual(same.inactive_reason, "no_component_output_delta_for_match")
        self.assertTrue(changed.numeric_effect)
        self.assertIsNone(changed.inactive_reason)

    def test_unknown_family_is_not_recognized(self):
        assembler = FeatureAssembler()
        activation = assembler.assemble_family("unknown", active_pit("unknown"))
        self.assertFalse(activation.recognized)
        self.assertFalse(activation.pit_legal)
        self.assertFalse(activation.assembled)
        self.assertFalse(activation.numeric_effect)

    def test_receipt_is_emitted_even_when_no_feature_is_active(self):
        assembler = FeatureAssembler()
        activation = assembler.assemble_family("player_technical", None)
        receipt = assembler.build_receipt(
            fixture_id="fx1",
            as_of=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
            canonical_home_team_id="team:a",
            canonical_away_team_id="team:b",
            activations=[activation],
            final_score_matrix_hash="matrixhash",
            final_1x2={"home": 0.4, "draw": 0.3, "away": 0.3},
            final_top1="home",
        )
        self.assertEqual(receipt.fixture_id, "fx1")
        self.assertEqual(len(receipt.activations), 1)
        self.assertTrue(receipt.receipt_hash)

    def test_receipt_hash_is_deterministic_and_activation_order_independent(self):
        assembler = FeatureAssembler()
        a = assembler.assemble_family("lineup_pstart", active_pit())
        b = assembler.assemble_family("player_technical", None)
        kwargs = dict(
            fixture_id="fx1",
            as_of=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
            canonical_home_team_id="team:a",
            canonical_away_team_id="team:b",
            final_score_matrix_hash="m",
            final_1x2={"home": 0.4, "draw": 0.3, "away": 0.3},
            final_top1="home",
        )
        r1 = assembler.build_receipt(activations=[a, b], **kwargs)
        r2 = assembler.build_receipt(activations=[b, a], **kwargs)
        self.assertEqual(r1.receipt_hash, r2.receipt_hash)

    def test_invalid_1x2_receipt_is_rejected(self):
        assembler = FeatureAssembler()
        with self.assertRaises(ValueError):
            assembler.build_receipt(
                fixture_id="fx1",
                as_of=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
                canonical_home_team_id="team:a",
                canonical_away_team_id="team:b",
                activations=[],
                final_1x2={"home": 0.5, "draw": 0.4, "away": 0.4},
                final_top1="home",
            )


if __name__ == "__main__":
    unittest.main()
