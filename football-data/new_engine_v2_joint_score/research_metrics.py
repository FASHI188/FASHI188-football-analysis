from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from engine import (
    CLASSES, FAMILIES, EngineState, Fixture, Parameters, apply_fitness, exact_score_probability,
    head_predict, joint_matrix, kl_project_to_1x2, matrix_1x2,
)
from strict import GovernanceError, canonical_json_bytes, sha256_file, strict_nonnegative_int

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE / "evidence"
ROOT = HERE.parent

PARAM_GRID = [
    Parameters(half_life_days=180.0, competition_half_life_days=600.0, team_prior_matches=6.0,
               competition_prior_matches=24.0, cross_season_shrink=0.50, strength_exponent=0.80),
    Parameters(half_life_days=240.0, competition_half_life_days=720.0, team_prior_matches=8.0,
               competition_prior_matches=30.0, cross_season_shrink=0.60, strength_exponent=0.85),
    Parameters(half_life_days=320.0, competition_half_life_days=900.0, team_prior_matches=10.0,
               competition_prior_matches=36.0, cross_season_shrink=0.70, strength_exponent=0.90),
]

DEP_GRIDS = {
    "INDEPENDENT_POISSON_FROZEN": [0.0],
    "DIXON_COLES_LOW_SCORE": [-0.12, -0.08, -0.04, 0.0, 0.04, 0.08],
    "DIAGONAL_INFLATION_BIVARIATE": [-0.60, -0.30, 0.0, 0.30, 0.60],
    "DYNAMIC_NB_DIAGONAL": [-0.60, -0.30, 0.0, 0.30, 0.60],
    "DYNAMIC_NB_MARCO": [-0.30, -0.15, 0.0, 0.15, 0.30],
    "DYNAMIC_NB_SARMANOV": [-1.50, -0.75, 0.0, 0.75, 1.50],
}
FITNESS_GRID = [(-0.05, -0.05), (-0.05, 0.0), (0.0, -0.05), (0.0, 0.0), (0.0, 0.05), (0.05, 0.0), (0.05, 0.05)]
MAX_FIT_ROWS = 900
MAX_HEAD_ROWS = 5000
EPS = 1e-15


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    out = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise GovernanceError(f"invalid jsonl {path}:{line_no}") from exc
            out.append(row)
    return out


def actual_class(row: dict[str, Any]) -> str:
    hg = strict_nonnegative_int(row["home_goals"], "home_goals")
    ag = strict_nonnegative_int(row["away_goals"], "away_goals")
    return "home" if hg > ag else "draw" if hg == ag else "away"


def batch_ranges(rows: list[dict[str, Any]]) -> list[tuple[int, int]]:
    if not rows:
        return []
    out = []
    start = 0
    current = rows[0]["cutoff"]
    for i, row in enumerate(rows[1:], start=1):
        if row["cutoff"] != current:
            out.append((start, i))
            start = i
            current = row["cutoff"]
    out.append((start, len(rows)))
    return out


def make_fixture(row: dict[str, Any]) -> Fixture:
    from datetime import datetime
    cutoff = datetime.fromisoformat(str(row["cutoff"]).replace("Z", "+00:00"))
    return Fixture(
        fixture_id=str(row["fixture_id"]),
        competition_id=str(row["competition_id"]),
        season=str(row["season"]),
        kickoff=cutoff,
        home_team_id=str(row["home_team_id"]),
        away_team_id=str(row["away_team_id"]),
        round_index=int(row["round_index"]),
    )


def prequential_features(rows: list[dict[str, Any]], params: Parameters) -> list[dict[str, Any]]:
    engine = EngineState(params)
    features: list[dict[str, Any]] = [None] * len(rows)  # type: ignore
    for start, end in batch_ranges(rows):
        fixtures = [make_fixture(row) for row in rows[start:end]]
        labels: dict[str, tuple[int, int]] = {}
        for offset, (row, fixture) in enumerate(zip(rows[start:end], fixtures), start=start):
            features[offset] = engine.predict_features(fixture)
            labels[fixture.fixture_id] = (
                strict_nonnegative_int(row["home_goals"], "home_goals"),
                strict_nonnegative_int(row["away_goals"], "away_goals"),
            )
        engine.apply_batch(fixtures, labels)
    return features


def outer_folds(rows: list[dict[str, Any]]) -> list[tuple[int, int, int]]:
    batches = batch_ranges(rows)
    if len(batches) < 40:
        raise GovernanceError("not enough chronological batches for 8 outer folds")
    first_test_batch = max(8, int(len(batches) * 0.45))
    remaining = len(batches) - first_test_batch
    if remaining < 16:
        raise GovernanceError("insufficient outer-test chronology")
    folds = []
    for k in range(8):
        bs = first_test_batch + (remaining * k) // 8
        be = first_test_batch + (remaining * (k + 1)) // 8
        if be <= bs:
            raise GovernanceError("empty outer fold")
        train_end = batches[bs][0]
        test_start = batches[bs][0]
        test_end = batches[be - 1][1]
        folds.append((train_end, test_start, test_end))
    return folds


def binary_ll(p: float, y: int) -> float:
    p = min(1.0 - EPS, max(EPS, float(p)))
    return -(y * math.log(p) + (1 - y) * math.log(1.0 - p))


def multiclass_metrics(probs: dict[str, float], actual: str) -> tuple[float, float, float]:
    ll = -math.log(max(EPS, probs[actual]))
    brier = sum((probs[k] - (1.0 if k == actual else 0.0)) ** 2 for k in CLASSES)
    order = ["home", "draw", "away"]
    ai = order.index(actual)
    cp = co = score = 0.0
    for i in range(len(order) - 1):
        cp += probs[order[i]]
        co += 1.0 if ai == i else 0.0
        score += (cp - co) ** 2
    return ll, brier, score / 2.0


def evaluate_predictions(items: list[dict[str, Any]]) -> dict[str, Any]:
    if not items:
        raise GovernanceError("cannot evaluate empty predictions")
    ll = brier = rps = exact_ll = 0.0
    draw_ll = draw_brier = 0.0
    draw_actual = draw_predicted = draw_correct = 0
    event_stats = {s: {"ll": 0.0, "brier": 0.0, "pred_sum": 0.0, "actual": 0} for s in ("0-0", "1-1", "2-2")}
    class_bins = {c: [[] for _ in range(10)] for c in CLASSES}
    top1_correct = 0
    for item in items:
        probs = item["probs"]
        actual = item["actual"]
        a, b, r = multiclass_metrics(probs, actual)
        ll += a; brier += b; rps += r
        top = max(CLASSES, key=lambda c: (probs[c], -CLASSES.index(c)))
        top1_correct += int(top == actual)
        ydraw = int(actual == "draw")
        draw_actual += ydraw
        draw_predicted += int(top == "draw")
        draw_correct += int(top == "draw" and actual == "draw")
        draw_ll += binary_ll(probs["draw"], ydraw)
        draw_brier += (probs["draw"] - ydraw) ** 2
        matrix = item["matrix"]
        hg, ag = item["home_goals"], item["away_goals"]
        pscore = exact_score_probability(matrix, hg, ag)
        exact_ll += -math.log(max(EPS, pscore))
        for score in event_stats:
            x, y = map(int, score.split("-"))
            p = exact_score_probability(matrix, x, y)
            obs = int(hg == x and ag == y)
            event_stats[score]["ll"] += binary_ll(p, obs)
            event_stats[score]["brier"] += (p - obs) ** 2
            event_stats[score]["pred_sum"] += p
            event_stats[score]["actual"] += obs
        for c in CLASSES:
            p = probs[c]
            idx = min(9, int(p * 10))
            class_bins[c][idx].append((p, int(actual == c)))
    n = len(items)
    eces = {}
    for c in CLASSES:
        ece = 0.0
        for bucket in class_bins[c]:
            if not bucket:
                continue
            conf = sum(p for p, _ in bucket) / len(bucket)
            obs = sum(y for _, y in bucket) / len(bucket)
            ece += len(bucket) / n * abs(conf - obs)
        eces[c] = ece
    score_events = {}
    for score, st in event_stats.items():
        score_events[score] = {
            "binary_logloss": st["ll"] / n,
            "brier": st["brier"] / n,
            "mean_probability": st["pred_sum"] / n,
            "observed_rate": st["actual"] / n,
            "absolute_calibration_error": abs(st["pred_sum"] / n - st["actual"] / n),
            "actual_n": st["actual"],
        }
    return {
        "n": n,
        "top1": top1_correct / n,
        "logloss": ll / n,
        "brier": brier / n,
        "rps": rps / n,
        "macro_ece": sum(eces.values()) / 3.0,
        "class_ece": eces,
        "draw_binary_logloss": draw_ll / n,
        "draw_brier": draw_brier / n,
        "draw_actual_n": draw_actual,
        "draw_top1_predicted_n": draw_predicted,
        "draw_recall": draw_correct / draw_actual if draw_actual else None,
        "draw_precision": draw_correct / draw_predicted if draw_predicted else None,
        "exact_score_logloss": exact_ll / n,
        "score_events": score_events,
    }

