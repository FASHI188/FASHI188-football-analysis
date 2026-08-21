from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

HDA_CLASS_ORDER: tuple[str, str, str] = ("HOME", "DRAW", "AWAY")
DEFAULT_PROBABILITY_TOLERANCE = 1e-12
DEFAULT_TIE_TOLERANCE = 1e-12
HDA_SCHEMA_VERSION = "football3_hda_v1"
PARTIAL_HDA_UNRESOLVED_TAIL = "PARTIAL_HDA_UNRESOLVED_TAIL"
ROBUST_PARTIAL_TOP1 = "ROBUST_PARTIAL_TOP1"
TOP1_UNRESOLVED_DUE_TO_TAIL = "TOP1_UNRESOLVED_DUE_TO_TAIL"
TOP1_TIE = "TOP1_TIE"
TOP1 = "TOP1"
COMPLETE_HDA = "COMPLETE_HDA"
K2_PER_ROW_HDA_RECOMPUTATION_NOT_AUTHORIZED = "K2_PER_ROW_HDA_RECOMPUTATION_NOT_AUTHORIZED"


class HDAValidationError(ValueError):
    """Fail-closed HDA contract violation with a stable machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ScoreCell:
    home_goals: int
    away_goals: int
    probability: float

    @property
    def score(self) -> tuple[int, int]:
        return (self.home_goals, self.away_goals)


def _fail(code: str, message: str) -> None:
    raise HDAValidationError(code, message)


def _require_fixed_probability_tolerance(value: float | None) -> float:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("MISSING_OR_INVALID_PROBABILITY_TOLERANCE", "probability_tolerance must be explicitly declared")
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        _fail("MISSING_OR_INVALID_PROBABILITY_TOLERANCE", "probability_tolerance must be finite and positive")
    if value != DEFAULT_PROBABILITY_TOLERANCE:
        _fail(
            "UNFROZEN_PROBABILITY_TOLERANCE",
            f"probability_tolerance is frozen at {DEFAULT_PROBABILITY_TOLERANCE:g}",
        )
    return value


def _require_fixed_tie_tolerance(value: float | None) -> float:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("MISSING_OR_INVALID_TIE_TOLERANCE", "tie_tolerance must be explicitly declared")
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        _fail("MISSING_OR_INVALID_TIE_TOLERANCE", "tie_tolerance must be finite and positive")
    if value != DEFAULT_TIE_TOLERANCE:
        _fail("UNFROZEN_TIE_TOLERANCE", f"tie_tolerance is frozen at {DEFAULT_TIE_TOLERANCE:g}")
    return value


def _validate_class_order(class_order: Sequence[str] | None) -> tuple[str, str, str]:
    if class_order is None:
        _fail("MISSING_CLASS_ORDER", "class_order must be explicitly declared")
    order = tuple(class_order)
    if order != HDA_CLASS_ORDER:
        _fail("INVALID_CLASS_ORDER", f"class_order must be {list(HDA_CLASS_ORDER)}")
    return HDA_CLASS_ORDER


def _validate_support_identity(score_support_id: str | None, schema_version: str | None) -> tuple[str | None, str | None]:
    support_ok = isinstance(score_support_id, str) and bool(score_support_id.strip())
    schema_ok = isinstance(schema_version, str) and bool(schema_version.strip())
    if not support_ok and not schema_ok:
        _fail("MISSING_SCORE_SUPPORT_IDENTITY", "score_support_id or schema_version must be declared")
    return (score_support_id.strip() if support_ok else None, schema_version.strip() if schema_ok else None)


def _validate_required_cells(required_score_cells: Iterable[tuple[int, int]] | None) -> tuple[tuple[int, int], ...]:
    if required_score_cells is None:
        _fail("MISSING_SUPPORT_CONTRACT", "required_score_cells must be explicitly supplied by the score-support contract")
    seen: set[tuple[int, int]] = set()
    for raw in required_score_cells:
        if not isinstance(raw, tuple) or len(raw) != 2:
            _fail("INVALID_REQUIRED_SCORE_CELL", f"invalid required score cell {raw!r}")
        h, a = raw
        if type(h) is not int or type(a) is not int or h < 0 or a < 0:
            _fail("INVALID_REQUIRED_SCORE_CELL", f"invalid required score cell {raw!r}")
        seen.add((h, a))
    if not seen:
        _fail("EMPTY_SUPPORT_CONTRACT", "required_score_cells cannot be empty")
    if not any(h == a for h, a in seen):
        _fail("SUPPORT_CONTRACT_HAS_NO_DIAGONAL", "score-support contract must declare at least one diagonal cell")
    return tuple(sorted(seen))


def _coerce_cell(raw: ScoreCell | Mapping[str, object]) -> ScoreCell:
    if isinstance(raw, ScoreCell):
        cell = raw
    elif isinstance(raw, Mapping):
        missing = [k for k in ("home_goals", "away_goals", "probability") if k not in raw]
        if missing:
            _fail("MISSING_SCORE_CELL_FIELD", f"score cell missing fields: {missing}")
        cell = ScoreCell(raw["home_goals"], raw["away_goals"], raw["probability"])  # type: ignore[arg-type]
    else:
        _fail("INVALID_SCORE_CELL", f"score cell must be ScoreCell or mapping, got {type(raw).__name__}")

    h, a, p = cell.home_goals, cell.away_goals, cell.probability
    if type(h) is not int or type(a) is not int or h < 0 or a < 0:
        _fail("INVALID_SCORE", f"score must contain nonnegative integers, got {(h, a)!r}")
    if isinstance(p, bool) or not isinstance(p, (int, float)):
        _fail("INVALID_PROBABILITY", f"probability for {(h, a)} must be numeric")
    p = float(p)
    if not math.isfinite(p):
        _fail("NONFINITE_PROBABILITY", f"probability for {(h, a)} must be finite")
    if p < 0:
        _fail("NEGATIVE_PROBABILITY", f"probability for {(h, a)} is negative")
    if p > 1:
        _fail("PROBABILITY_ABOVE_ONE", f"probability for {(h, a)} exceeds 1")
    return ScoreCell(h, a, p)


def validate_score_matrix(
    score_cells: Iterable[ScoreCell | Mapping[str, object]],
    *,
    unresolved_tail: bool | None,
    tail_probability: float | None,
    class_order: Sequence[str] | None,
    probability_tolerance: float | None,
    score_support_id: str | None,
    schema_version: str | None,
    required_score_cells: Iterable[tuple[int, int]] | None,
) -> dict[str, object]:
    """Validate a score-probability support without normalization or silent repair."""

    tol = _require_fixed_probability_tolerance(probability_tolerance)
    order = _validate_class_order(class_order)
    support_id, schema = _validate_support_identity(score_support_id, schema_version)
    required = _validate_required_cells(required_score_cells)

    if type(unresolved_tail) is not bool:
        _fail("MISSING_TAIL_STATUS", "unresolved_tail must be explicitly declared as bool")
    if tail_probability is None or isinstance(tail_probability, bool) or not isinstance(tail_probability, (int, float)):
        _fail("MISSING_OR_INVALID_TAIL_PROBABILITY", "tail_probability must be explicitly declared")
    tail_probability = float(tail_probability)
    if not math.isfinite(tail_probability):
        _fail("NONFINITE_TAIL_PROBABILITY", "tail_probability must be finite")
    if tail_probability < 0 or tail_probability > 1:
        _fail("INVALID_TAIL_PROBABILITY", "tail_probability must lie in [0,1]")
    if not unresolved_tail and abs(tail_probability) > tol:
        _fail("TAIL_STATUS_MASS_CONFLICT", "resolved support cannot declare material unresolved tail mass")
    if unresolved_tail and tail_probability <= tol:
        _fail("UNRESOLVED_TAIL_REQUIRES_POSITIVE_MASS", "unresolved_tail=True requires positive tail_probability")

    cells: list[ScoreCell] = []
    by_score: dict[tuple[int, int], float] = {}
    for raw in score_cells:
        cell = _coerce_cell(raw)
        if cell.score in by_score:
            if by_score[cell.score] == cell.probability:
                _fail("DUPLICATE_SCORE_CELL", f"duplicate score cell {cell.score}")
            _fail("CONFLICTING_DUPLICATE_SCORE_CELL", f"conflicting probabilities for score cell {cell.score}")
        by_score[cell.score] = cell.probability
        cells.append(cell)
    if not cells:
        _fail("EMPTY_SCORE_MATRIX", "score matrix cannot be empty")

    required_set = set(required)
    present = set(by_score)
    missing_diagonal = sorted(cell for cell in required_set - present if cell[0] == cell[1])
    if missing_diagonal:
        _fail("MISSING_REQUIRED_DIAGONAL", f"missing required diagonal cells: {missing_diagonal}")
    missing = sorted(required_set - present)
    if missing:
        _fail("MISSING_REQUIRED_SCORE_CELL", f"missing required score cells: {missing}")

    unknown = sorted(present - required_set)
    if unknown:
        _fail("SCORE_CELL_OUTSIDE_SUPPORT", f"score cells outside declared support: {unknown}")

    raw_known_sum = math.fsum(cell.probability for cell in cells)
    raw_total = raw_known_sum + tail_probability
    residual = raw_total - 1.0
    if abs(residual) > tol:
        _fail(
            "PROBABILITY_MASS_NOT_CONSERVED",
            f"raw_known_sum={raw_known_sum:.17g}, tail_probability={tail_probability:.17g}, raw_total={raw_total:.17g}, residual={residual:.3g}",
        )

    return {
        "status": "VALID_SCORE_MATRIX",
        "class_order": list(order),
        "probability_tolerance": tol,
        "score_support_id": support_id,
        "schema_version": schema,
        "unresolved_tail": unresolved_tail,
        "tail_probability": tail_probability,
        "raw_known_probability_sum": raw_known_sum,
        "raw_total_probability": raw_total,
        "probability_residual": residual,
        "score_cell_count": len(cells),
        "required_score_cell_count": len(required),
        "cells": cells,
    }


def _validate_hda_probability_mapping(
    probabilities: Mapping[str, float],
    *,
    class_order: Sequence[str] | None,
    probability_tolerance: float | None,
) -> dict[str, float]:
    tol = _require_fixed_probability_tolerance(probability_tolerance)
    order = _validate_class_order(class_order)
    if set(probabilities) != set(order):
        _fail("MISSING_OR_EXTRA_HDA_PROBABILITY", f"HDA probability keys must be exactly {list(order)}")
    out: dict[str, float] = {}
    for label in order:
        p = probabilities[label]
        if isinstance(p, bool) or not isinstance(p, (int, float)):
            _fail("INVALID_HDA_PROBABILITY", f"probability for {label} must be numeric")
        p = float(p)
        if not math.isfinite(p):
            _fail("NONFINITE_HDA_PROBABILITY", f"probability for {label} must be finite")
        if p < 0 or p > 1:
            _fail("INVALID_HDA_PROBABILITY", f"probability for {label} must lie in [0,1]")
        out[label] = p
    total = math.fsum(out[label] for label in order)
    if abs(total - 1.0) > tol:
        _fail("HDA_PROBABILITY_MASS_NOT_CONSERVED", f"HDA probabilities sum to {total:.17g}")
    return out


def choose_hda_top1(
    probabilities: Mapping[str, float],
    *,
    class_order: Sequence[str] | None,
    probability_tolerance: float | None,
    tie_tolerance: float | None,
) -> dict[str, object]:
    """Choose HDA Top1 without allowing array order to break a numerical tie."""

    probs = _validate_hda_probability_mapping(
        probabilities, class_order=class_order, probability_tolerance=probability_tolerance
    )
    tie_tol = _require_fixed_tie_tolerance(tie_tolerance)
    max_prob = max(probs.values())
    tied = [label for label in HDA_CLASS_ORDER if max_prob - probs[label] <= tie_tol]
    ordered_values = sorted(probs.values(), reverse=True)
    margin = ordered_values[0] - ordered_values[1]
    if margin <= tie_tol:
        return {
            "status": TOP1_TIE,
            "top1_category": None,
            "tied_categories": tied,
            "top1_margin": margin,
            "near_tie": True,
            "tie_tolerance": tie_tol,
        }
    winner = max(probs, key=probs.__getitem__)
    return {
        "status": TOP1,
        "top1_category": winner,
        "tied_categories": [],
        "top1_margin": margin,
        "near_tie": False,
        "tie_tolerance": tie_tol,
    }


def _score_class(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "HOME"
    if home_goals == away_goals:
        return "DRAW"
    return "AWAY"


def _max_specific_score(cells: Sequence[ScoreCell], tie_tolerance: float) -> dict[str, object]:
    max_p = max(cell.probability for cell in cells)
    maxima = sorted(cell.score for cell in cells if max_p - cell.probability <= tie_tolerance)
    if len(maxima) != 1:
        return {
            "max_specific_score": None,
            "max_specific_scores": maxima,
            "max_specific_score_probability": max_p,
            "max_specific_score_is_draw": None,
            "exact_score_top1_category": None,
            "exact_score_top1_status": "EXACT_SCORE_TOP1_TIE",
        }
    score = maxima[0]
    category = _score_class(*score)
    return {
        "max_specific_score": score,
        "max_specific_scores": maxima,
        "max_specific_score_probability": max_p,
        "max_specific_score_is_draw": score[0] == score[1],
        "exact_score_top1_category": category,
        "exact_score_top1_status": "EXACT_SCORE_TOP1",
    }


def aggregate_score_matrix_to_hda(
    score_cells: Iterable[ScoreCell | Mapping[str, object]],
    *,
    unresolved_tail: bool | None,
    tail_probability: float | None,
    class_order: Sequence[str] | None,
    probability_tolerance: float | None,
    tie_tolerance: float | None,
    score_support_id: str | None,
    schema_version: str | None,
    required_score_cells: Iterable[tuple[int, int]] | None,
) -> dict[str, object]:
    """Aggregate exact score probabilities into HOME/DRAW/AWAY, fail-closed on unresolved support."""

    validation = validate_score_matrix(
        score_cells,
        unresolved_tail=unresolved_tail,
        tail_probability=tail_probability,
        class_order=class_order,
        probability_tolerance=probability_tolerance,
        score_support_id=score_support_id,
        schema_version=schema_version,
        required_score_cells=required_score_cells,
    )
    tie_tol = _require_fixed_tie_tolerance(tie_tolerance)
    cells = validation["cells"]
    assert isinstance(cells, list)
    known_values: dict[str, list[float]] = {label: [] for label in HDA_CLASS_ORDER}
    for cell in cells:
        assert isinstance(cell, ScoreCell)
        known_values[_score_class(cell.home_goals, cell.away_goals)].append(cell.probability)
    known = {label: math.fsum(known_values[label]) for label in HDA_CLASS_ORDER}

    exact = _max_specific_score(cells, tie_tol)
    tail = float(validation["tail_probability"])
    base: dict[str, object] = {
        "class_order": list(HDA_CLASS_ORDER),
        "score_support_id": validation["score_support_id"],
        "schema_version": validation["schema_version"],
        "probability_tolerance": validation["probability_tolerance"],
        "tie_tolerance": tie_tol,
        "raw_known_probability_sum": validation["raw_known_probability_sum"],
        "raw_total_probability": validation["raw_total_probability"],
        "probability_residual": validation["probability_residual"],
        "diagonal_probability_sum": known["DRAW"],
        "known_home_mass": known["HOME"],
        "known_draw_mass": known["DRAW"],
        "known_away_mass": known["AWAY"],
        "unresolved_tail_mass": tail,
        **exact,
    }

    if bool(validation["unresolved_tail"]):
        bounds = {
            label: {"lower": known[label], "upper": known[label] + tail}
            for label in HDA_CLASS_ORDER
        }
        robust: str | None = None
        robust_margin: float | None = None
        for label in HDA_CLASS_ORDER:
            lower = bounds[label]["lower"]
            competitor_upper = max(bounds[other]["upper"] for other in HDA_CLASS_ORDER if other != label)
            margin = lower - competitor_upper
            if margin > tie_tol:
                robust = label
                robust_margin = margin
                break
        base.update(
            {
                "status": PARTIAL_HDA_UNRESOLVED_TAIL,
                "home_probability": None,
                "draw_probability": None,
                "away_probability": None,
                "hda_probability_sum": None,
                "hda_bounds": bounds,
                "top1_status": ROBUST_PARTIAL_TOP1 if robust is not None else TOP1_UNRESOLVED_DUE_TO_TAIL,
                "top1_category": robust,
                "top1_margin": robust_margin,
                "near_tie": None,
                "tied_categories": [],
            }
        )
    else:
        probs = dict(known)
        decision = choose_hda_top1(
            probs,
            class_order=HDA_CLASS_ORDER,
            probability_tolerance=probability_tolerance,
            tie_tolerance=tie_tolerance,
        )
        base.update(
            {
                "status": COMPLETE_HDA,
                "home_probability": probs["HOME"],
                "draw_probability": probs["DRAW"],
                "away_probability": probs["AWAY"],
                "hda_probability_sum": math.fsum(probs.values()),
                "hda_bounds": {label: {"lower": probs[label], "upper": probs[label]} for label in HDA_CLASS_ORDER},
                "top1_status": decision["status"],
                "top1_category": decision["top1_category"],
                "top1_margin": decision["top1_margin"],
                "near_tie": decision["near_tie"],
                "tied_categories": decision["tied_categories"],
            }
        )

    exact_category = base["exact_score_top1_category"]
    hda_top1 = base["top1_category"]
    base["exact_score_top1_and_hda_top1_agree"] = (
        None if exact_category is None or hda_top1 is None else exact_category == hda_top1
    )
    return base


def _validate_metric_inputs(
    probability_rows: Sequence[Mapping[str, float]],
    labels: Sequence[str],
    *,
    class_order: Sequence[str] | None,
    probability_tolerance: float | None,
) -> list[dict[str, float]]:
    _validate_class_order(class_order)
    _require_fixed_probability_tolerance(probability_tolerance)
    if not probability_rows:
        _fail("EMPTY_HDA_SAMPLE", "probability_rows cannot be empty")
    if len(probability_rows) != len(labels):
        _fail("LABEL_PROBABILITY_ROW_MISMATCH", "labels and probability_rows must have equal length")
    rows: list[dict[str, float]] = []
    for i, row in enumerate(probability_rows):
        try:
            rows.append(
                _validate_hda_probability_mapping(
                    row,
                    class_order=HDA_CLASS_ORDER,
                    probability_tolerance=probability_tolerance,
                )
            )
        except HDAValidationError as exc:
            _fail(exc.code, f"row {i}: {exc.message}")
    for i, label in enumerate(labels):
        if label not in HDA_CLASS_ORDER:
            _fail("INVALID_HDA_LABEL", f"label at row {i} must be HOME/DRAW/AWAY")
    return rows


def draw_classification_metrics(
    probability_rows: Sequence[Mapping[str, float]],
    labels: Sequence[str],
    *,
    class_order: Sequence[str] | None,
    probability_tolerance: float | None,
    tie_tolerance: float | None,
) -> dict[str, object]:
    rows = _validate_metric_inputs(
        probability_rows,
        labels,
        class_order=class_order,
        probability_tolerance=probability_tolerance,
    )
    _require_fixed_tie_tolerance(tie_tolerance)
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
    """Compute HDA proper scores and diagnostics for already supplied labels.

    This function performs no I/O and no model fitting. The caller is responsible for
    label-access authorization. Proper scores are primary; Accuracy/F1 remain diagnostics.
    """

    rows = _validate_metric_inputs(
        probability_rows,
        labels,
        class_order=class_order,
        probability_tolerance=probability_tolerance,
    )
    _require_fixed_tie_tolerance(tie_tolerance)
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
        onehot = {label: 1.0 if label == truth else 0.0 for label in HDA_CLASS_ORDER}
        brier_total += math.fsum((row[label] - onehot[label]) ** 2 for label in HDA_CLASS_ORDER)
        cdf_p_home = row["HOME"]
        cdf_y_home = onehot["HOME"]
        cdf_p_home_draw = row["HOME"] + row["DRAW"]
        cdf_y_home_draw = onehot["HOME"] + onehot["DRAW"]
        rps_total += ((cdf_p_home - cdf_y_home) ** 2 + (cdf_p_home_draw - cdf_y_home_draw) ** 2) / 2.0
        residual_max = max(residual_max, abs(math.fsum(row.values()) - 1.0))

    classification = draw_classification_metrics(
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
    out.update(classification)
    return out
