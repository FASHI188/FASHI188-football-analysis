from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from assembly.feature_assembler import FeatureAssembler, FeatureFamilyPolicy
from components.r43_native_matrix_components import R43QMarketScoreBaseline, R43TDynamicStateMatrixComponent
from components.r43q_market_score_core import R43QMarketScoreCore
from evaluation.unified_evaluator import evaluate, paired_compare
from identity.team_identity import TeamIdentityResolver
from pipeline.unified_dataset import PredictionCase, SettledOutcome, UnifiedDatasetGenerator, dataset_fingerprint, time_ordered_folds
from pipeline.unified_inference import FixtureRequest, UnifiedInferenceEngine, canonical_matrix
from pit.feature_store import PITFeatureRecord, PointInTimeFeatureStore

UTC = timezone.utc
Q_PAYLOAD = {
    "one_x_two_odds": {"home": 2.05, "draw": 3.35, "away": 3.75},
    "asian_handicap": {"line": -0.25, "home": 1.97, "away": 1.93},
    "over_under": {"line": 2.5, "over": 1.95, "under": 1.95},
}


def identity_resolver(n=20):
    rows = []
    for i in range(n):
        rows.append({
            "source_namespace": "test",
            "source_team_id": f"t{i}",
            "canonical_team_id": f"T{i}",
            "mapping_method": "test_pinned",
            "provenance_hash": "test",
        })
    return TeamIdentityResolver(rows)


def fixture(fid, home_i, away_i, as_of):
    return FixtureRequest(
        fixture_id=fid,
        as_of=as_of,
        home_source_namespace="test",
        home_source_team_id=f"t{home_i}",
        home_source_name=None,
        away_source_namespace="test",
        away_source_team_id=f"t{away_i}",
        away_source_name=None,
    )


class StaticMatrixBaseline:
    component_id = "test_static_matrix"
    component_version = "test-v1"

    def __init__(self, matrix):
        self.matrix = canonical_matrix(matrix)

    def predict(self, request, canonical_home_team_id, canonical_away_team_id, payload):
        if "actual_result" in payload or "home_goals_90" in payload or "away_goals_90" in payload:
            raise AssertionError("settled outcome leaked into baseline payload")
        return self.matrix


def baseline_matrix():
    return canonical_matrix([
        {"home_goals": 0, "away_goals": 0, "probability": 0.25},
        {"home_goals": 1, "away_goals": 0, "probability": 0.30},
        {"home_goals": 0, "away_goals": 1, "probability": 0.20},
        {"home_goals": 1, "away_goals": 1, "probability": 0.10},
        {"home_goals": 2, "away_goals": 1, "probability": 0.10},
        {"home_goals": 1, "away_goals": 2, "probability": 0.05},
    ])


def candidate_matrix():
    return canonical_matrix([
        {"home_goals": 0, "away_goals": 0, "probability": 0.30},
        {"home_goals": 1, "away_goals": 0, "probability": 0.20},
        {"home_goals": 0, "away_goals": 1, "probability": 0.20},
        {"home_goals": 1, "away_goals": 1, "probability": 0.15},
        {"home_goals": 2, "away_goals": 1, "probability": 0.10},
        {"home_goals": 1, "away_goals": 2, "probability": 0.05},
    ])


def engine(baseline, components=(), store=None, assembler=None):
    actual_store = store or PointInTimeFeatureStore()
    return UnifiedInferenceEngine(
        identity_resolver(),
        actual_store,
        assembler or FeatureAssembler(),
        baseline,
        components,
    )


def market_record_for_request(req: FixtureRequest) -> PITFeatureRecord:
    snapshot_at = req.as_of - timedelta(minutes=30)
    return PITFeatureRecord(
        feature_family="market_1x2_ah_ou",
        entity_type="fixture_market",
        canonical_entity_id=req.fixture_id,
        fixture_id=req.fixture_id,
        value={"snapshot_timestamp_utc": snapshot_at.isoformat(), **Q_PAYLOAD},
        source_name="m6_test_market",
        source_record_id=f"{req.fixture_id}:market",
        source_hash=f"m6:{req.fixture_id}",
        observed_at=snapshot_at + timedelta(minutes=1),
        known_at=snapshot_at + timedelta(minutes=2),
        effective_at=snapshot_at,
        expires_at=req.as_of + timedelta(hours=2),
        leakage_class="prematch_market_snapshot",
        historical_use_allowed=True,
        adapter_version="m6-test-v1",
    )


def q_engine(requests, components=()):
    store = PointInTimeFeatureStore([market_record_for_request(req) for req in requests])
    baseline = R43QMarketScoreBaseline(store)
    assembler = FeatureAssembler({
        "market_1x2_ah_ou": FeatureFamilyPolicy(True, False, True),
    })
    return engine(baseline, components, store=store, assembler=assembler)


def cases_with_outcomes(outcomes, matrix_payload=None, start=None):
    start = start or datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    cases = []
    for i, outcome in enumerate(outcomes):
        kickoff = start + timedelta(days=i)
        cases.append(PredictionCase(
            fixture(f"fx{i}", i * 2, i * 2 + 1, kickoff - timedelta(hours=6)),
            kickoff,
            matrix_payload or {},
            outcome=outcome,
            competition_id="TEST",
        ))
    return tuple(cases)


class UnifiedDatasetEvaluatorTests(unittest.TestCase):
    def test_case_requires_strictly_prematch_asof(self):
        kickoff = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
        with self.assertRaisesRegex(ValueError, "strictly before kickoff"):
            PredictionCase(fixture("bad", 0, 1, kickoff), kickoff, {})

    def test_dataset_replay_live_share_same_prediction_numerics(self):
        outcomes = (SettledOutcome(2, 0), SettledOutcome(1, 1))
        historical = cases_with_outcomes(outcomes)
        live_cases = tuple(PredictionCase(
            c.request, c.kickoff_at, c.baseline_payload,
            feature_specs=c.feature_specs,
            component_payload=c.component_payload,
            outcome=None,
            competition_id=c.competition_id,
        ) for c in historical)

        hashes = []
        for mode, cases in (("dataset", historical), ("replay", historical), ("live", live_cases)):
            rows = UnifiedDatasetGenerator(engine(StaticMatrixBaseline(baseline_matrix()))).generate(mode, cases)
            hashes.append(tuple(r.prediction_numerics_hash for r in rows))
        self.assertEqual(hashes[0], hashes[1])
        self.assertEqual(hashes[1], hashes[2])

    def test_live_rejects_settled_outcome_input(self):
        cases = cases_with_outcomes((SettledOutcome(1, 0),))
        with self.assertRaisesRegex(ValueError, "live generation must not receive settled outcomes"):
            UnifiedDatasetGenerator(engine(StaticMatrixBaseline(baseline_matrix()))).generate("live", cases)

    def test_prediction_hash_excludes_outcome_but_row_hash_includes_it(self):
        kickoff = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
        req = fixture("same", 0, 1, kickoff - timedelta(hours=4))
        home_case = PredictionCase(req, kickoff, {}, outcome=SettledOutcome(1, 0))
        away_case = PredictionCase(req, kickoff, {}, outcome=SettledOutcome(0, 1))
        gen1 = UnifiedDatasetGenerator(engine(StaticMatrixBaseline(baseline_matrix())))
        gen2 = UnifiedDatasetGenerator(engine(StaticMatrixBaseline(baseline_matrix())))
        home_row = gen1.generate("replay", (home_case,))[0]
        away_row = gen2.generate("replay", (away_case,))[0]
        self.assertEqual(home_row.prediction_numerics_hash, away_row.prediction_numerics_hash)
        self.assertNotEqual(home_row.row_hash, away_row.row_hash)

    def test_duplicate_fixture_ids_fail_closed(self):
        kickoff = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
        c1 = PredictionCase(fixture("dup", 0, 1, kickoff - timedelta(hours=4)), kickoff, {})
        c2 = PredictionCase(fixture("dup", 2, 3, kickoff - timedelta(hours=3)), kickoff + timedelta(days=1), {})
        with self.assertRaisesRegex(ValueError, "duplicate fixture_id"):
            UnifiedDatasetGenerator(engine(StaticMatrixBaseline(baseline_matrix()))).generate("replay", (c1, c2))

    def test_r43t_same_kickoff_group_is_frozen_before_settlement(self):
        built = R43QMarketScoreCore.build(
            Q_PAYLOAD["one_x_two_odds"], Q_PAYLOAD["asian_handicap"], Q_PAYLOAD["over_under"]
        )
        component_payload = {
            "r43t_static_lambda_home": built["lambda_home"],
            "r43t_static_lambda_away": built["lambda_away"],
        }
        kickoff = datetime(2026, 8, 2, 18, 0, tzinfo=UTC)
        req1 = fixture("g1", 0, 1, kickoff - timedelta(hours=5))
        req2 = fixture("g2", 2, 3, kickoff - timedelta(hours=4))
        group = (
            PredictionCase(
                req1, kickoff, {},
                component_payload=component_payload, outcome=SettledOutcome(4, 0),
            ),
            PredictionCase(
                req2, kickoff, {},
                component_payload=component_payload, outcome=SettledOutcome(3, 0),
            ),
        )
        t = R43TDynamicStateMatrixComponent(enabled=True)
        rows = UnifiedDatasetGenerator(q_engine((req1, req2), (t,))).generate("replay", group)
        receipts = t.projection_receipts()
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(receipts), 2)
        self.assertEqual(receipts[0]["state_total_pred"], receipts[1]["state_total_pred"])
        self.assertEqual(receipts[0]["state_diff_pred"], receipts[1]["state_diff_pred"])
        snap = t.snapshot()
        self.assertFalse(snap["group_open"])
        self.assertNotEqual(snap["x"], [0.0, 0.0])

    def test_stateful_live_is_fail_closed_until_pending_settlement_lifecycle_is_governed(self):
        t = R43TDynamicStateMatrixComponent(enabled=True)
        kickoff = datetime(2026, 8, 2, 18, 0, tzinfo=UTC)
        req = fixture("live-t", 0, 1, kickoff - timedelta(hours=2))
        case = PredictionCase(req, kickoff, {})
        with self.assertRaisesRegex(RuntimeError, "pending-settlement live lifecycle"):
            UnifiedDatasetGenerator(q_engine((req,), (t,))).generate("live", (case,))

    def test_time_folds_are_chronological_and_never_split_same_kickoff_group(self):
        start = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
        specs = [
            ("a", start, 0, 1),
            ("b", start, 2, 3),
            ("c", start + timedelta(days=1), 4, 5),
            ("d", start + timedelta(days=2), 6, 7),
            ("e", start + timedelta(days=2), 8, 9),
            ("f", start + timedelta(days=3), 10, 11),
        ]
        cases = tuple(PredictionCase(
            fixture(fid, h, a, ko - timedelta(hours=3)), ko, {}, outcome=SettledOutcome(1, 0)
        ) for fid, ko, h, a in specs)
        rows = UnifiedDatasetGenerator(engine(StaticMatrixBaseline(baseline_matrix()))).generate("replay", cases)
        folds = time_ordered_folds(rows, 3)
        kickoff_to_fold = {}
        for fi, fold in enumerate(folds):
            for row in fold:
                previous = kickoff_to_fold.setdefault(row.kickoff_at, fi)
                self.assertEqual(previous, fi)
        for left, right in zip(folds, folds[1:]):
            self.assertLess(max(r.kickoff_at for r in left), min(r.kickoff_at for r in right))

    def test_evaluator_and_strict_paired_comparison(self):
        outcomes = (
            SettledOutcome(2, 0),
            SettledOutcome(1, 1),
            SettledOutcome(0, 0),
            SettledOutcome(0, 1),
        )
        cases = cases_with_outcomes(outcomes)
        base_rows = UnifiedDatasetGenerator(engine(StaticMatrixBaseline(baseline_matrix()))).generate("replay", cases)
        cand_rows = UnifiedDatasetGenerator(engine(StaticMatrixBaseline(candidate_matrix()))).generate("replay", cases)
        bm = evaluate(base_rows)
        cm = evaluate(cand_rows)
        self.assertEqual(bm["count"], 4)
        self.assertEqual(bm["hits"], 1)
        self.assertAlmostEqual(bm["top1_accuracy"], 0.25)
        self.assertEqual(cm["hits"], 2)
        self.assertAlmostEqual(cm["top1_accuracy"], 0.50)
        expected_base_ll = -(math.log(0.40) + 2 * math.log(0.35) + math.log(0.25)) / 4
        self.assertAlmostEqual(bm["logloss"], expected_base_ll, places=15)

        paired = paired_compare(base_rows, cand_rows)
        self.assertEqual(paired["fixture_count"], 4)
        self.assertAlmostEqual(paired["candidate_minus_baseline"]["accuracy_pp"], 25.0)
        self.assertEqual(paired["decision_changes"]["top1_changed_count"], 4)
        self.assertEqual(paired["decision_changes"]["changed_to_draw_count"], 4)
        with self.assertRaisesRegex(ValueError, "exact same fixture set"):
            paired_compare(base_rows, cand_rows[:-1])

    def test_dataset_fingerprint_is_order_independent_but_content_sensitive(self):
        cases = cases_with_outcomes((SettledOutcome(1, 0), SettledOutcome(0, 0)))
        rows = UnifiedDatasetGenerator(engine(StaticMatrixBaseline(baseline_matrix()))).generate("replay", cases)
        self.assertEqual(dataset_fingerprint(rows), dataset_fingerprint(reversed(rows)))
        alt = UnifiedDatasetGenerator(engine(StaticMatrixBaseline(candidate_matrix()))).generate("replay", cases)
        self.assertNotEqual(dataset_fingerprint(rows), dataset_fingerprint(alt))


if __name__ == "__main__":
    unittest.main()
