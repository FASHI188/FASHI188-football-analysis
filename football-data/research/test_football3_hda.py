from __future__ import annotations

import math
import unittest

from football3_hda import (
    COMPLETE_HDA,
    DEFAULT_PROBABILITY_TOLERANCE,
    DEFAULT_TIE_TOLERANCE,
    HDA_CLASS_ORDER,
    HDAValidationError,
    PARTIAL_HDA_UNRESOLVED_TAIL,
    ROBUST_PARTIAL_TOP1,
    ScoreCell,
    TOP1_TIE,
    TOP1_UNRESOLVED_DUE_TO_TAIL,
    aggregate_score_matrix_to_hda,
    choose_hda_top1,
    draw_classification_metrics,
    score_hda_probabilities,
    validate_score_matrix,
)

P_TOL = DEFAULT_PROBABILITY_TOLERANCE
TIE_TOL = DEFAULT_TIE_TOLERANCE


def required(n: int = 3) -> tuple[tuple[int, int], ...]:
    return tuple((h, a) for h in range(n) for a in range(n))


def cells(matrix: list[list[float]]) -> list[ScoreCell]:
    return [ScoreCell(h, a, float(matrix[h][a])) for h in range(len(matrix)) for a in range(len(matrix[h]))]


def agg(matrix: list[list[float]], *, unresolved_tail: bool = False, tail: float = 0.0, support=None):
    support = support or required(len(matrix))
    return aggregate_score_matrix_to_hda(
        cells(matrix),
        unresolved_tail=unresolved_tail,
        tail_probability=tail,
        class_order=HDA_CLASS_ORDER,
        probability_tolerance=P_TOL,
        tie_tolerance=TIE_TOL,
        score_support_id=f"synthetic-{len(support)}-cell-support",
        schema_version="synthetic-score-support-v1",
        required_score_cells=support,
    )


LOW_SYMMETRIC = [
    [0.20, 0.16, 0.08],
    [0.16, 0.12, 0.07],
    [0.08, 0.07, 0.06],
]
MID_SYMMETRIC = [
    [0.10, 0.14, 0.12],
    [0.14, 0.09, 0.105],
    [0.12, 0.105, 0.08],
]
HOME_ADVANTAGE = [
    [0.08, 0.05, 0.03],
    [0.20, 0.10, 0.04],
    [0.22, 0.18, 0.10],
]
AWAY_ADVANTAGE = [list(row) for row in zip(*HOME_ADVANTAGE)]
DRAW_AGGREGATE_NOT_SCORE_TOP1 = [
    [0.12, 0.18, 0.09],
    [0.19, 0.11, 0.06],
    [0.08, 0.06, 0.11],
]
ONE_ONE_SCORE_TOP1_HOME_HDA = [
    [0.05, 0.11, 0.09],
    [0.15, 0.20, 0.08],
    [0.14, 0.13, 0.05],
]


