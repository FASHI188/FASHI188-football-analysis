"""Unified strict evaluator for Football3 1X2 prediction datasets.

Uses one metric implementation for historical development, rolling replay and
future confirmation: Top1 accuracy, multiclass log loss, Brier, RPS and draw
calibration. Paired comparisons require the exact same fixture set by default.
"""
from __future__ import annotations

import math
from typing import Iterable

from pipeline.unified_dataset import PredictionDatasetRow

CLASSES = ("home", "draw", "away")


def _top1(p):
    return max(CLASSES, key=lambda k: (float(p[k]), -CLASSES.index(k)))


def _validated_rows(rows: Iterable[PredictionDatasetRow]) -> tuple[PredictionDatasetRow, ...]:
    items = tuple(rows)
    ids = [r.fixture_id for r in items]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate fixture_id in evaluation rows")
    for row in items:
        if row.actual_result not in CLASSES:
            raise ValueError(f"fixture {row.fixture_id} is not settled")
        if set(row.probabilities) != set(CLASSES):
            raise ValueError("probabilities must contain exactly home/draw/away")
        p = {k: float(row.probabilities[k]) for k in CLASSES}
        if any((not math.isfinite(v) or v <= 0.0) for v in p.values()):
            raise ValueError("evaluation probabilities must be finite and positive")
        if abs(sum(p.values()) - 1.0) > 1e-9:
            raise ValueError("evaluation probabilities must sum to one")
        if row.top1 != _top1(p):
            raise ValueError("stored top1 does not match probabilities")
    return items


def _balanced_bins(items: tuple[PredictionDatasetRow, ...], n_bins: int = 5):
    ordered = sorted(items, key=lambda r: (float(r.probabilities["draw"]), r.fixture_id))
    bins = min(n_bins, len(ordered))
    if bins <= 0:
        return []
    base, extra = divmod(len(ordered), bins)
    out = []
    pos = 0
    for i in range(bins):
        size = base + (1 if i < extra else 0)
        out.append(ordered[pos:pos + size])
        pos += size
    return out


def evaluate(rows: Iterable[PredictionDatasetRow]) -> dict:
    items = _validated_rows(rows)
    n = len(items)
    if n == 0:
        raise ValueError("cannot evaluate empty dataset")

    hits = 0
    ll = 0.0
    brier = 0.0
    rps = 0.0
    draw_ll = 0.0
    draw_brier = 0.0
    picks = {k: 0 for k in CLASSES}
    hit_by = {k: 0 for k in CLASSES}
    actuals = {k: 0 for k in CLASSES}

    for row in items:
        p = {k: float(row.probabilities[k]) for k in CLASSES}
        y = row.actual_result
        pick = _top1(p)
        hit = int(pick == y)
        hits += hit
        picks[pick] += 1
        hit_by[pick] += hit
        actuals[y] += 1
        ll -= math.log(max(p[y], 1e-15))
        brier += sum((p[k] - (1.0 if y == k else 0.0)) ** 2 for k in CLASSES)
        ph, pd = p["home"], p["draw"]
        rps += (
            (ph - (1.0 if y == "home" else 0.0)) ** 2
            + ((ph + pd) - (1.0 if y in {"home", "draw"} else 0.0)) ** 2
        ) / 2.0
        yd = 1.0 if y == "draw" else 0.0
        draw_ll -= yd * math.log(max(pd, 1e-15)) + (1.0 - yd) * math.log(max(1.0 - pd, 1e-15))
        draw_brier += (pd - yd) ** 2

    draw_bins = []
    ece = 0.0
    for bucket in _balanced_bins(items, 5):
        mean_pred = sum(float(r.probabilities["draw"]) for r in bucket) / len(bucket)
        actual_rate = sum(1.0 if r.actual_result == "draw" else 0.0 for r in bucket) / len(bucket)
        weight = len(bucket) / n
        ece += weight * abs(mean_pred - actual_rate)
        draw_bins.append({"n": len(bucket), "mean_pred": mean_pred, "actual_rate": actual_rate})

    return {
        "count": n,
        "hits": hits,
        "top1_accuracy": hits / n,
        "logloss": ll / n,
        "brier": brier / n,
        "rps": rps / n,
        "top1_picks": picks,
        "top1_hits": hit_by,
        "actuals": actuals,
        "draw_calibration": {
            "n": n,
            "mean_pred": sum(float(r.probabilities["draw"]) for r in items) / n,
            "actual_rate": actuals["draw"] / n,
            "logloss": draw_ll / n,
            "brier": draw_brier / n,
            "ece5": ece,
            "bins": draw_bins,
        },
    }


def paired_compare(
    baseline_rows: Iterable[PredictionDatasetRow],
    candidate_rows: Iterable[PredictionDatasetRow],
) -> dict:
    base_items = _validated_rows(baseline_rows)
    cand_items = _validated_rows(candidate_rows)
    base = {r.fixture_id: r for r in base_items}
    cand = {r.fixture_id: r for r in cand_items}
    if set(base) != set(cand):
        raise ValueError("paired comparison requires exact same fixture set")
    for fid in base:
        if base[fid].actual_result != cand[fid].actual_result:
            raise ValueError(f"actual result mismatch for {fid}")

    bm = evaluate(base_items)
    cm = evaluate(cand_items)
    changed = 0
    to_draw = 0
    from_draw = 0
    for fid in sorted(base):
        b = base[fid].top1
        c = cand[fid].top1
        if b != c:
            changed += 1
            to_draw += int(c == "draw")
            from_draw += int(b == "draw")

    return {
        "fixture_count": len(base),
        "baseline": bm,
        "candidate": cm,
        "candidate_minus_baseline": {
            "hits": cm["hits"] - bm["hits"],
            "accuracy_pp": 100.0 * (cm["top1_accuracy"] - bm["top1_accuracy"]),
            "logloss": cm["logloss"] - bm["logloss"],
            "brier": cm["brier"] - bm["brier"],
            "rps": cm["rps"] - bm["rps"],
            "draw_logloss": cm["draw_calibration"]["logloss"] - bm["draw_calibration"]["logloss"],
            "draw_brier": cm["draw_calibration"]["brier"] - bm["draw_calibration"]["brier"],
        },
        "decision_changes": {
            "top1_changed_count": changed,
            "changed_to_draw_count": to_draw,
            "changed_from_draw_count": from_draw,
            "baseline_draw_picks": bm["top1_picks"]["draw"],
            "candidate_draw_picks": cm["top1_picks"]["draw"],
        },
    }
