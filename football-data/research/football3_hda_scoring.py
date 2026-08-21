from __future__ import annotations

import math
from typing import Mapping, Sequence

from football3_hda import (
    DEFAULT_PROBABILITY_TOLERANCE,
    DEFAULT_TIE_TOLERANCE,
    HDA_CLASS_ORDER,
    HDAValidationError,
    TOP1_TIE,
    choose_hda_top1,
)

FOOTBALL3_HDA_SCORING_INFRASTRUCTURE = "PURE_HDA_PROBABILITY_SCORING_NO_IO_NO_TRAINING"


def _fail(code: str, message: str) -> None:
    raise HDAValidationError(code, message)


def _require_contract_constants(
    class_order: Sequence[str] | None,
    probability_tolerance: float | None,
    tie_tolerance: float | None,
) -> None:
    if class_order is None or tuple(class_order) != HDA_CLASS_ORDER:
        _fail("INVALID_CLASS_ORDER", f"class_order must be {list(HDA_CLASS_ORDER)}")
    if (
        probability_tolerance is None
        or isinstance(probability_tolerance, bool)
        or not isinstance(probability_tolerance, (int, float))
        or float(probability_tolerance) != DEFAULT_PROBABILITY_TOLERANCE
    ):
        _fail("UNFROZEN_PROBABILITY_TOLERANCE", f"probability_tolerance is frozen at {DEFAULT_PROBABILITY_TOLERANCE:g}")
    if (
        tie_tolerance is None
        or isinstance(tie_tolerance, bool)
        or not isinstance(tie_tolerance, (int, float))
        or float(tie_tolerance) != DEFAULT_TIE_TOLERANCE
    ):
        _fail("UNFROZEN_TIE_TOLERANCE", f"tie_tolerance is frozen at {DEFAULT_TIE_TOLERANCE:g}")


def _validate_metric_inputs(
    probability_rows: Sequence[Mapping[str, float]],
    labels: Sequence[str],
    *,
    class_order: Sequence[str] | None,
    probability_tolerance: float | None,
    tie_tolerance: float | None,
) -> list[dict[str, float]]:
    _require_contract_constants(class_order, probability_tolerance, tie_tolerance)
    if not probability_rows:
        _fail("EMPTY_HDA_SAMPLE", "probability_rows cannot be empty")
    if len(probability_rows) != len(labels):
        _fail("LABEL_PROBABILITY_ROW_MISMATCH", "labels and probability_rows must have equal length")

    rows: list[dict[str, float]] = []
    for row_index, row in enumerate(probability_rows):
        if set(row) != set(HDA_CLASS_ORDER):
            _fail("MISSING_OR_EXTRA_HDA_PROBABILITY", f"row {row_index}: HDA keys must be exactly {list(HDA_CLASS_ORDER)}")
        checked: dict[str, float] = {}
        for category in HDA_CLASS_ORDER:
            value = row[category]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                _fail("INVALID_HDA_PROBABILITY", f"row {row_index} {category}: probability must be numeric")
            value = float(value)
            if not math.isfinite(value):
                _fail("NONFINITE_HDA_PROBABILITY", f"row {row_index} {category}: probability must be finite")
            if value < 0.0 or value > 1.0:
                _fail("INVALID_HDA_PROBABILITY", f"row {row_index} {category}: probability must lie in [0,1]")
            checked[category] = value
        total = math.fsum(checked.values())
        if abs(total - 1.0) > DEFAULT_PROBABILITY_TOLERANCE:
            _fail("HDA_PROBABILITY_MASS_NOT_CONSERVED", f"row {row_index}: HDA probability sum is {total:.17g}")
        rows.append(checked)

    for row_index, truth in enumerate(labels):
        if truth not in HDA_CLASS_ORDER:
            _fail("INVALID_HDA_LABEL", f"label at row {row_index} must be HOME/DRAW/AWAY")
    return rows