class Football3HDATest(unittest.TestCase):
    def assertFailCode(self, code: str, fn, *args, **kwargs):
        with self.assertRaises(HDAValidationError) as ctx:
            fn(*args, **kwargs)
        self.assertEqual(ctx.exception.code, code)

    # 1
    def test_symmetric_low_goals_draw_is_natural_top1_below_half(self):
        r = agg(LOW_SYMMETRIC)
        self.assertEqual(r["status"], COMPLETE_HDA)
        self.assertAlmostEqual(r["home_probability"], r["away_probability"], places=15)
        self.assertEqual(r["top1_category"], "DRAW")
        self.assertLess(r["draw_probability"], 0.5)
        self.assertAlmostEqual(r["draw_probability"], 0.38)

    # 2
    def test_symmetric_mid_goals_home_away_tie_and_draw_lower(self):
        low = agg(LOW_SYMMETRIC)
        mid = agg(MID_SYMMETRIC)
        self.assertLess(mid["draw_probability"], low["draw_probability"])
        self.assertLessEqual(abs(mid["home_probability"] - mid["away_probability"]), TIE_TOL)
        self.assertEqual(mid["top1_status"], TOP1_TIE)
        self.assertEqual(set(mid["tied_categories"]), {"HOME", "AWAY"})

    # 3
    def test_home_advantage_home_top1(self):
        self.assertEqual(agg(HOME_ADVANTAGE)["top1_category"], "HOME")

    # 4
    def test_away_advantage_away_top1(self):
        self.assertEqual(agg(AWAY_ADVANTAGE)["top1_category"], "AWAY")

    # 5
    def test_aggregate_draw_top1_without_draw_exact_score_top1(self):
        r = agg(DRAW_AGGREGATE_NOT_SCORE_TOP1)
        self.assertEqual(r["top1_category"], "DRAW")
        self.assertEqual(r["max_specific_score"], (1, 0))
        self.assertFalse(r["max_specific_score_is_draw"])
        self.assertFalse(r["exact_score_top1_and_hda_top1_agree"])

    # 6
    def test_one_one_exact_score_top1_but_home_hda_top1(self):
        r = agg(ONE_ONE_SCORE_TOP1_HOME_HDA)
        self.assertEqual(r["max_specific_score"], (1, 1))
        self.assertTrue(r["max_specific_score_is_draw"])
        self.assertEqual(r["top1_category"], "HOME")
        self.assertFalse(r["exact_score_top1_and_hda_top1_agree"])

    # 7
    def test_total_probability_point_nine_fails_closed(self):
        bad = [row[:] for row in LOW_SYMMETRIC]
        bad[0][0] -= 0.10
        self.assertFailCode("PROBABILITY_MASS_NOT_CONSERVED", agg, bad)

    # 8
    def test_negative_probability_fails_closed(self):
        bad = [row[:] for row in LOW_SYMMETRIC]
        bad[0][0] = -0.01
        self.assertFailCode("NEGATIVE_PROBABILITY", agg, bad)

    # 9
    def test_nan_probability_fails_closed(self):
        bad = [row[:] for row in LOW_SYMMETRIC]
        bad[0][0] = math.nan
        self.assertFailCode("NONFINITE_PROBABILITY", agg, bad)

    # 10
    def test_inf_probability_fails_closed(self):
        bad = [row[:] for row in LOW_SYMMETRIC]
        bad[0][0] = math.inf
        self.assertFailCode("NONFINITE_PROBABILITY", agg, bad)

    # 11
    def test_duplicate_score_cell_fails_closed(self):
        c = cells(LOW_SYMMETRIC)
        c.append(c[0])
        self.assertFailCode(
            "DUPLICATE_SCORE_CELL",
            aggregate_score_matrix_to_hda,
            c,
            unresolved_tail=False,
            tail_probability=0.0,
            class_order=HDA_CLASS_ORDER,
            probability_tolerance=P_TOL,
            tie_tolerance=TIE_TOL,
            score_support_id="s",
            schema_version="v1",
            required_score_cells=required(3),
        )

    # 12
    def test_missing_required_diagonal_fails_closed(self):
        support = required(2)
        c = [ScoreCell(0, 0, 0.3), ScoreCell(0, 1, 0.2), ScoreCell(1, 0, 0.5)]
        self.assertFailCode(
            "MISSING_REQUIRED_DIAGONAL",
            validate_score_matrix,
            c,
            unresolved_tail=False,
            tail_probability=0.0,
            class_order=HDA_CLASS_ORDER,
            probability_tolerance=P_TOL,
            score_support_id="2x2",
            schema_version="v1",
            required_score_cells=support,
        )

    # 13
    def test_wrong_category_order_fails_closed(self):
        self.assertFailCode(
            "INVALID_CLASS_ORDER",
            validate_score_matrix,
            cells(LOW_SYMMETRIC),
            unresolved_tail=False,
            tail_probability=0.0,
            class_order=("HOME", "AWAY", "DRAW"),
            probability_tolerance=P_TOL,
            score_support_id="s",
            schema_version="v1",
            required_score_cells=required(3),
        )

    # 14
    def test_tail_7plus_unresolved_is_partial_not_full_hda(self):
        matrix = [[x * 0.8 for x in row] for row in LOW_SYMMETRIC]
        r = agg(matrix, unresolved_tail=True, tail=0.2)
        self.assertEqual(r["status"], PARTIAL_HDA_UNRESOLVED_TAIL)
        self.assertIsNone(r["home_probability"])
        self.assertIsNone(r["draw_probability"])
        self.assertIsNone(r["away_probability"])
        self.assertIsNone(r["hda_probability_sum"])
        self.assertAlmostEqual(r["unresolved_tail_mass"], 0.2)

    # 15
    def test_tail_with_robust_top1(self):
        matrix = [[0.05, 0.05], [0.65, 0.05]]
        r = agg(matrix, unresolved_tail=True, tail=0.20, support=required(2))
        self.assertEqual(r["top1_status"], ROBUST_PARTIAL_TOP1)
        self.assertEqual(r["top1_category"], "HOME")
        self.assertGreater(r["hda_bounds"]["HOME"]["lower"], r["hda_bounds"]["DRAW"]["upper"])
        self.assertGreater(r["hda_bounds"]["HOME"]["lower"], r["hda_bounds"]["AWAY"]["upper"])

    # 16
    def test_tail_can_change_top1_is_unresolved(self):
        matrix = [[0.15, 0.20], [0.35, 0.10]]
        r = agg(matrix, unresolved_tail=True, tail=0.20, support=required(2))
        self.assertEqual(r["top1_status"], TOP1_UNRESOLVED_DUE_TO_TAIL)
        self.assertIsNone(r["top1_category"])

    # 17
    def test_exact_score_top1_and_hda_top1_differ(self):
        r = agg(ONE_ONE_SCORE_TOP1_HOME_HDA)
        self.assertEqual(r["exact_score_top1_category"], "DRAW")
        self.assertEqual(r["top1_category"], "HOME")
        self.assertFalse(r["exact_score_top1_and_hda_top1_agree"])

    # 18
    def test_synthetic_hda_metrics(self):
        rows = [
            {"HOME": 0.70, "DRAW": 0.20, "AWAY": 0.10},
            {"HOME": 0.20, "DRAW": 0.60, "AWAY": 0.20},
            {"HOME": 0.10, "DRAW": 0.20, "AWAY": 0.70},
            {"HOME": 0.40, "DRAW": 0.35, "AWAY": 0.25},
        ]
        labels = ["HOME", "DRAW", "AWAY", "DRAW"]
        m = score_hda_probabilities(
            rows,
            labels,
            class_order=HDA_CLASS_ORDER,
            probability_tolerance=P_TOL,
            tie_tolerance=TIE_TOL,
        )
        self.assertTrue(math.isfinite(m["LogLoss"]))
        self.assertGreaterEqual(m["Brier"], 0.0)
        self.assertGreaterEqual(m["RPS"], 0.0)
        self.assertEqual(m["DrawCalls"], 1)
        self.assertAlmostEqual(m["DrawRecall"], 0.5)
        self.assertTrue(m["probability_conservation_pass"])
        self.assertFalse(m["classification_metrics_can_override_proper_score_failure"])

    # 19
    def test_wrong_label_and_probability_row_count_fails_closed(self):
        rows = [{"HOME": 0.7, "DRAW": 0.2, "AWAY": 0.1}]
        self.assertFailCode(
            "LABEL_PROBABILITY_ROW_MISMATCH",
            score_hda_probabilities,
            rows,
            ["HOME", "DRAW"],
            class_order=HDA_CLASS_ORDER,
            probability_tolerance=P_TOL,
            tie_tolerance=TIE_TOL,
        )

    # 20
    def test_empty_input_fails_closed(self):
        self.assertFailCode(
            "EMPTY_SCORE_MATRIX",
            validate_score_matrix,
            [],
            unresolved_tail=False,
            tail_probability=0.0,
            class_order=HDA_CLASS_ORDER,
            probability_tolerance=P_TOL,
            score_support_id="s",
            schema_version="v1",
            required_score_cells=((0, 0),),
        )

    # 21: explicit floating tie case from audit finding
    def test_floating_home_away_difference_5e_17_is_top1_tie(self):
        r = choose_hda_top1(
            {"HOME": 0.40000000000000005, "DRAW": 0.2, "AWAY": 0.4},
            class_order=HDA_CLASS_ORDER,
            probability_tolerance=P_TOL,
            tie_tolerance=TIE_TOL,
        )
        self.assertEqual(r["status"], TOP1_TIE)
        self.assertEqual(set(r["tied_categories"]), {"HOME", "AWAY"})

    # 22
    def test_conflicting_duplicate_score_cell_fails_closed(self):
        c = cells(LOW_SYMMETRIC)
        c.append(ScoreCell(0, 0, 0.21))
        self.assertFailCode(
            "CONFLICTING_DUPLICATE_SCORE_CELL",
            aggregate_score_matrix_to_hda,
            c,
            unresolved_tail=False,
            tail_probability=0.0,
            class_order=HDA_CLASS_ORDER,
            probability_tolerance=P_TOL,
            tie_tolerance=TIE_TOL,
            score_support_id="s",
            schema_version="v1",
            required_score_cells=required(3),
        )

    # 23
    def test_noninteger_score_fails_closed(self):
        c = cells(LOW_SYMMETRIC)
        c[0] = ScoreCell(0.5, 0, 0.20)  # type: ignore[arg-type]
        self.assertFailCode(
            "INVALID_SCORE",
            validate_score_matrix,
            c,
            unresolved_tail=False,
            tail_probability=0.0,
            class_order=HDA_CLASS_ORDER,
            probability_tolerance=P_TOL,
            score_support_id="s",
            schema_version="v1",
            required_score_cells=required(3),
        )

    # 24
    def test_missing_class_order_fails_closed(self):
        self.assertFailCode(
            "MISSING_CLASS_ORDER",
            validate_score_matrix,
            cells(LOW_SYMMETRIC),
            unresolved_tail=False,
            tail_probability=0.0,
            class_order=None,
            probability_tolerance=P_TOL,
            score_support_id="s",
            schema_version="v1",
            required_score_cells=required(3),
        )

    # 25
    def test_missing_tail_status_fails_closed(self):
        self.assertFailCode(
            "MISSING_TAIL_STATUS",
            validate_score_matrix,
            cells(LOW_SYMMETRIC),
            unresolved_tail=None,
            tail_probability=0.0,
            class_order=HDA_CLASS_ORDER,
            probability_tolerance=P_TOL,
            score_support_id="s",
            schema_version="v1",
            required_score_cells=required(3),
        )

    # 26
    def test_tail_mass_status_conflict_fails_closed(self):
        self.assertFailCode(
            "TAIL_STATUS_MASS_CONFLICT",
            validate_score_matrix,
            cells(LOW_SYMMETRIC),
            unresolved_tail=False,
            tail_probability=0.01,
            class_order=HDA_CLASS_ORDER,
            probability_tolerance=P_TOL,
            score_support_id="s",
            schema_version="v1",
            required_score_cells=required(3),
        )

    # 27
    def test_unfrozen_tolerance_fails_closed(self):
        self.assertFailCode(
            "UNFROZEN_PROBABILITY_TOLERANCE",
            validate_score_matrix,
            cells(LOW_SYMMETRIC),
            unresolved_tail=False,
            tail_probability=0.0,
            class_order=HDA_CLASS_ORDER,
            probability_tolerance=1e-10,
            score_support_id="s",
            schema_version="v1",
            required_score_cells=required(3),
        )

    # 28
    def test_tiny_allowed_residual_is_recorded_not_normalized(self):
        matrix = [row[:] for row in LOW_SYMMETRIC]
        matrix[2][2] -= 5e-13
        r = agg(matrix)
        self.assertNotEqual(r["raw_total_probability"], 1.0)
        self.assertLessEqual(abs(r["hda_probability_sum"] - r["raw_known_probability_sum"]), 1e-15)
        self.assertLessEqual(abs(r["probability_residual"]), P_TOL)

    # 29
    def test_invalid_label_fails_closed(self):
        self.assertFailCode(
            "INVALID_HDA_LABEL",
            score_hda_probabilities,
            [{"HOME": 0.7, "DRAW": 0.2, "AWAY": 0.1}],
            ["X"],
            class_order=HDA_CLASS_ORDER,
            probability_tolerance=P_TOL,
            tie_tolerance=TIE_TOL,
        )

    # 30
    def test_missing_probability_fails_closed(self):
        self.assertFailCode(
            "MISSING_OR_EXTRA_HDA_PROBABILITY",
            score_hda_probabilities,
            [{"HOME": 0.7, "DRAW": 0.3}],
            ["HOME"],
            class_order=HDA_CLASS_ORDER,
            probability_tolerance=P_TOL,
            tie_tolerance=TIE_TOL,
        )

    # 31
    def test_empty_metric_sample_fails_closed(self):
        self.assertFailCode(
            "EMPTY_HDA_SAMPLE",
            score_hda_probabilities,
            [],
            [],
            class_order=HDA_CLASS_ORDER,
            probability_tolerance=P_TOL,
            tie_tolerance=TIE_TOL,
        )

    # 32
    def test_draw_metrics_confusion_matrix_includes_tie_without_silent_break(self):
        rows = [
            {"HOME": 0.4, "DRAW": 0.2, "AWAY": 0.4},
            {"HOME": 0.2, "DRAW": 0.6, "AWAY": 0.2},
        ]
        labels = ["HOME", "DRAW"]
        m = draw_classification_metrics(
            rows,
            labels,
            class_order=HDA_CLASS_ORDER,
            probability_tolerance=P_TOL,
            tie_tolerance=TIE_TOL,
        )
        self.assertEqual(m["Top1TieCount"], 1)
        self.assertEqual(m["confusion_matrix"]["HOME"][TOP1_TIE], 1)
        self.assertEqual(m["confusion_matrix"]["DRAW"]["DRAW"], 1)

    # 33
    def test_negative_score_fails_closed(self):
        bad = cells(LOW_SYMMETRIC)
        bad[0] = ScoreCell(-1, 0, bad[0].probability)
        self.assertFailCode(
            "INVALID_SCORE",
            aggregate_score_matrix_to_hda,
            bad,
            unresolved_tail=False,
            tail_probability=0.0,
            class_order=HDA_CLASS_ORDER,
            probability_tolerance=P_TOL,
            tie_tolerance=TIE_TOL,
            score_support_id="synthetic-negative-score",
            schema_version="synthetic-score-support-v1",
            required_score_cells=required(),
        )

    # 34
    def test_score_cell_outside_support_fails_closed(self):
        bad = cells(LOW_SYMMETRIC) + [ScoreCell(3, 0, 0.0)]
        self.assertFailCode(
            "SCORE_CELL_OUTSIDE_SUPPORT",
            aggregate_score_matrix_to_hda,
            bad,
            unresolved_tail=False,
            tail_probability=0.0,
            class_order=HDA_CLASS_ORDER,
            probability_tolerance=P_TOL,
            tie_tolerance=TIE_TOL,
            score_support_id="synthetic-outside-support",
            schema_version="synthetic-score-support-v1",
            required_score_cells=required(),
        )

    # 35
    def test_missing_support_identity_fails_closed(self):
        self.assertFailCode(
            "MISSING_SCORE_SUPPORT_IDENTITY",
            aggregate_score_matrix_to_hda,
            cells(LOW_SYMMETRIC),
            unresolved_tail=False,
            tail_probability=0.0,
            class_order=HDA_CLASS_ORDER,
            probability_tolerance=P_TOL,
            tie_tolerance=TIE_TOL,
            score_support_id=None,
            schema_version=None,
            required_score_cells=required(),
        )

    # 36
    def test_unresolved_tail_zero_mass_fails_closed(self):
        self.assertFailCode(
            "UNRESOLVED_TAIL_REQUIRES_POSITIVE_MASS",
            aggregate_score_matrix_to_hda,
            cells(LOW_SYMMETRIC),
            unresolved_tail=True,
            tail_probability=0.0,
            class_order=HDA_CLASS_ORDER,
            probability_tolerance=P_TOL,
            tie_tolerance=TIE_TOL,
            score_support_id="synthetic-tail-zero",
            schema_version="synthetic-score-support-v1",
            required_score_cells=required(),
        )


if __name__ == "__main__":
    unittest.main()
