#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import numpy as np

LABELS = ("H", "D", "A")
EPS = 1e-12


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sigmoid(values: np.ndarray | float) -> np.ndarray | float:
    clipped = np.clip(values, -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def logit(value: float) -> float:
    p = min(1.0 - EPS, max(EPS, float(value)))
    return math.log(p / (1.0 - p))


@dataclass(frozen=True)
class FitResult:
    coefficients: list[float]
    converged: bool
    iterations: int
    gradient_inf: float
    probability_gate_pass: bool
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "coefficients": self.coefficients,
            "converged": self.converged,
            "iterations": self.iterations,
            "gradient_inf": self.gradient_inf,
            "probability_gate_pass": self.probability_gate_pass,
            "error": self.error,
        }


def fit_offset_logistic(
    x: np.ndarray,
    y: np.ndarray,
    offset: np.ndarray,
    *,
    l2: float,
    positive_weight: float,
    max_iterations: int = 60,
    tolerance: float = 1e-7,
) -> FitResult:
    if x.ndim != 2 or y.ndim != 1 or offset.ndim != 1 or len(x) != len(y) or len(y) != len(offset):
        return FitResult([], False, 0, math.inf, False, "shape mismatch")
    if len(y) < 20 or len(np.unique(y)) < 2:
        return FitResult([], False, 0, math.inf, False, "insufficient binary classes")
    design = np.column_stack([np.ones(len(x)), x.astype(float, copy=False)])
    beta = np.zeros(design.shape[1], dtype=float)
    weights = np.where(y > 0.5, float(positive_weight), 1.0)
    penalty = np.eye(design.shape[1], dtype=float) * float(l2)
    penalty[0, 0] = 0.0
    last_gradient = math.inf
    try:
        for iteration in range(1, max_iterations + 1):
            eta = offset + design @ beta
            p = sigmoid(eta)
            variance = np.clip(p * (1.0 - p), 1e-8, None)
            gradient = design.T @ (weights * (p - y)) + penalty @ beta
            hessian = design.T @ (design * (weights * variance)[:, None]) + penalty
            hessian += np.eye(hessian.shape[0]) * 1e-9
            step = np.linalg.solve(hessian, gradient)
            beta -= step
            last_gradient = float(np.max(np.abs(gradient)))
            if not np.all(np.isfinite(beta)):
                return FitResult([], False, iteration, last_gradient, False, "nonfinite coefficients")
            if float(np.max(np.abs(step))) <= tolerance and last_gradient <= 1e-5:
                probabilities = np.asarray(sigmoid(offset + design @ beta), dtype=float)
                gate = bool(np.all(np.isfinite(probabilities)) and np.all((probabilities > 0) & (probabilities < 1)))
                return FitResult(beta.tolist(), True, iteration, last_gradient, gate)
        probabilities = np.asarray(sigmoid(offset + design @ beta), dtype=float)
        gate = bool(np.all(np.isfinite(probabilities)) and np.all((probabilities > 0) & (probabilities < 1)))
        return FitResult(beta.tolist(), False, max_iterations, last_gradient, gate, "nonconvergence")
    except np.linalg.LinAlgError as exc:
        return FitResult([], False, 0, math.inf, False, f"linear algebra: {exc}")


def predict_draw(fit: FitResult, x: np.ndarray, offset: np.ndarray) -> np.ndarray:
    if not fit.converged or not fit.probability_gate_pass:
        raise ValueError(f"invalid fit: {fit.error}")
    beta = np.asarray(fit.coefficients, dtype=float)
    design = np.column_stack([np.ones(len(x)), x.astype(float, copy=False)])
    probabilities = np.asarray(sigmoid(offset + design @ beta), dtype=float)
    if not np.all(np.isfinite(probabilities)) or np.any(probabilities <= 0) or np.any(probabilities >= 1):
        raise ValueError("draw probability gate failed")
    return probabilities


def hda_from_draw_and_elo(draw: np.ndarray, elo_home_adv: np.ndarray) -> np.ndarray:
    ratio_home = np.asarray(sigmoid(np.asarray(elo_home_adv, dtype=float) * math.log(10.0) / 400.0), dtype=float)
    non_draw = 1.0 - draw
    output = np.column_stack([non_draw * ratio_home, draw, non_draw * (1.0 - ratio_home)])
    if not np.all(np.isfinite(output)) or float(np.max(np.abs(output.sum(axis=1) - 1.0))) > 1e-10:
        raise ValueError("H/D/A probability conservation failed")
    return output


def _argmax_label(row: Sequence[float]) -> str:
    return LABELS[max(range(3), key=lambda index: (float(row[index]), -index))]


