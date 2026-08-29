from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from assembly.feature_assembler import FeatureAssembler, FeatureFamilyPolicy
from components.r43_native_matrix_components import R43QMarketScoreBaseline, R43TDynamicStateMatrixComponent, dense_to_cells
from components.r43_probability_matrix_adapters import R43RScoreMatrixTransportComponent, R43YScoreMatrixTransportComponent
from components.r43q_market_score_core import R43QMarketScoreCore
from components.r43r_football_residual import residual_prob
from components.r43u_fixed_diagonal import R43UFixedDiagonalInflationComponent
from components.r43y_draw_calibration import calibrate
from identity.team_identity import TeamIdentityResolver
from pipeline.unified_inference import FixtureRequest, UnifiedInferenceEngine, canonical_matrix, matrix_hash, one_x_two
from pit.feature_store import PITFeatureRecord, PointInTimeFeatureStore


Q_PAYLOAD = {
    "one_x_two_odds": {"home": 2.05, "draw": 3.35, "away": 3.75},
    "asian_handicap": {"line": -0.25, "home": 1.97, "away": 1.93},
    "over_under": {"line": 2.5, "over": 1.95, "under": 1.95},
}
AS_OF = datetime(2026, 8, 29, 5, 0, tzinfo=timezone.utc)
SNAPSHOT_AT = AS_OF - timedelta(minutes=20)


def resolver():
    rows = []
    for sid, cid in (("h1", "H1"), ("a1", "A1"), ("h2", "H2"), ("a2", "A2")):
        rows.append({
            "source_namespace": "test",
            "source_team_id": sid,
            "canonical_team_id": cid,
            "mapping_method": "test_pinned",
            "provenance_hash": "test",
        })
    return TeamIdentityResolver(rows)


def request(fid="fx1", h="h1", a="a1"):
    return FixtureRequest(
        fixture_id=fid,
        as_of=AS_OF,
        home_source_namespace="test",
        home_source_team_id=h,
        home_source_name=None,
        away_source_namespace="test",
        away_source_team_id=a,
        away_source_name=None,
    )


def market_record(fid: str) -> PITFeatureRecord:
    return PITFeatureRecord(
        feature_family="market_1x2_ah_ou",
        entity_type="fixture_market",
        canonical_entity_id=fid,
        fixture_id=fid,
        value={"snapshot_timestamp_utc": SNAPSHOT_AT.isoformat(), **Q_PAYLOAD},
        source_name="test_atomic_market",
        source_record_id=f"{fid}:market:1",
        source_hash=f"source-{fid}",
        observed_at=SNAPSHOT_AT + timedelta(minutes=1),
        known_at=SNAPSHOT_AT + timedelta(minutes=2),
        effective_at=SNAPSHOT_AT,
        expires_at=AS_OF + timedelta(hours=1),
        leakage_class="prematch_market_snapshot",
        historical_use_allowed=True,
        adapter_version="test-market-v1",
    )


def market_store(*fids: str) -> PointInTimeFeatureStore:
    return PointInTimeFeatureStore([market_record(fid) for fid in fids])


def research_market_assembler() -> FeatureAssembler:
    return FeatureAssembler({
        "market_1x2_ah_ou": FeatureFamilyPolicy(True, False, True),
    })


def engine(baseline, components=(), store=None, assembler=None):
    actual_store = store or PointInTimeFeatureStore()
    return UnifiedInferenceEngine(
        resolver(),
        actual_store,
        assembler or FeatureAssembler(),
        baseline,
        components,
    )


def q_engine(components=(), fids=("fx1",)):
    store = market_store(*fids)
    baseline = R43QMarketScoreBaseline(store)
    return engine(baseline, components, store=store, assembler=research_market_assembler()), baseline


class StaticMatrixBaseline:
    component_id = "test_static_matrix"
    component_version = "test-v1"

    def __init__(self, matrix):
        self.matrix = canonical_matrix(matrix)

    def predict(self, request, canonical_home_team_id, canonical_away_team_id, payload):
        return self.matrix


def small_matrix():
    return canonical_matrix([
        {"home_goals": 0, "away_goals": 0, "probability": 0.12},
        {"home_goals": 0, "away_goals": 1, "probability": 0.14},
        {"home_goals": 1, "away_goals": 0, "probability": 0.22},
        {"home_goals": 1, "away_goals": 1, "probability": 0.18},
        {"home_goals": 1, "away_goals": 2, "probability": 0.10},
        {"home_goals": 2, "away_goals": 1, "probability": 0.16},
        {"home_goals": 2, "away_goals": 2, "probability": 0.08},
    ])