def draw_classification_metrics(
    probability_rows: Sequence[Mapping[str, float]],
    labels: Sequence[str],
    *,
    class_order: Sequence[str] | None,
    probability_tolerance: float | None,
    tie_tolerance: float | None,
) -> dict[str, object]:
    """Synthetic/authorized-label diagnostics; never performs I/O or fitting."""
    rows = _validate_metric_inputs(
        probability_rows,
        labels,
        class_order=class_order,
        probability_tolerance=probability_tolerance,
        tie_tolerance=tie_tolerance,
    )
    predicted: list[str | None] = []
    tie_count = 0
    for row in rows:
        decision = choose_hda_top1(
            row,
            class_order=HDA_CLASS_ORDER,
            probability_tolerance=probability_tolerance,
            tie_tolerance=tie_tolerance,
        )
        if decision["status"] == TOP1_TIE:
            predicted.append(None)
            tie_count += 1
        else:
            predicted.append(str(decision["top1_category"]))

    n = len(labels)
    correct = sum(int(pred == truth) for pred, truth in zip(predicted, labels))
    draw_calls = sum(pred == "DRAW" for pred in predicted)
    tp = sum(pred == "DRAW" and truth == "DRAW" for pred, truth in zip(predicted, labels))
    fp = sum(pred == "DRAW" and truth != "DRAW" for pred, truth in zip(predicted, labels))
    fn = sum(pred != "DRAW" and truth == "DRAW" for pred, truth in zip(predicted, labels))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    columns = (*HDA_CLASS_ORDER, TOP1_TIE)
    confusion = {truth: {pred: 0 for pred in columns} for truth in HDA_CLASS_ORDER}
    for pred, truth in zip(predicted, labels):
        confusion[truth][pred if pred is not None else TOP1_TIE] += 1

    return {
        "Accuracy": correct / n,
        "DrawPrecision": precision,
        "DrawRecall": recall,
        "DrawF1": f1,
        "DrawCalls": draw_calls,
        "DrawCallCoverage": draw_calls / n,
        "Top1TieCount": tie_count,
        "confusion_matrix": confusion,
        "classification_metrics_diagnostic_only": True,
    }


def score_hda_probabilities(
    probability_rows: Sequence[Mapping[str, float]],
    labels: Sequence[str],
    *,
    class_order: Sequence[str] | None,
    probability_tolerance: float | None,
    tie_tolerance: float | None,
) -> dict[str, object]:
    """Compute HDA proper scores plus diagnostics for already supplied labels.

    This is pure probability scoring infrastructure: no files, network, subprocess,
    model fitting, prediction, or label acquisition. Real-label callers must be bound
    to a valid football3 V2 experiment contract/helper by the production guard.
    """
    rows = _validate_metric_inputs(
        probability_rows,
        labels,
        class_order=class_order,
        probability_tolerance=probability_tolerance,
        tie_tolerance=tie_tolerance,
    )
    n = len(rows)
    ll_total = 0.0
    brier_total = 0.0
    rps_total = 0.0
    residual_max = 0.0
    for row, truth in zip(rows, labels):
        p_true = row[truth]
        if p_true == 0.0:
            ll_total = math.inf
        elif math.isfinite(ll_total):
            ll_total += -math.log(p_true)
        onehot = {category: 1.0 if category == truth else 0.0 for category in HDA_CLASS_ORDER}
        brier_total += math.fsum((row[category] - onehot[category]) ** 2 for category in HDA_CLASS_ORDER)
        cdf_p_home = row["HOME"]
        cdf_y_home = onehot["HOME"]
        cdf_p_home_draw = row["HOME"] + row["DRAW"]
        cdf_y_home_draw = onehot["HOME"] + onehot["DRAW"]
        rps_total += ((cdf_p_home - cdf_y_home) ** 2 + (cdf_p_home_draw - cdf_y_home_draw) ** 2) / 2.0
        residual_max = max(residual_max, abs(math.fsum(row.values()) - 1.0))

    diagnostics = draw_classification_metrics(
        rows,
        labels,
        class_order=HDA_CLASS_ORDER,
        probability_tolerance=probability_tolerance,
        tie_tolerance=tie_tolerance,
    )
    out: dict[str, object] = {
        "class_order": list(HDA_CLASS_ORDER),
        "sample_count": n,
        "LogLoss": math.inf if math.isinf(ll_total) else ll_total / n,
        "Brier": brier_total / n,
        "RPS": rps_total / n,
        "probability_residual_max": residual_max,
        "probability_conservation_pass": residual_max <= DEFAULT_PROBABILITY_TOLERANCE,
        "proper_scores_primary": True,
        "classification_metrics_can_override_proper_score_failure": False,
    }
    out.update(diagnostics)
    return out
