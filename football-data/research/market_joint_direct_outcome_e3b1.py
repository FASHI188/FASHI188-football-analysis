#!/usr/bin/env python3
"""E3b-1 research: market-joint direct H/D/A probability head.

p_e3b1 = softmax(log(p_closing_1x2) + beta(X)).
No binary draw gate, manual draw lift, threshold override, score-matrix edit,
formal mutation, or target-season fitting.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np
from scipy.optimize import minimize

HERE = Path(__file__).resolve().parent
FD = HERE.parent
for path in (FD / "engine", FD / "validation", HERE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import big5_high_completeness_b100 as b100  # noqa: E402
import matrix_draw_gate_e3a as e3a  # noqa: E402
from platform_core import ROOT  # noqa: E402

OUT = ROOT.parent / "artifacts/research/market_joint_direct_outcome_e3b1"
OUTCOMES = ("home", "draw", "away")
INDEX = {name: i for i, name in enumerate(OUTCOMES)}
EPS = 1e-12
SPECS = ("market_joint", "market_joint_champion_team")
L2_GRID = (1.0, 3.0, 10.0)
DEFAULT_SPEC, DEFAULT_L2 = SPECS[-1], 3.0
MIN_TRAIN, MIN_VALID, MIN_CLASS = 650, 250, 60
TEAM_KEYS = (
    "mu_total", "ess", "allocation_home_share", "home_score_signal",
    "away_score_signal", "home_direct_total_rate", "away_direct_total_rate",
)
EXCLUDE_BOOKS = {"Avg", "Max", "BbAv", "BbMx"}


def head() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT.parent, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def number(value: Any) -> float | None:
    try:
        x = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def odds(value: Any) -> float | None:
    x = number(value)
    return x if x is not None and x > 1.0 else None


def devig(values: list[float]) -> list[float]:
    inv = [1.0 / x for x in values]
    total = sum(inv)
    return [x / total for x in inv]


def group(row: dict[str, str], groups: tuple[tuple[str, ...], ...]) -> tuple[list[float], tuple[str, ...]] | None:
    for keys in groups:
        values = [odds(row.get(key)) for key in keys]
        if all(x is not None for x in values):
            return devig([float(x) for x in values]), keys
    return None


def entropy(p: list[float]) -> float:
    return -sum(x * math.log(max(EPS, x)) for x in p)


def margin(p: list[float]) -> float:
    ordered = sorted(p, reverse=True)
    return ordered[0] - ordered[1]


def book_features(row: dict[str, str], close: list[float]) -> tuple[list[float], int]:
    rows = []
    for key in row:
        if not key.endswith("CH") or len(key) <= 2:
            continue
        prefix = key[:-2]
        if prefix in EXCLUDE_BOOKS:
            continue
        h, d, a = odds(row.get(key)), odds(row.get(prefix + "CD")), odds(row.get(prefix + "CA"))
        if h is not None and d is not None and a is not None:
            rows.append(devig([h, d, a]))
    if len(rows) < 2:
        return [0.0] * 13, len(rows)
    columns = list(zip(*rows))
    avg = [mean(col) for col in columns]
    std = [math.sqrt(mean((x - avg[i]) ** 2 for x in col)) for i, col in enumerate(columns)]
    span = [max(col) - min(col) for col in columns]
    tops = Counter(max(range(3), key=lambda i: (p[i], -i)) for p in rows)
    votes = [tops[i] / len(rows) for i in range(3)]
    return [
        *[avg[i] - close[i] for i in range(3)], *std, *span, *votes,
        math.log1p(len(rows)),
    ], len(rows)


def extract_market(row: dict[str, str]) -> dict[str, Any] | None:
    opening, closing = group(row, b100.OPEN_1X2), group(row, b100.CLOSE_1X2)
    if opening is None or closing is None:
        return None
    op, op_keys = opening
    cp, cp_keys = closing
    features = [
        *op, *cp, *[cp[i] - op[i] for i in range(3)],
        *[math.log(max(EPS, cp[i])) - math.log(max(EPS, op[i])) for i in range(3)],
        entropy(cp), margin(cp), entropy(cp) - entropy(op), margin(cp) - margin(op),
    ]
    names = [
        "open_h", "open_d", "open_a", "close_h", "close_d", "close_a",
        "move_h", "move_d", "move_a", "logmove_h", "logmove_d", "logmove_a",
        "close_entropy", "close_margin", "entropy_move", "margin_move",
    ]

    open_ou, close_ou = group(row, b100.OPEN_OU), group(row, b100.CLOSE_OU)
    if open_ou and close_ou:
        ou = [close_ou[0][1], close_ou[0][1] - open_ou[0][1], 1.0]
    else:
        ou = [0.0, 0.0, 0.0]
    features.extend(ou)
    names.extend(("close_under25", "under25_move", "ou_available"))

    open_ah, close_ah = group(row, b100.OPEN_AH), group(row, b100.CLOSE_AH)
    ol, cl = number(row.get("AHh")), number(row.get("AHCh"))
    if open_ah and close_ah and ol is not None and cl is not None:
        ah = [cl, cl - ol, close_ah[0][0], close_ah[0][0] - open_ah[0][0], 1.0]
    else:
        ah = [0.0] * 5
    features.extend(ah)
    names.extend(("close_ah", "ah_line_move", "close_ah_home", "ah_home_move", "ah_available"))

    books, book_count = book_features(row, cp)
    features.extend(books)
    names.extend((
        "book_mean_h", "book_mean_d", "book_mean_a", "book_std_h", "book_std_d",
        "book_std_a", "book_span_h", "book_span_d", "book_span_a", "book_vote_h",
        "book_vote_d", "book_vote_a", "book_count_log",
    ))
    return {
        "market_probs": {name: cp[i] for i, name in enumerate(OUTCOMES)},
        "market_x": features,
        "market_names": names,
        "audit": {
            "open_group": list(op_keys), "close_group": list(cp_keys),
            "ou_available": bool(ou[-1]), "ah_available": bool(ah[-1]),
            "book_count": book_count,
        },
    }


def market_map(competition_id: str) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    metadata = b100.raw_rows(competition_id)
    by_source: dict[str, dict[int, str]] = defaultdict(dict)
    for key, meta in metadata.items():
        by_source[str(meta["source_path"])][int(meta["source_line"])] = key
    output = {}
    audit = Counter()
    for relative, wanted in by_source.items():
        with (ROOT / relative).open("r", encoding="utf-8-sig", newline="") as handle:
            for line_no, raw in enumerate(csv.DictReader(handle), start=2):
                key = wanted.get(line_no)
                if key is None:
                    continue
                audit["identified"] += 1
                observation = extract_market({
                    str(k).strip(): "" if v is None else str(v).strip()
                    for k, v in raw.items() if k
                })
                if observation is None:
                    audit["missing_open_or_close_1x2"] += 1
                    continue
                output[key] = observation
                audit["joined"] += 1
                audit["ou_available"] += int(observation["audit"]["ou_available"])
                audit["ah_available"] += int(observation["audit"]["ah_available"])
                audit["books_2plus"] += int(observation["audit"]["book_count"] >= 2)
    audit["unique"] = len(output)
    return output, dict(audit)


def join(competition_id: str, seasons: dict[str, list[dict[str, Any]]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    markets, audit = market_map(competition_id)
    output, total = [], 0
    for records in seasons.values():
        for record in records:
            total += 1
            market = markets.get(record["match_key"])
            if market is None:
                continue
            item = dict(record)
            item.update(market)
            item["season_start_year"] = e3a.season_year(item["season"])
            output.append(item)
    return output, {
        "champion_oos": total, "market_joined": len(output),
        "coverage": len(output) / total if total else 0.0, "market": audit,
    }


def vector(record: dict[str, Any], spec: str) -> tuple[list[float], list[str]]:
    values, names = list(record["market_x"]), list(record["market_names"])
    competitions = list(b100.BIG5)
    values.extend(1.0 if record["competition_id"] == cid else 0.0 for cid in competitions)
    names.extend(f"league_{cid}" for cid in competitions)
    if spec == "market_joint_champion_team":
        champion = [float(record["champion_probs"][name]) for name in OUTCOMES]
        market = [float(record["market_probs"][name]) for name in OUTCOMES]
        values.extend(champion)
        values.extend(champion[i] - market[i] for i in range(3))
        values.extend((float(record["strength_gap"]), float(record["allocation_gap"])))
        names.extend((
            "champion_h", "champion_d", "champion_a", "champion_minus_market_h",
            "champion_minus_market_d", "champion_minus_market_a", "strength_gap",
            "allocation_gap",
        ))
        exact = record.get("p_total_exact", {})
        values.extend(float(exact.get(str(total), 0.0)) for total in range(7))
        values.append(sum(float(v) for k, v in exact.items() if int(k) >= 7))
        names.extend([f"total_{i}" for i in range(7)] + ["total_7plus"])
        sample = dict(record.get("team_sample", {}))
        for key in TEAM_KEYS:
            x = number(sample.get(key))
            values.extend((x if x is not None else 0.0, 1.0 if x is not None else 0.0))
            names.extend((f"team_{key}", f"team_{key}_available"))
    if len(values) != len(names) or not all(math.isfinite(float(x)) for x in values):
        raise RuntimeError("feature schema/finite audit failed")
    return [float(x) for x in values], names


def class_support(rows: list[dict[str, Any]]) -> bool:
    counts = Counter(row["actual_outcome"] for row in rows)
    return all(counts[name] >= MIN_CLASS for name in OUTCOMES)


def softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - logits.max(axis=1, keepdims=True)
    exp = np.exp(np.clip(shifted, -60.0, 60.0))
    return exp / exp.sum(axis=1, keepdims=True)


def fit(rows: list[dict[str, Any]], spec: str, l2: float) -> dict[str, Any]:
    counts = Counter(row["actual_outcome"] for row in rows)
    if len(rows) < MIN_TRAIN or not class_support(rows):
        return {"status": "FALLBACK_MARKET", "rows": len(rows), "counts": dict(counts), "spec": spec, "l2": l2}
    raw, names = [], None
    for row in rows:
        values, current_names = vector(row, spec)
        if names is None:
            names = current_names
        elif names != current_names:
            raise RuntimeError("feature schema drift")
        raw.append(values)
    x0 = np.asarray(raw, dtype=float)
    means, scales = x0.mean(axis=0), x0.std(axis=0)
    scales = np.where(scales > 1e-10, scales, 1.0)
    x = np.column_stack((np.ones(len(rows)), (x0 - means) / scales))
    market = np.asarray([[row["market_probs"][name] for name in OUTCOMES] for row in rows], dtype=float)
    market = np.clip(market, EPS, 1.0)
    market /= market.sum(axis=1, keepdims=True)
    offset = np.log(market)
    y = np.asarray([INDEX[row["actual_outcome"]] for row in rows], dtype=int)
    onehot = np.zeros((len(rows), 3))
    onehot[np.arange(len(rows)), y] = 1.0
    dims = x.shape[1]

    def objective(flat: np.ndarray) -> tuple[float, np.ndarray]:
        weights = flat.reshape(2, dims)
        residual = np.zeros((len(rows), 3))
        residual[:, :2] = x @ weights.T
        probabilities = softmax(offset + residual)
        nll = -np.log(np.clip(probabilities[np.arange(len(rows)), y], EPS, 1.0)).mean()
        loss = nll + 0.5 * l2 * np.square(weights[:, 1:]).sum() / len(rows)
        gradient = ((probabilities - onehot)[:, :2].T @ x) / len(rows)
        gradient[:, 1:] += l2 * weights[:, 1:] / len(rows)
        return float(loss), gradient.ravel()

    result = minimize(
        objective, np.zeros(2 * dims), method="L-BFGS-B", jac=True,
        options={"maxiter": 400, "ftol": 1e-11, "gtol": 1e-7, "maxls": 30},
    )
    loss, gradient = objective(np.asarray(result.x))
    return {
        "status": "TRAINED", "rows": len(rows), "counts": dict(counts), "spec": spec,
        "l2": l2, "names": names, "means": means.tolist(), "scales": scales.tolist(),
        "weights": np.asarray(result.x).reshape(2, dims).tolist(),
        "optimizer": {
            "success": bool(result.success), "status": int(result.status),
            "message": str(result.message), "iterations": int(result.nit),
            "evaluations": int(result.nfev), "loss": loss,
            "gradient_norm": float(np.linalg.norm(gradient)),
        },
    }


def predict(model: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, float]]:
    if model["status"] != "TRAINED":
        return [dict(row["market_probs"]) for row in rows]
    raw = []
    for row in rows:
        values, names = vector(row, model["spec"])
        if names != model["names"]:
            raise RuntimeError("prediction schema mismatch")
        raw.append(values)
    x0 = np.asarray(raw, dtype=float)
    x = np.column_stack((
        np.ones(len(rows)),
        (x0 - np.asarray(model["means"])) / np.asarray(model["scales"]),
    ))
    market = np.asarray([[row["market_probs"][name] for name in OUTCOMES] for row in rows])
    market = np.clip(market, EPS, 1.0)
    market /= market.sum(axis=1, keepdims=True)
    residual = np.zeros((len(rows), 3))
    residual[:, :2] = x @ np.asarray(model["weights"]).T
    probabilities = softmax(np.log(market) + residual)
    return [
        {name: float(probabilities[i, j]) for j, name in enumerate(OUTCOMES)}
        for i in range(len(rows))
    ]


def simple_scores(rows: list[dict[str, Any]], field: str) -> tuple[float, float, float]:
    logloss, brier, rps = [], [], []
    for row in rows:
        p = [float(row[field][name]) for name in OUTCOMES]
        y = INDEX[row["actual_outcome"]]
        logloss.append(-math.log(max(EPS, p[y])))
        brier.append(sum((p[i] - (1.0 if i == y else 0.0)) ** 2 for i in range(3)))
        rps.append(((p[0] - (1.0 if y == 0 else 0.0)) ** 2 +
                    (p[0] + p[1] - (1.0 if y <= 1 else 0.0)) ** 2) / 2.0)
    return mean(logloss), mean(brier), mean(rps)


def choose(prior: list[dict[str, Any]]) -> dict[str, Any]:
    years = sorted({row["season_start_year"] for row in prior})
    if len(years) < 2:
        return {"status": "DEFAULT_NO_INNER_YEAR", "spec": DEFAULT_SPEC, "l2": DEFAULT_L2, "leaderboard": []}
    validation_year = years[-1]
    train_rows = [row for row in prior if row["season_start_year"] < validation_year]
    validation = [row for row in prior if row["season_start_year"] == validation_year]
    if len(train_rows) < MIN_TRAIN or len(validation) < MIN_VALID or not class_support(train_rows):
        return {
            "status": "DEFAULT_INNER_COVERAGE", "spec": DEFAULT_SPEC, "l2": DEFAULT_L2,
            "train_rows": len(train_rows), "validation_rows": len(validation),
            "validation_year": validation_year, "leaderboard": [],
        }
    leaderboard = []
    for spec in SPECS:
        for l2 in L2_GRID:
            model = fit(train_rows, spec, l2)
            if model["status"] != "TRAINED":
                continue
            probs = predict(model, validation)
            scored = []
            for row, probability in zip(validation, probs):
                item = dict(row); item["selection_probs"] = probability; scored.append(item)
            ll, br, rp = simple_scores(scored, "selection_probs")
            leaderboard.append({
                "spec": spec, "l2": l2, "logloss": ll, "brier": br, "rps": rp,
                "optimizer": model["optimizer"],
            })
    if not leaderboard:
        return {"status": "DEFAULT_INNER_FIT_FAILURE", "spec": DEFAULT_SPEC, "l2": DEFAULT_L2, "leaderboard": []}
    leaderboard.sort(key=lambda x: (x["logloss"], x["brier"], x["rps"], SPECS.index(x["spec"]), -x["l2"]))
    return {
        "status": "SELECTED_PRIOR_ONLY", "spec": leaderboard[0]["spec"],
        "l2": leaderboard[0]["l2"], "validation_year": validation_year,
        "train_rows": len(train_rows), "validation_rows": len(validation),
        "leaderboard": leaderboard,
    }


def expanding_oos(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_year: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_year[row["season_start_year"]].append(row)
    years, output, folds = sorted(by_year), [], []
    for target in years:
        prior = [row for year in years if year < target for row in by_year[year]]
        current = sorted(by_year[target], key=lambda x: (x["date"], x["competition_id"], x["match_key"]))
        selection = choose(prior)
        model = fit(prior, selection["spec"], selection["l2"])
        probabilities = predict(model, current)
        for row, probability in zip(current, probabilities):
            item = dict(row)
            item["e3b1_probs"] = probability
            item["e3b1_modeled"] = model["status"] == "TRAINED"
            output.append(item)
        folds.append({
            "target_year": target, "prior_rows": len(prior), "target_rows": len(current),
            "selection": selection, "model": model,
        })
    return output, folds


def wilson(successes: int, total: int, z: float = 1.959963984540054) -> list[float] | None:
    if not total:
        return None
    p = successes / total
    den = 1 + z * z / total
    center = (p + z * z / (2 * total)) / den
    radius = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / den
    return [max(0.0, center - radius), min(1.0, center + radius)]


def ece(rows: list[dict[str, Any]], field: str) -> float:
    buckets = [[] for _ in range(10)]
    for row in rows:
        p = {name: float(row[field][name]) for name in OUTCOMES}
        call = max(OUTCOMES, key=lambda name: (p[name], -INDEX[name]))
        confidence = p[call]
        buckets[min(9, int(confidence * 10))].append((confidence, float(call == row["actual_outcome"])))
    return sum(
        len(bucket) / len(rows) * abs(mean(x[0] for x in bucket) - mean(x[1] for x in bucket))
        for bucket in buckets if bucket
    )


def metrics(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    result = e3a.classification_metrics(rows, field)
    if result.get("count"):
        hits = round(result["accuracy"] * result["count"])
        result["accuracy_wilson_95"] = wilson(hits, result["count"])
        result["confidence_ece_10bin"] = ece(rows, field)
    return result


def delta(candidate: dict[str, Any], baseline: dict[str, Any]) -> dict[str, float]:
    keys = (
        "accuracy", "balanced_accuracy", "macro_f1", "logloss", "brier", "rps",
        "draw_precision", "draw_recall", "draw_f1", "confidence_ece_10bin",
    )
    return {key: float(candidate[key]) - float(baseline[key]) for key in keys}


def per_league(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        cid: {
            "competition_zh": b100.BIG5[cid],
            "count": len(subset := [row for row in rows if row["competition_id"] == cid]),
            "market": metrics(subset, "market_probs"),
            "champion": metrics(subset, "champion_probs"),
            "e3b1": metrics(subset, "e3b1_probs"),
        }
        for cid in b100.BIG5
    }


def probability_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [
        [float(row["e3b1_probs"][name]) for name in OUTCOMES]
        for row in rows
    ]
    return {
        "max_sum_residual": max((abs(sum(p) - 1.0) for p in values), default=0.0),
        "min_probability": min((min(p) for p in values), default=None),
        "max_probability": max((max(p) for p in values), default=None),
        "all_finite": all(math.isfinite(x) for p in values for x in p),
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# E3b-1 Market-Joint Direct Outcome Head", "",
        "Research-only; formal_weight=0; no score-matrix projection.", "",
        f"- HEAD: `{report['repository_head']}`",
        f"- Full market-complete OOS: {report['full_oos']['count']}",
        f"- Modeled: {report['full_oos']['modeled_count']}",
        f"- Fixed B100: {report['b100']['count']}", "",
    ]
    for section in ("full_oos", "b100"):
        lines.extend((f"## {section}", ""))
        for label in ("market", "champion", "e3b1"):
            m = report[section][label]
            lines.extend((
                f"### {label}",
                f"- Accuracy / Balanced / Macro-F1: {m['accuracy']:.4%} / {m['balanced_accuracy']:.4%} / {m['macro_f1']:.4%}",
                f"- Draw P/R/F1: {m['draw_precision']:.4%} / {m['draw_recall']:.4%} / {m['draw_f1']:.4%}",
                f"- LogLoss / Brier / RPS / ECE: {m['logloss']:.6f} / {m['brier']:.6f} / {m['rps']:.6f} / {m['confidence_ece_10bin']:.6f}",
                "",
            ))
    lines.extend((
        "Combined Big Five results are architecture screening only. Formal use requires",
        "per-domain OOS/forward validation and E3b-2 constrained projection into the",
        "single audited score matrix.", "",
    ))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=str(OUT))
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    all_rows, joins, base_folds, failures = [], {}, {}, []
    for cid in b100.BIG5:
        try:
            season_rows, folds = e3a.nested_competition(cid)
            rows, audit = join(cid, season_rows)
            all_rows.extend(rows)
            joins[cid], base_folds[cid] = audit, folds
        except Exception as exc:
            failures.append({"competition_id": cid, "error": f"{type(exc).__name__}: {exc}"})

    if failures or not all_rows:
        report = {
            "schema_version": "1.0", "research_status": "FAIL",
            "repository_head": head(), "failures": failures or [{"error": "no rows"}],
            "formal_mutation": {"model": 0, "data": 0, "config": 0, "current": 0},
        }
        (out / "market_joint_direct_outcome_e3b1.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return 1

    evaluated, folds = expanding_oos(all_rows)
    by_comp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in evaluated:
        by_comp[row["competition_id"]].append(row)
    b100_rows, selection = e3a.fixed_b100(by_comp)
    modeled = [row for row in evaluated if row["e3b1_modeled"]]

    full = {key: metrics(evaluated, field) for key, field in (
        ("market", "market_probs"), ("champion", "champion_probs"), ("e3b1", "e3b1_probs"),
    )}
    fixed = {key: metrics(b100_rows, field) for key, field in (
        ("market", "market_probs"), ("champion", "champion_probs"), ("e3b1", "e3b1_probs"),
    )}
    audit = probability_audit(evaluated)
    optimizer_success = [
        fold["model"]["optimizer"]["success"]
        for fold in folds if fold["model"]["status"] == "TRAINED"
    ]
    expected_b100 = b100.TARGET_PER_LEAGUE * len(b100.BIG5)
    passed = (
        not failures and len(b100_rows) == expected_b100 and audit["all_finite"]
        and audit["max_sum_residual"] <= 1e-10 and audit["min_probability"] >= 0.0
        and all(optimizer_success)
    )

    report = {
        "schema_version": "1.0",
        "research_status": "PASS" if passed else "FAIL",
        "repository_head": head(),
        "scope": "90_minutes_including_stoppage",
        "experiment": "E3B1_MARKET_JOINT_DIRECT_OUTCOME_HEAD",
        "architecture": {
            "formula": "softmax(log(P_closing_1X2_market)+residual_logits(X))",
            "three_class_direct_probability": True,
            "binary_draw_gate": False, "manual_draw_lift": False,
            "top1_threshold_override": False, "score_matrix_modified": False,
            "feature_specs": list(SPECS), "l2_grid": list(L2_GRID),
            "target_season_used_for_training_or_selection": False,
            "market_families": [
                "opening/closing 1X2", "OU2.5", "Asian handicap",
                "cross-bookmaker closing disagreement",
            ],
        },
        "coverage": {
            "market_joined": len(all_rows), "evaluated": len(evaluated),
            "modeled": len(modeled), "modeled_rate": len(modeled) / len(evaluated),
            "by_competition": joins,
        },
        "full_oos": {
            "count": len(evaluated), "modeled_count": len(modeled), **full,
            "delta_e3b1_minus_market": delta(full["e3b1"], full["market"]),
            "delta_e3b1_minus_champion": delta(full["e3b1"], full["champion"]),
            "per_league": per_league(evaluated),
        },
        "b100": {
            "count": len(b100_rows),
            "modeled_count": sum(row["e3b1_modeled"] for row in b100_rows),
            "selection": selection, **fixed,
            "delta_e3b1_minus_market": delta(fixed["e3b1"], fixed["market"]),
            "delta_e3b1_minus_champion": delta(fixed["e3b1"], fixed["champion"]),
            "per_league": per_league(b100_rows),
            "records": [{
                "match_key": row["match_key"], "competition_id": row["competition_id"],
                "actual_score": row["actual_score"], "actual_outcome": row["actual_outcome"],
                "market_probs": row["market_probs"], "champion_probs": row["champion_probs"],
                "e3b1_probs": row["e3b1_probs"], "modeled": row["e3b1_modeled"],
            } for row in b100_rows],
        },
        "head_folds": folds,
        "base_parameter_folds": base_folds,
        "audit": {
            "probability": audit, "optimizer_success": optimizer_success,
            "b100_count_contract": "PASS" if len(b100_rows) == expected_b100 else "FAIL",
            "temporal_contract": "target season uses earlier seasons only",
            "market_snapshot": "retrospective closing market; research only",
        },
        "promotion": {
            "automatic_promotion": False, "formal_weight": 0,
            "status": "CHALLENGE_LAYER_ONLY",
            "next_gate": "E3b-2 minimum-KL/IPF unified-matrix coordination",
            "per_domain_oos": "NOT_EVALUATED",
            "forward_validation": "NOT_EVALUATED",
        },
        "formal_mutation": {
            "model": 0, "data": 0, "config": 0, "current": 0, "formal_weight": 0,
        },
        "failures": failures,
    }
    (out / "market_joint_direct_outcome_e3b1.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "market_joint_direct_outcome_e3b1.md").write_text(markdown(report), encoding="utf-8")
    if args.print_summary:
        print(json.dumps({
            "status": report["research_status"], "coverage": report["coverage"],
            "full_oos": report["full_oos"], "b100": {
                key: report["b100"][key] for key in (
                    "count", "modeled_count", "market", "champion", "e3b1",
                    "delta_e3b1_minus_market",
                )
            },
            "audit": report["audit"], "promotion": report["promotion"],
        }, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