class R43ComponentChainCompositionTests(unittest.TestCase):
    def test_r43q_baseline_exact_pit_bound_and_not_formal_default(self):
        e, baseline = q_engine()
        self.assertFalse(baseline.formal_default)
        self.assertTrue(baseline.pit_bound_market)
        result = e.predict("replay", request(), {})
        built = R43QMarketScoreCore.build(
            Q_PAYLOAD["one_x_two_odds"], Q_PAYLOAD["asian_handicap"], Q_PAYLOAD["over_under"]
        )
        self.assertEqual(result.score_matrix_hash, matrix_hash(dense_to_cells(built["score_matrix"])))
        activations = result.feature_activation_receipt["activations"]
        self.assertEqual(len(activations), 1)
        self.assertEqual(activations[0]["feature_family"], "market_1x2_ah_ou")
        self.assertTrue(activations[0]["numeric_effect"])

    def test_q_u_y_same_numerics_across_dataset_replay_live(self):
        built = R43QMarketScoreCore.build(
            Q_PAYLOAD["one_x_two_odds"], Q_PAYLOAD["asian_handicap"], Q_PAYLOAD["over_under"]
        )
        qmatrix = dense_to_cells(built["score_matrix"])
        u = R43UFixedDiagonalInflationComponent(enabled=True)
        umatrix = u.apply(qmatrix, None, {})
        u_probs = one_x_two(umatrix)
        payload = {"r43y_source_r43u0_probabilities": u_probs}
        outputs = []
        for mode in ("dataset", "replay", "live"):
            e, _ = q_engine(
                (R43UFixedDiagonalInflationComponent(enabled=True), R43YScoreMatrixTransportComponent(enabled=True)),
            )
            outputs.append(e.predict(mode, request(), {}, component_payload=payload))
        self.assertEqual(len({r.score_matrix_hash for r in outputs}), 1)
        self.assertEqual(len({tuple(sorted(r.probabilities.items())) for r in outputs}), 1)
        expected = calibrate(u_probs)
        for k in expected:
            self.assertAlmostEqual(outputs[0].probabilities[k], expected[k], places=15)

    def test_y_before_u_fails_closed_on_chain_order(self):
        built = R43QMarketScoreCore.build(
            Q_PAYLOAD["one_x_two_odds"], Q_PAYLOAD["asian_handicap"], Q_PAYLOAD["over_under"]
        )
        qmatrix = dense_to_cells(built["score_matrix"])
        umatrix = R43UFixedDiagonalInflationComponent(enabled=True).apply(qmatrix, None, {})
        source_u = one_x_two(umatrix)
        bad_engine, _ = q_engine(
            (R43YScoreMatrixTransportComponent(enabled=True), R43UFixedDiagonalInflationComponent(enabled=True)),
        )
        with self.assertRaisesRegex(RuntimeError, "source_1x2_mismatch"):
            bad_engine.predict(
                "replay", request(), {},
                component_payload={"r43y_source_r43u0_probabilities": source_u},
            )

    def test_r43r_probability_component_composes_when_source_mass_matches(self):
        matrix = small_matrix()
        market = one_x_two(matrix)
        football = {"home": 0.48, "draw": 0.29, "away": 0.23}
        beta = 0.075
        e = engine(StaticMatrixBaseline(matrix), (R43RScoreMatrixTransportComponent(enabled=True),))
        result = e.predict(
            "replay", request(), {},
            component_payload={
                "r43r_market_probabilities": market,
                "r43r_football_probabilities": football,
                "r43r_beta": beta,
            },
        )
        expected = residual_prob(market, football, beta)
        for k in expected:
            self.assertAlmostEqual(result.probabilities[k], expected[k], places=15)

    def test_r43t_same_kickoff_group_freezes_state_then_updates_after_group(self):
        built = R43QMarketScoreCore.build(
            Q_PAYLOAD["one_x_two_odds"], Q_PAYLOAD["asian_handicap"], Q_PAYLOAD["over_under"]
        )
        lh = built["lambda_home"]
        la = built["lambda_away"]
        t = R43TDynamicStateMatrixComponent(enabled=True)
        e, _ = q_engine((t,), fids=("fx1", "fx2"))
        component_payload = {"r43t_static_lambda_home": lh, "r43t_static_lambda_away": la}

        with self.assertRaisesRegex(RuntimeError, "begin_group"):
            e.predict("replay", request(), {}, component_payload=component_payload)

        t.begin_group()
        first = e.predict("replay", request("fx1", "h1", "a1"), {}, component_payload=component_payload)
        second = e.predict("replay", request("fx2", "h2", "a2"), {}, component_payload=component_payload)
        receipts = t.projection_receipts()
        self.assertEqual(len(receipts), 2)
        self.assertEqual(receipts[0]["state_total_pred"], receipts[1]["state_total_pred"])
        self.assertEqual(receipts[0]["state_diff_pred"], receipts[1]["state_diff_pred"])
        self.assertEqual(first.score_matrix_hash, second.score_matrix_hash)

        t.settle_group([
            {"lambda_home": lh, "lambda_away": la, "hg": 4, "ag": 0},
            {"lambda_home": lh, "lambda_away": la, "hg": 3, "ag": 0},
        ])
        self.assertFalse(t.snapshot()["group_open"])

        q_only, _ = q_engine()
        q_only_result = q_only.predict("replay", request(), {})
        t.begin_group()
        after = e.predict("replay", request(), {}, component_payload=component_payload)
        self.assertNotEqual(after.score_matrix_hash, q_only_result.score_matrix_hash)
        t.settle_group([{"lambda_home": lh, "lambda_away": la, "hg": 1, "ag": 1}])

    def test_all_research_components_are_disabled_by_default(self):
        self.assertFalse(R43UFixedDiagonalInflationComponent().enabled)
        self.assertFalse(R43RScoreMatrixTransportComponent().enabled)
        self.assertFalse(R43YScoreMatrixTransportComponent().enabled)
        self.assertFalse(R43TDynamicStateMatrixComponent().enabled)


if __name__ == "__main__":
    unittest.main()