def reliability_table(predictions: np.ndarray, labels: Sequence[str], bins: int = 10) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(bins):
        low, high = index / bins, (index + 1) / bins
        mask = (predictions[:, 1] >= low) & ((predictions[:, 1] < high) | ((index == bins - 1) & (predictions[:, 1] == 1.0)))
        count = int(mask.sum())
        if count:
            mean_prediction = float(predictions[mask, 1].mean())
            observed = float(np.mean([1.0 if labels[i] == "D" else 0.0 for i in np.where(mask)[0]]))
            gap = abs(mean_prediction - observed)
        else:
            mean_prediction = observed = gap = None
        rows.append({"bin": index, "low": low, "high": high, "n": count,
                     "mean_predicted_draw": mean_prediction, "observed_draw_rate": observed,
                     "absolute_gap": gap})
    return rows


def calibration_line(predictions: np.ndarray, labels: Sequence[str]) -> dict[str, Any]:
    y = np.asarray([1.0 if label == "D" else 0.0 for label in labels], dtype=float)
    if len(np.unique(y)) < 2:
        return {"status": "UNAVAILABLE_DEGENERATE", "intercept": None, "slope": None}
    x = np.asarray([[logit(value)] for value in predictions[:, 1]], dtype=float)
    fit = fit_offset_logistic(x, y, np.zeros(len(y)), l2=1e-6, positive_weight=1.0)
    if not fit.converged:
        return {"status": "UNAVAILABLE_NONCONVERGENCE", "intercept": None, "slope": None, "fit": fit.as_dict()}
    return {"status": "AVAILABLE", "intercept": fit.coefficients[0], "slope": fit.coefficients[1], "fit": fit.as_dict()}


def metrics(predictions: np.ndarray, labels: Sequence[str]) -> dict[str, Any]:
    if predictions.shape != (len(labels), 3) or not len(labels):
        raise ValueError("metric shape mismatch")
    if not np.all(np.isfinite(predictions)) or np.any(predictions <= 0) or np.any(predictions >= 1):
        raise ValueError("invalid metric probabilities")
    if float(np.max(np.abs(predictions.sum(axis=1) - 1.0))) > 1e-10:
        raise ValueError("metric probability conservation failed")
    predicted = [_argmax_label(row) for row in predictions]
    confusion = {actual: {guess: 0 for guess in LABELS} for actual in LABELS}
    for actual, guess in zip(labels, predicted):
        confusion[actual][guess] += 1
    class_metrics: dict[str, dict[str, float]] = {}
    for label in LABELS:
        tp = confusion[label][label]
        fp = sum(confusion[other][label] for other in LABELS if other != label)
        fn = sum(confusion[label][other] for other in LABELS if other != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0
        class_metrics[label] = {"precision": precision, "recall": recall, "f1": f1}
    actual_indices = np.asarray([LABELS.index(label) for label in labels], dtype=int)
    log_loss = float(-np.log(np.clip(predictions[np.arange(len(labels)), actual_indices], EPS, 1.0)).mean())
    one_hot = np.zeros_like(predictions)
    one_hot[np.arange(len(labels)), actual_indices] = 1.0
    brier = float(np.sum((predictions - one_hot) ** 2, axis=1).mean())
    rps = float(np.mean(((predictions[:, 0] - one_hot[:, 0]) ** 2 +
                         (predictions[:, 0] + predictions[:, 1] - one_hot[:, 0] - one_hot[:, 1]) ** 2) / 2.0))
    reliability = reliability_table(predictions, labels)
    draw_ece = sum(row["n"] / len(labels) * float(row["absolute_gap"] or 0.0) for row in reliability)
    top_ece = 0.0
    confidence = predictions.max(axis=1)
    correctness = np.asarray([1.0 if predicted[i] == labels[i] else 0.0 for i in range(len(labels))])
    for index in range(10):
        mask = (confidence >= index / 10) & ((confidence < (index + 1) / 10) | ((index == 9) & (confidence == 1.0)))
        if mask.any():
            top_ece += float(mask.mean()) * abs(float(confidence[mask].mean()) - float(correctness[mask].mean()))
    return {
        "n": len(labels),
        "Accuracy": sum(confusion[label][label] for label in LABELS) / len(labels),
        "Macro-F1": sum(class_metrics[label]["f1"] for label in LABELS) / 3.0,
        "Draw Precision": class_metrics["D"]["precision"],
        "Draw Recall": class_metrics["D"]["recall"],
        "Draw F1": class_metrics["D"]["f1"],
        "Log Loss": log_loss,
        "Brier": brier,
        "RPS": rps,
        "Draw ECE": draw_ece,
        "Top-label ECE": top_ece,
        "draw_calibration": calibration_line(predictions, labels),
        "reliability_table": reliability,
        "confusion": confusion,
    }


def metric_delta(candidate: dict[str, Any], baseline: dict[str, Any]) -> dict[str, float]:
    keys = ("Accuracy", "Macro-F1", "Draw Precision", "Draw Recall", "Draw F1", "Log Loss", "Brier", "RPS", "Draw ECE", "Top-label ECE")
    return {key: float(candidate[key]) - float(baseline[key]) for key in keys}


def percentile(values: Iterable[float], q: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("empty percentile")
    position = (len(ordered) - 1) * q
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight
