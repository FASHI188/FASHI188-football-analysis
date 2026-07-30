#!/usr/bin/env python3
"""E3e-0 research: pure 90-minute H/D/A draw-identifiability diagnosis.

This experiment freezes the 6,251 market-complete Big-Five rolling-OOS rows and
fixed B100. It does not train or evaluate score, total-goal, or BTTS outputs and
has no joint-matrix promotion gate. OU/AH may be used only as pre-match input
features.

Feature groups:
A. market-only pre-match features;
B. non-market team/model-state features;
C. market + team features.

For every feature group, compare:
- linear logistic regression;
- nonlinear histogram gradient boosting.

First diagnose q=P(Draw|X). Then fit r=P(Home|Non-Draw,X) and derive
P(D)=q, P(H)=(1-q)r, P(A)=(1-q)(1-r).

No class weights, no manual draw threshold, no target-season fitting, no formal
mutation. Candidate ranking thresholds are descriptive only. formal_weight=0.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

HERE = Path(__file__).resolve().parent
FD = HERE.parent
for path in (FD / "engine", FD / "validation", HERE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import big5_high_completeness_b100 as b100  # noqa: E402
import e3d1_direct_td_joint_head as e3d1  # noqa: E402
import market_joint_direct_outcome_e3b1 as e3b1  # noqa: E402
import matrix_draw_gate_e3a as e3a  # noqa: E402
from platform_core import ROOT  # noqa: E402

OUT = ROOT.parent / "artifacts/research/e3e0_draw_identifiability"
OUTCOMES = ("home", "draw", "away")
INDEX = {name: index for index, name in enumerate(OUTCOMES)}
EPS = 1e-12
MIN_TRAIN = 650
BOOTSTRAP_RESAMPLES = 250
SEED = 3500
TOP_FRACTIONS = (0.05, 0.10, 0.15, 0.20)
PRECISION_TARGETS = (0.30, 0.35)
TEAM_SAMPLE_KEYS = (
    "mu_total", "allocation_home_share", "ess", "home_score_signal",
    "away_score_signal", "home_direct_total_rate", "away_direct_total_rate",
    "home_raw_matches", "away_raw_matches", "home_effective_matches",
    "away_effective_matches",
)


def repository_head() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT.parent, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def market_feature(record: dict[str, Any]) -> tuple[list[float], list[str]]:
    values = [finite(value) for value in record["market_x"]]
    names = [f"market_{name}" for name in record["market_names"]]
    if len(values) != len(names):
        raise RuntimeError("market feature schema mismatch")
    return values, names


def team_feature(record: dict[str, Any]) -> tuple[list[float], list[str]]:
    values: list[float] = []
    names: list[str] = []
    champion = record["champion_probs"]
    for label in OUTCOMES:
        values.append(finite(champion[label]))
        names.append(f"champion_{label}")
    values.extend((finite(record.get("strength_gap")), finite(record.get("allocation_gap"))))
    names.extend(("strength_gap", "allocation_gap"))
    exact = dict(record.get("p_total_exact", {}))
    for total in range(7):
        values.append(finite(exact.get(str(total))))
        names.append(f"team_total_{total}")
    values.append(sum(finite(value) for key, value in exact.items() if int(key) >= 7))
    names.append("team_total_7plus")
    sample = dict(record.get("team_sample", {}))
    for key in TEAM_SAMPLE_KEYS:
        raw = e3b1.number(sample.get(key))
        if key == "ess" and raw is not None:
            raw = math.log1p(max(0.0, raw))
        values.extend((0.0 if raw is None else float(raw), 0.0 if raw is None else 1.0))
        names.extend((f"team_{key}", f"team_{key}_available"))
    for competition_id in b100.BIG5:
        values.append(1.0 if record["competition_id"] == competition_id else 0.0)
        names.append(f"league_{competition_id}")
    if not all(math.isfinite(value) for value in values):
        raise RuntimeError("non-finite team feature")
    return values, names


def feature_vector(record: dict[str, Any], group: str) -> tuple[list[float], list[str]]:
    market_values, market_names = market_feature(record)
    team_values, team_names = team_feature(record)
    if group == "A_MARKET":
        return market_values, market_names
    if group == "B_TEAM":
        return team_values, team_names
    if group == "C_COMBINED":
        return market_values + team_values, market_names + team_names
    raise ValueError(group)


def matrix_for_rows(rows: list[dict[str, Any]], group: str) -> tuple[np.ndarray, list[str]]:
    values = []
    names: list[str] | None = None
    for record in rows:
        row_values, row_names = feature_vector(record, group)
        if names is None:
            names = row_names
        elif row_names != names:
            raise RuntimeError(f"feature schema drift for {group}")
        values.append(row_values)
    return np.asarray(values, dtype=float), list(names or [])


def make_estimator(kind: str, seed: int) -> Any:
    if kind == "LOGISTIC":
        return Pipeline([
            ("scale", StandardScaler()),
            ("model", LogisticRegression(
                C=0.35, penalty="l2", solver="lbfgs", max_iter=1200,
                random_state=seed,
            )),
        ])
    if kind == "TREE":
        return HistGradientBoostingClassifier(
            learning_rate=0.045, max_iter=220, max_leaf_nodes=15,
            min_samples_leaf=55, l2_regularization=3.0, max_bins=127,
            random_state=seed,
        )
    raise ValueError(kind)


def positive_probability(model: Any, x: np.ndarray) -> np.ndarray:
    classes = list(model.classes_)
    if 1 not in classes:
        return np.zeros(len(x), dtype=float)
    return np.asarray(model.predict_proba(x)[:, classes.index(1)], dtype=float)


def fallback_probs(record: dict[str, Any], group: str) -> tuple[float, float]:
    source = record["market_probs"] if group in ("A_MARKET", "C_COMBINED") else record["champion_probs"]
    q = finite(source["draw"])
    nondraw = finite(source["home"]) + finite(source["away"])
    r = finite(source["home"]) / max(EPS, nondraw)
    return min(1.0, max(0.0, q)), min(1.0, max(0.0, r))


def rolling_oof(rows: list[dict[str, Any]], group: str, kind: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_year: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_year[int(row["season_start_year"])].append(row)
    output: list[dict[str, Any]] = []
    folds: list[dict[str, Any]] = []
    for target_year in sorted(by_year):
        prior = [row for year in sorted(by_year) if year < target_year for row in by_year[year]]
        current = sorted(by_year[target_year], key=lambda row: (row["date"], row["competition_id"], row["match_key"]))
        train_draws = sum(row["actual_outcome"] == "draw" for row in prior)
        train_nondraw_home = sum(row["actual_outcome"] == "home" for row in prior)
        train_nondraw_away = sum(row["actual_outcome"] == "away" for row in prior)
        trainable = (
            len(prior) >= MIN_TRAIN and train_draws > 0 and train_draws < len(prior)
            and train_nondraw_home > 0 and train_nondraw_away > 0
        )
        if trainable:
            x_train, names = matrix_for_rows(prior, group)
            x_current, current_names = matrix_for_rows(current, group)
            if current_names != names:
                raise RuntimeError("OOF feature schema mismatch")
            y_draw = np.asarray([int(row["actual_outcome"] == "draw") for row in prior], dtype=int)
            nondraw_index = np.asarray([index for index, row in enumerate(prior) if row["actual_outcome"] != "draw"], dtype=int)
            y_home = np.asarray([int(prior[index]["actual_outcome"] == "home") for index in nondraw_index], dtype=int)
            draw_model = make_estimator(kind, SEED + target_year)
            home_model = make_estimator(kind, SEED + 100 + target_year)
            draw_model.fit(x_train, y_draw)
            home_model.fit(x_train[nondraw_index], y_home)
            q_values = positive_probability(draw_model, x_current)
            r_values = positive_probability(home_model, x_current)
            status = "MODELED"
        else:
            names = feature_vector(current[0], group)[1] if current else []
            q_values = np.asarray([fallback_probs(row, group)[0] for row in current], dtype=float)
            r_values = np.asarray([fallback_probs(row, group)[1] for row in current], dtype=float)
            status = "BASELINE_FALLBACK"
        for row, q_raw, r_raw in zip(current, q_values, r_values):
            q = min(1.0, max(0.0, float(q_raw)))
            r = min(1.0, max(0.0, float(r_raw)))
            probabilities = {
                "draw": q,
                "home": (1.0 - q) * r,
                "away": (1.0 - q) * (1.0 - r),
            }
            total = sum(probabilities.values())
            probabilities = {label: value / max(EPS, total) for label, value in probabilities.items()}
            item = dict(row)
            item["e3e_draw_probability"] = q
            item["e3e_nondraw_home_probability"] = r
            item["e3e_probs"] = probabilities
            item["e3e_status"] = status
            output.append(item)
        folds.append({
            "target_year": target_year, "prior_rows": len(prior),
            "prior_draws": train_draws, "prior_home": train_nondraw_home,
            "prior_away": train_nondraw_away, "target_rows": len(current),
            "status": status, "feature_count": len(names), "class_weight": None,
            "posthoc_threshold": None,
        })
    return output, folds


def safe_roc(y: np.ndarray, p: np.ndarray) -> float | None:
    return float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else None


def ece_binary(y: np.ndarray, p: np.ndarray, bins: int = 10) -> tuple[float, list[dict[str, Any]]]:
    edges = np.linspace(0.0, 1.0, bins + 1)
    table = []
    ece = 0.0
    for index in range(bins):
        lo, hi = float(edges[index]), float(edges[index + 1])
        mask = (p >= lo) & (p < hi if index < bins - 1 else p <= hi)
        count = int(mask.sum())
        if not count:
            continue
        predicted = float(p[mask].mean())
        actual = float(y[mask].mean())
        ece += count / len(y) * abs(predicted - actual)
        table.append({
            "lower": lo, "upper": hi, "count": count,
            "mean_predicted": predicted, "actual_rate": actual,
            "absolute_gap": abs(predicted - actual),
        })
    return float(ece), table


def wilson(successes: int, total: int, z: float = 1.959963984540054) -> list[float] | None:
    if total <= 0:
        return None
    proportion = successes / total
    denominator = 1.0 + z * z / total
    center = (proportion + z * z / (2.0 * total)) / denominator
    radius = z * math.sqrt(proportion * (1.0 - proportion) / total + z * z / (4.0 * total * total)) / denominator
    return [max(0.0, center - radius), min(1.0, center + radius)]


def top_candidate_metrics(y: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    order = np.argsort(-p, kind="mergesort")
    draws = int(y.sum())
    output = {}
    for fraction in TOP_FRACTIONS:
        count = max(1, int(math.ceil(len(y) * fraction)))
        selected = y[order[:count]]
        hits = int(selected.sum())
        output[f"top_{int(fraction * 100)}pct"] = {
            "count": count, "hits": hits, "precision": hits / count,
            "precision_wilson_95": wilson(hits, count),
            "recall": hits / max(1, draws),
            "minimum_probability": float(p[order[count - 1]]),
        }
    return output


def precision_target_metrics(y: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    order = np.argsort(-p, kind="mergesort")
    cumulative = np.cumsum(y[order])
    draws = int(y.sum())
    output = {}
    for target in PRECISION_TARGETS:
        best: dict[str, Any] | None = None
        for count in range(1, len(y) + 1):
            hits = int(cumulative[count - 1])
            precision = hits / count
            if precision + 1e-15 < target:
                continue
            recall = hits / max(1, draws)
            candidate = {
                "count": count, "hits": hits, "precision": precision,
                "precision_wilson_95": wilson(hits, count), "recall": recall,
                "minimum_probability": float(p[order[count - 1]]),
            }
            if best is None or (candidate["recall"], candidate["count"]) > (best["recall"], best["count"]):
                best = candidate
        output[f"precision_at_least_{int(target * 100)}pct"] = best
    return output


def bootstrap_pr_auc(y: np.ndarray, p: np.ndarray, seed: int) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    estimates = []
    n = len(y)
    for _ in range(BOOTSTRAP_RESAMPLES):
        sample = rng.integers(0, n, size=n)
        sampled_y = y[sample]
        if len(np.unique(sampled_y)) < 2:
            continue
        estimates.append(float(average_precision_score(sampled_y, p[sample])))
    estimates.sort()
    if not estimates:
        return {"resamples": 0, "lower_95": None, "upper_95": None}
    lower = estimates[int(0.025 * (len(estimates) - 1))]
    upper = estimates[int(0.975 * (len(estimates) - 1))]
    return {"resamples": len(estimates), "lower_95": lower, "upper_95": upper}


def draw_diagnostics(rows: list[dict[str, Any]], q_field: str = "e3e_draw_probability", seed: int = SEED) -> dict[str, Any]:
    y = np.asarray([int(row["actual_outcome"] == "draw") for row in rows], dtype=int)
    p = np.asarray([finite(row[q_field]) for row in rows], dtype=float)
    prevalence = float(y.mean())
    ece, calibration = ece_binary(y, p)
    pr_auc = float(average_precision_score(y, p))
    top = top_candidate_metrics(y, p)
    bootstrap = bootstrap_pr_auc(y, p, seed)
    candidate_lower_above_base = any(
        item["precision_wilson_95"] is not None and item["precision_wilson_95"][0] > prevalence
        for item in top.values()
    )
    identifiable = (
        bootstrap["lower_95"] is not None and bootstrap["lower_95"] > prevalence
        and candidate_lower_above_base
    )
    return {
        "count": len(rows), "actual_draws": int(y.sum()), "prevalence": prevalence,
        "pr_auc": pr_auc, "pr_auc_lift_absolute": pr_auc - prevalence,
        "pr_auc_lift_relative": pr_auc / prevalence - 1.0 if prevalence > 0 else None,
        "pr_auc_bootstrap_95": bootstrap, "roc_auc": safe_roc(y, p),
        "brier": float(brier_score_loss(y, p)),
        "logloss": float(log_loss(y, np.column_stack((1.0 - p, p)), labels=[0, 1])),
        "ece_10bin": ece, "calibration_table": calibration,
        "top_candidates": top, "precision_targets": precision_target_metrics(y, p),
        "identifiability_gate": {
            "pr_auc_lower_95_above_prevalence": bool(bootstrap["lower_95"] is not None and bootstrap["lower_95"] > prevalence),
            "candidate_precision_lower_95_above_prevalence": candidate_lower_above_base,
            "identifiable_from_current_features": identifiable,
        },
    }


def prediction_counts(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    counts = Counter(max(OUTCOMES, key=lambda label: (finite(row[field][label]), -INDEX[label])) for row in rows)
    return {
        "counts": {label: int(counts[label]) for label in OUTCOMES},
        "proportions": {label: counts[label] / len(rows) for label in OUTCOMES},
    }


def actual_draw_misclassification(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts = Counter()
    for row in rows:
        if row["actual_outcome"] != "draw":
            continue
        call = max(OUTCOMES, key=lambda label: (finite(row[field][label]), -INDEX[label]))
        counts[call] += 1
    return {label: int(counts[label]) for label in OUTCOMES}


def hda_metrics(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    result = e3b1.metrics(rows, field)
    result["prediction_distribution"] = prediction_counts(rows, field)
    result["actual_draw_called_as"] = actual_draw_misclassification(rows, field)
    return result


def quantile_edges(values: list[float], bins: int = 4) -> list[float]:
    finite_values = np.asarray([value for value in values if math.isfinite(value)], dtype=float)
    if len(finite_values) == 0:
        return []
    return sorted(set(float(value) for value in np.quantile(finite_values, np.linspace(0, 1, bins + 1))))


def numeric_bin(value: float, edges: list[float]) -> str:
    if len(edges) < 2:
        return "all"
    for index in range(len(edges) - 1):
        upper_inclusive = index == len(edges) - 2
        if value >= edges[index] and (value < edges[index + 1] or (upper_inclusive and value <= edges[index + 1])):
            return f"Q{index + 1}[{edges[index]:.4g},{edges[index + 1]:.4g}{']' if upper_inclusive else ')'}"
    return "outside"


def season_stage_map(rows: list[dict[str, Any]]) -> dict[str, str]:
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["competition_id"], int(row["season_start_year"]))].append(row)
    output = {}
    labels = ("early_0_25", "mid1_25_50", "mid2_50_75", "late_75_100")
    for values in grouped.values():
        ordered = sorted(values, key=lambda row: (row["date"], row["match_key"]))
        denominator = max(1, len(ordered) - 1)
        for index, row in enumerate(ordered):
            output[row["match_key"]] = labels[min(3, int(index / denominator * 4.0))]
    return output


def handicap_value(record: dict[str, Any]) -> float:
    mapping = {name: finite(value) for name, value in zip(record["market_names"], record["market_x"])}
    return mapping.get("close_ah", 0.0)


def binned_diagnostics(rows: list[dict[str, Any]], q_field: str = "e3e_draw_probability") -> dict[str, Any]:
    stage = season_stage_map(rows)
    strength = [finite(row.get("strength_gap")) for row in rows]
    expected_total = [finite(dict(row.get("team_sample", {})).get("mu_total")) for row in rows]
    handicap = [handicap_value(row) for row in rows]
    strength_edges = quantile_edges(strength)
    total_edges = quantile_edges(expected_total)
    handicap_edges = quantile_edges(handicap)
    dimensions: dict[str, Callable[[dict[str, Any]], str]] = {
        "strength_gap": lambda row: numeric_bin(finite(row.get("strength_gap")), strength_edges),
        "expected_total": lambda row: numeric_bin(finite(dict(row.get("team_sample", {})).get("mu_total")), total_edges),
        "asian_handicap": lambda row: numeric_bin(handicap_value(row), handicap_edges),
        "league": lambda row: str(row["competition_id"]),
        "season_stage": lambda row: stage.get(row["match_key"], "unknown"),
    }
    result = {}
    for dimension, getter in dimensions.items():
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            groups[getter(row)].append(row)
        items = {}
        for label, subset in sorted(groups.items()):
            y = np.asarray([int(row["actual_outcome"] == "draw") for row in subset], dtype=int)
            p = np.asarray([finite(row[q_field]) for row in subset], dtype=float)
            items[label] = {
                "count": len(subset), "draws": int(y.sum()),
                "prevalence": float(y.mean()) if len(y) else None,
                "mean_predicted": float(p.mean()) if len(p) else None,
                "pr_auc": float(average_precision_score(y, p)) if y.sum() and y.sum() < len(y) else None,
                "roc_auc": safe_roc(y, p),
                "brier": float(brier_score_loss(y, p)) if len(y) else None,
            }
        result[dimension] = {
            "edges": {"strength_gap": strength_edges, "expected_total": total_edges, "asian_handicap": handicap_edges}.get(dimension),
            "bins": items,
        }
    return result


def baseline_sections(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "market": hda_metrics(rows, "market_probs"),
        "champion": hda_metrics(rows, "champion_probs"),
        "e3b1": hda_metrics(rows, "e3b1_probs"),
        "e3d1": hda_metrics(rows, "e3d1_probs"),
    }


def model_section(rows: list[dict[str, Any]], seed: int) -> dict[str, Any]:
    return {
        "draw_identifiability": draw_diagnostics(rows, seed=seed),
        "two_stage_hda": hda_metrics(rows, "e3e_probs"),
        "bins": binned_diagnostics(rows),
        "modeled_count": sum(row["e3e_status"] == "MODELED" for row in rows),
        "fallback_count": sum(row["e3e_status"] != "MODELED" for row in rows),
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# E3e-0 Pure 90-Minute H/D/A Draw Identifiability", "",
        "Research-only; formal_weight=0; no score/total/BTTS output gate; no post-hoc draw threshold.", "",
        f"- Repository HEAD: `{report['repository_head']}`",
        f"- Full fixed OOF: {report['sample']['count']}",
        f"- Actual draws: {report['sample']['actual_draws']} ({report['sample']['draw_rate']:.4%})",
        f"- Fixed B100: {report['b100']['count']}", "",
        "## Draw identifiability", "",
        "| Group | Model | PR-AUC | Lift vs base | ROC-AUC | Brier | ECE | Top10 P/R | Top20 P/R | Identifiable |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in report["models"].values():
        draw = item["full_oof"]["draw_identifiability"]
        top10 = draw["top_candidates"]["top_10pct"]
        top20 = draw["top_candidates"]["top_20pct"]
        lines.append(
            f"| {item['feature_group']} | {item['model_type']} | {draw['pr_auc']:.4%} | "
            f"{draw['pr_auc_lift_absolute']:+.4%} | {draw['roc_auc']:.4%} | {draw['brier']:.6f} | "
            f"{draw['ece_10bin']:.6f} | {top10['precision']:.2%}/{top10['recall']:.2%} | "
            f"{top20['precision']:.2%}/{top20['recall']:.2%} | "
            f"{draw['identifiability_gate']['identifiable_from_current_features']} |"
        )
    lines.extend(["", "## Two-stage H/D/A", "",
        "| Group | Model | Accuracy | Balanced | Macro-F1 | Draw P | Draw R | Draw F1 | LogLoss | Brier | RPS | Pred H/D/A |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ])
    for baseline_name, metrics in report["baselines"].items():
        counts = metrics["prediction_distribution"]["counts"]
        lines.append(
            f"| Baseline | {baseline_name} | {metrics['accuracy']:.4%} | {metrics['balanced_accuracy']:.4%} | "
            f"{metrics['macro_f1']:.4%} | {metrics['draw_precision']:.4%} | {metrics['draw_recall']:.4%} | "
            f"{metrics['draw_f1']:.4%} | {metrics['logloss']:.6f} | {metrics['brier']:.6f} | "
            f"{metrics['rps']:.6f} | {counts['home']}/{counts['draw']}/{counts['away']} |"
        )
    for item in report["models"].values():
        metrics = item["full_oof"]["two_stage_hda"]
        counts = metrics["prediction_distribution"]["counts"]
        lines.append(
            f"| {item['feature_group']} | {item['model_type']} | {metrics['accuracy']:.4%} | "
            f"{metrics['balanced_accuracy']:.4%} | {metrics['macro_f1']:.4%} | "
            f"{metrics['draw_precision']:.4%} | {metrics['draw_recall']:.4%} | {metrics['draw_f1']:.4%} | "
            f"{metrics['logloss']:.6f} | {metrics['brier']:.6f} | {metrics['rps']:.6f} | "
            f"{counts['home']}/{counts['draw']}/{counts['away']} |"
        )
    lines.extend(["", "## Diagnostic verdict", "",
        f"- Best draw PR-AUC model: `{report['verdict']['best_model']}` at {report['verdict']['best_pr_auc']:.4%}.",
        f"- Base draw rate: {report['sample']['draw_rate']:.4%}.",
        f"- Current features identifiable: {report['verdict']['current_features_identifiable']}.",
        f"- Stop condition: {report['verdict']['stop_condition']}.",
        "- No model is promoted; no threshold is activated; formal_weight remains 0.", "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=str(OUT))
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        base_rows, lineage = e3d1.build_records()
        evaluated, e3d1_folds = e3d1.expanding_oos(base_rows)
        if len(evaluated) != 6251:
            raise RuntimeError(f"fixed sample contract failed: {len(evaluated)} != 6251")
        actual_draws = sum(row["actual_outcome"] == "draw" for row in evaluated)
        model_outputs = {}
        enriched_by_key: dict[str, dict[str, Any]] = {row["match_key"]: dict(row) for row in evaluated}
        for group_index, group in enumerate(("A_MARKET", "B_TEAM", "C_COMBINED")):
            for kind_index, kind in enumerate(("LOGISTIC", "TREE")):
                key = f"{group}__{kind}"
                predicted, folds = rolling_oof(evaluated, group, kind)
                for row in predicted:
                    enriched_by_key[row["match_key"]][f"{key}_q"] = row["e3e_draw_probability"]
                    enriched_by_key[row["match_key"]][f"{key}_probs"] = row["e3e_probs"]
                    enriched_by_key[row["match_key"]][f"{key}_status"] = row["e3e_status"]
                model_outputs[key] = {
                    "feature_group": group, "model_type": kind, "class_weight": None,
                    "posthoc_threshold": None, "folds": folds,
                    "full_oof": model_section(predicted, SEED + group_index * 10 + kind_index),
                }
        enriched = list(enriched_by_key.values())
        by_competition: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in enriched:
            by_competition[row["competition_id"]].append(row)
        b100_rows, selection = e3a.fixed_b100(by_competition)
        expected_b100 = b100.TARGET_PER_LEAGUE * len(b100.BIG5)
        if len(b100_rows) != expected_b100:
            raise RuntimeError(f"B100 contract failed: {len(b100_rows)}")
        for key, item in model_outputs.items():
            fixed = []
            for source in b100_rows:
                row = dict(source)
                row["e3e_draw_probability"] = finite(source[f"{key}_q"])
                row["e3e_probs"] = dict(source[f"{key}_probs"])
                row["e3e_status"] = source[f"{key}_status"]
                fixed.append(row)
            item["b100"] = model_section(fixed, SEED + 1000 + len(fixed))
        best_key = max(model_outputs, key=lambda key: model_outputs[key]["full_oof"]["draw_identifiability"]["pr_auc"])
        best_draw = model_outputs[best_key]["full_oof"]["draw_identifiability"]
        identifiable = bool(best_draw["identifiability_gate"]["identifiable_from_current_features"])
        report = {
            "schema_version": "1.0", "research_status": "PASS",
            "repository_head": repository_head(),
            "experiment": "E3E0_PURE_HDA_DRAW_IDENTIFIABILITY",
            "scope": "90_minutes_including_stoppage",
            "sample": {"count": len(evaluated), "actual_draws": actual_draws,
                "draw_rate": actual_draws / len(evaluated), "fixed_sample_expected": 6251},
            "feature_groups": {
                "A_MARKET": "opening/closing 1X2, movements, OU2.5, AH and bookmaker disagreement only",
                "B_TEAM": "Champion/team-state features with no market input",
                "C_COMBINED": "A plus B",
            },
            "models": model_outputs, "baselines": baseline_sections(evaluated),
            "b100": {"count": len(b100_rows), "selection": selection,
                "baselines": baseline_sections(b100_rows)},
            "lineage": {**lineage, "e3d1_folds": e3d1_folds},
            "verdict": {
                "best_model": best_key, "best_pr_auc": best_draw["pr_auc"],
                "best_pr_auc_bootstrap_95": best_draw["pr_auc_bootstrap_95"],
                "current_features_identifiable": identifiable,
                "stop_condition": (
                    "CURRENT_FEATURES_SHOW_RANKING_SIGNAL; DO_NOT PROMOTE; NEXT STEP MAY STUDY ROBUST OOS MODELING"
                    if identifiable else
                    "PR_AUC/CANDIDATE_PRECISION_NOT_SIGNIFICANTLY_ABOVE_BASE; STOP THRESHOLD TUNING AND MOVE TO NEW PIT FEATURES"
                ),
            },
            "audit": {
                "target_season_used_for_training": False, "rolling_oof": True,
                "class_weights_used": False, "manual_draw_threshold_used": False,
                "score_total_btts_output_or_gate_used": False,
                "b100_count_contract": "PASS",
                "probability_sum_max_residual": max(
                    abs(sum(row[f"{key}_probs"].values()) - 1.0)
                    for row in enriched for key in model_outputs
                ),
            },
            "promotion": {"automatic_promotion": False, "formal_weight": 0,
                "status": "DIAGNOSTIC_ONLY"},
            "formal_mutation": {"model": 0, "data": 0, "config": 0,
                "current": 0, "formal_weight": 0},
            "failures": [],
        }
    except Exception as exc:
        report = {
            "schema_version": "1.0", "research_status": "FAIL",
            "repository_head": repository_head(),
            "experiment": "E3E0_PURE_HDA_DRAW_IDENTIFIABILITY",
            "failures": [{"error": f"{type(exc).__name__}: {exc}"}],
            "promotion": {"automatic_promotion": False, "formal_weight": 0,
                "status": "DIAGNOSTIC_ONLY"},
            "formal_mutation": {"model": 0, "data": 0, "config": 0,
                "current": 0, "formal_weight": 0},
        }
    json_path = output_dir / "e3e0_draw_identifiability.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if report["research_status"] == "PASS":
        (output_dir / "e3e0_draw_identifiability.md").write_text(markdown(report), encoding="utf-8")
    if args.print_summary:
        summary = {
            "research_status": report["research_status"],
            "repository_head": report.get("repository_head"),
            "sample": report.get("sample"), "verdict": report.get("verdict"),
            "models": {
                key: {"draw": item["full_oof"]["draw_identifiability"],
                    "hda": item["full_oof"]["two_stage_hda"],
                    "modeled_count": item["full_oof"]["modeled_count"],
                    "fallback_count": item["full_oof"]["fallback_count"]}
                for key, item in report.get("models", {}).items()
            },
            "failures": report.get("failures"),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if report["research_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
