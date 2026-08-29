from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from assembly.feature_assembler import FeatureAssembler
from components.v500_dynamic_state import V500BayesianDynamicStateComponent
from identity.team_identity import TeamIdentityResolver
from pipeline.s60_numerical_baseline import S60NumericalBaseline
from pipeline.unified_dataset import PredictionCase, SettledOutcome, UnifiedDatasetGenerator
from pipeline.unified_inference import FixtureRequest, UnifiedInferenceEngine, canonical_matrix
from pit.feature_store import PointInTimeFeatureStore

UTC = timezone.utc


def resolver():
    return TeamIdentityResolver([
        {"source_namespace": "test", "source_team_id": "h", "canonical_team_id": "H", "mapping_method": "test", "provenance_hash": "x"},
        {"source_namespace": "test", "source_team_id": "a", "canonical_team_id": "A", "mapping_method": "test", "provenance_hash": "x"},
        {"source_namespace": "test", "source_team_id": "h2", "canonical_team_id": "H2", "mapping_method": "test", "provenance_hash": "x"},
        {"source_namespace": "test", "source_team_id": "a2", "canonical_team_id": "A2", "mapping_method": "test", "provenance_hash": "x"},
    ])


def history_rows(n=36):
    out = []
    start = datetime(2025, 1, 1, tzinfo=UTC)
    teams = [("H", "A"), ("H2", "A2"), ("H", "A2"), ("H2", "A")]
    scores = [(2, 0), (1, 1), (0, 2)]
    for i in range(n):
        hg, ag = scores[i % len(scores)]
        h, a = teams[i % len(teams)]
        dt = start + timedelta(days=i)
        out.append({
            "date": dt.date().isoformat(),
            "game_id": f"g{i:03d}",
            "competition_id": "C1",
            "home_team": h,
            "away_team": a,
            "home_goals": hg,
            "away_goals": ag,
            "home_xg": 1.2 + 0.1 * (i % 3),
            "away_xg": 0.9 + 0.1 * ((i + 1) % 3),
            "xg_known_at": (dt + timedelta(hours=3)).isoformat(),
        })
    return out


def request(fid, h, a, as_of):
    return FixtureRequest(
        fid,
        as_of,
        "test",
        h,
        None,
        "test",
        a,
        None,
    )


class StaticBaseline:
    component_id = "static"
    component_version = "1"

    def predict(self, request, canonical_home_team_id, canonical_away_team_id, payload):
        return canonical_matrix([
            {"home_goals": 0, "away_goals": 0, "probability": 0.18},
            {"home_goals": 1, "away_goals": 0, "probability": 0.27},
            {"home_goals": 0, "away_goals": 1, "probability": 0.21},
            {"home_goals": 1, "away_goals": 1, "probability": 0.17},
            {"home_goals": 2, "away_goals": 1, "probability": 0.10},
            {"home_goals": 1, "away_goals": 2, "probability": 0.07},
        ])


class RealRuntimeNumericalChainTests(unittest.TestCase):
    def test_s60_fits_and_predicts_inside_unified_engine(self):
        s60 = S60NumericalBaseline.fit_from_history(
            history_rows(), expected_history_rows=None, classifier_train_rows=18
        )
        engine = UnifiedInferenceEngine(
            resolver(), PointInTimeFeatureStore(), FeatureAssembler(), s60
        )
        as_of = datetime(2026, 1, 1, 8, tzinfo=UTC)
        result = engine.predict(
            "live",
            request("fx", "h", "a", as_of),
            {"competition_id": "C1", "target_date": "2026-01-02"},
        )
        self.assertEqual(result.component_chain[0]["component_id"], "S60_stage_primary_numerical_baseline")
        receipt = result.component_chain[0]["numerical_receipt"]
        self.assertFalse(receipt["per_fixture_precomputed_numerics_accepted"])
        self.assertEqual(receipt["fit"]["history_rows"], 36)
        self.assertAlmostEqual(sum(result.probabilities.values()), 1.0, places=12)
        self.assertEqual(len(result.feature_activation_receipt["activations"]), 0)

    def test_s60_rejects_external_precomputed_matrix_or_probabilities(self):
        s60 = S60NumericalBaseline.fit_from_history(
            history_rows(), expected_history_rows=None, classifier_train_rows=18
        )
        engine = UnifiedInferenceEngine(
            resolver(), PointInTimeFeatureStore(), FeatureAssembler(), s60
        )
        as_of = datetime(2026, 1, 1, 8, tzinfo=UTC)
        with self.assertRaisesRegex(ValueError, "forbids precomputed"):
            engine.predict(
                "live",
                request("fx", "h", "a", as_of),
                {"competition_id": "C1", "target_date": "2026-01-02", "score_matrix": []},
            )

    def test_v500_is_disabled_by_default(self):
        v = V500BayesianDynamicStateComponent()
        self.assertFalse(v.enabled)
        self.assertEqual(v.formal_weight, 0)
        self.assertIn("INVALIDATED", v.source_status)

    def test_v500_enabled_uses_group_lifecycle_and_never_precomputed_output(self):
        v = V500BayesianDynamicStateComponent(enabled=True)
        engine = UnifiedInferenceEngine(
            resolver(), PointInTimeFeatureStore(), FeatureAssembler(), StaticBaseline(), (v,)
        )
        kickoff = datetime(2026, 1, 3, 18, tzinfo=UTC)
        dt = kickoff - timedelta(hours=1)
        cases = (
            PredictionCase(
                request("v1", "h", "a", dt), kickoff, {},
                component_payload={"prediction_datetime": kickoff.isoformat()},
                outcome=SettledOutcome(2, 0), competition_id="C1",
            ),
            PredictionCase(
                request("v2", "h2", "a2", dt), kickoff, {},
                component_payload={"prediction_datetime": kickoff.isoformat()},
                outcome=SettledOutcome(1, 1), competition_id="C1",
            ),
        )
        rows = UnifiedDatasetGenerator(engine).generate("replay", cases)
        self.assertEqual(len(rows), 2)
        self.assertFalse(v._group_open)
        for row in rows:
            item = [x for x in row.component_chain if x["component_id"] == v.component_id][0]
            self.assertTrue(item["enabled"])
            self.assertFalse(item["numerical_receipt"]["precomputed_v500_output_accepted"])
        bad = PredictionCase(
            request("v3", "h", "a", dt), kickoff + timedelta(days=1), {},
            component_payload={"prediction_datetime": (kickoff + timedelta(days=1)).isoformat(), "v500_score_matrix": []},
            outcome=SettledOutcome(0, 1), competition_id="C1",
        )
        with self.assertRaisesRegex(ValueError, "forbids precomputed"):
            UnifiedDatasetGenerator(engine).generate("replay", (bad,))


if __name__ == "__main__":
    unittest.main()
