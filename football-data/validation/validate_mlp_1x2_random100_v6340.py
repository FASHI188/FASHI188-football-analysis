#!/usr/bin/env python3
"""V6.34.0 lightweight neural-network 1X2 random100 challenge.

Purpose
-------
Test whether a small feed-forward neural network (MLP) extracts useful nonlinear signal
from the already-built V6.32 pre-match feature panel. This is deliberately a fast
challenge, not a new data pipeline.

Discipline
----------
- Reuse the exact V6.32 PIT feature builder; no new post-match information.
- Select architecture/regularization/blend only on 2023/24 and 2024/25 rolling validation.
- 2025/26 is untouched until model selection is frozen.
- Fixed new random100 seed 634100; all sampled matches count.
- No confidence filtering, league dropping, threshold tuning, seed replacement or
  post-hoc parameter changes are permitted.
- Development research only. formal_weight=0; CURRENT cannot auto-promote.
"""
from __future__ import annotations

import json
import math
import random
import warnings
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sklearn.exceptions import ConvergenceWarning
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler

import validate_direct_xg_shot_market_catboost_random100_v6320 as base

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "v6_mlp_1x2_random100_v6340_status.json"
SEED = 634100
TARGET = 100
TRAIN_SEASONS = ("2022/23", "2023/24", "2024/25")
TEST_SEASON = "2025/26"

# Small predeclared search space. No 2025/26 tuning.
ARCHITECTURES = ((32,), (64, 32))
ALPHAS = (0.001, 0.01)
MARKET_BLEND_WEIGHTS = (0.0, 0.25, 0.50)
MAX_ITER = 300
LEARNING_RATE_INIT = 0.001
EPS = 1e-12

warnings.filterwarnings("ignore", category=ConvergenceWarning)


def _geometric_pool(model: list[float], market: list[float], market_weight: float) -> list[float]:
    if market_weight <= 0.0:
        return [float(x) for x in model]
    logs = [
        (1.0 - market_weight) * math.log(max(EPS, float(a)))
        + market_weight * math.log(max(EPS, float(b)))
        for a, b in zip(model, market)
    ]
    m = max(logs)
    raw = [math.exp(x - m) for x in logs]
    z = sum(raw)
    return [x / z for x in raw]


def _metrics(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    n = len(rows)
    hits = 0
    brier = logloss = rps = 0.0
    predicted = Counter()
    for row in rows:
        p = [float(x) for x in row[key]]
        y = int(row["y"])
        pick = max(range(3), key=lambda i: p[i])
        hits += int(pick == y)
        predicted[str(pick)] += 1
        brier += sum((p[i] - (1.0 if i == y else 0.0)) ** 2 for i in range(3))
        logloss -= math.log(max(EPS, p[y]))
        c1 = p[0] - (1.0 if y == 0 else 0.0)
        c2 = p[0] + p[1] - (1.0 if y <= 1 else 0.0)
        rps += (c1 * c1 + c2 * c2) / 2.0
    return {
        "count": n,
        "hits": hits,
        "top1": hits / n if n else None,
        "brier": brier / n if n else None,
        "logloss": logloss / n if n else None,
        "rps": rps / n if n else None,
        "predicted_counts": dict(predicted),
    }


def _fit(train: list[dict[str, Any]], hidden: tuple[int, ...], alpha: float) -> tuple[StandardScaler, MLPClassifier]:
    scaler = StandardScaler()
    x = scaler.fit_transform([r["x"] for r in train])
    y = [int(r["y"]) for r in train]
    model = MLPClassifier(
        hidden_layer_sizes=hidden,
        activation="relu",
        solver="adam",
        alpha=float(alpha),
        batch_size=128,
        learning_rate="constant",
        learning_rate_init=LEARNING_RATE_INIT,
        max_iter=MAX_ITER,
        shuffle=True,
        random_state=SEED,
        tol=1e-4,
        n_iter_no_change=30,
        early_stopping=False,
    )
    model.fit(x, y)
    return scaler, model


def _raw_probs(scaler: StandardScaler, model: MLPClassifier, rows: list[dict[str, Any]]) -> list[list[float]]:
    x = scaler.transform([r["x"] for r in rows])
    raw = model.predict_proba(x)
    # Classes are expected to be [0,1,2], but map explicitly for auditability.
    classes = [int(v) for v in model.classes_]
    out = []
    for q in raw:
        by_class = {c: float(v) for c, v in zip(classes, q)}
        p = [by_class.get(i, 0.0) for i in range(3)]
        z = sum(p)
        if z <= 0.0:
            raise RuntimeError("MLP produced non-positive probability sum")
        out.append([v / z for v in p])
    return out


def _validation_select(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    folds = [
        (("2022/23",), "2023/24"),
        (("2022/23", "2023/24"), "2024/25"),
    ]
    leaderboard: list[dict[str, Any]] = []
    for hidden in ARCHITECTURES:
        for alpha in ALPHAS:
            fold_cache = []
            for train_seasons, valid_season in folds:
                train = [r for r in rows if r["season"] in train_seasons]
                valid = [dict(r) for r in rows if r["season"] == valid_season]
                scaler, model = _fit(train, hidden, alpha)
                raw = _raw_probs(scaler, model, valid)
                fold_cache.append((valid, raw, int(model.n_iter_), float(model.loss_)))

            for blend in MARKET_BLEND_WEIGHTS:
                fold_summaries = []
                for valid, raw, n_iter, loss in fold_cache:
                    for row, q in zip(valid, raw):
                        row["candidate"] = _geometric_pool(q, row["market"], blend)
                    fold_summaries.append({
                        "candidate": _metrics(valid, "candidate"),
                        "market": _metrics(valid, "market"),
                        "n_iter": n_iter,
                        "training_loss": loss,
                    })
                mean_top1 = sum(f["candidate"]["top1"] for f in fold_summaries) / len(fold_summaries)
                mean_logloss = sum(f["candidate"]["logloss"] for f in fold_summaries) / len(fold_summaries)
                mean_rps = sum(f["candidate"]["rps"] for f in fold_summaries) / len(fold_summaries)
                market_logloss = sum(f["market"]["logloss"] for f in fold_summaries) / len(fold_summaries)
                market_rps = sum(f["market"]["rps"] for f in fold_summaries) / len(fold_summaries)
                leaderboard.append({
                    "hidden": list(hidden),
                    "alpha": alpha,
                    "market_blend": blend,
                    "mean_top1": mean_top1,
                    "mean_logloss": mean_logloss,
                    "mean_rps": mean_rps,
                    "mean_market_logloss": market_logloss,
                    "mean_market_rps": market_rps,
                    "proper_guard": bool(mean_logloss <= market_logloss + 0.01 and mean_rps <= market_rps + 0.01),
                    "folds": fold_summaries,
                })
    eligible = [x for x in leaderboard if x["proper_guard"]] or leaderboard
    selected = min(
        eligible,
        key=lambda x: (-x["mean_top1"], x["mean_logloss"], x["mean_rps"], tuple(x["hidden"]), x["alpha"], x["market_blend"]),
    )
    leaderboard.sort(key=lambda x: (-x["mean_top1"], x["mean_logloss"], x["mean_rps"]))
    return selected, leaderboard[:12]


def main() -> int:
    rows, data_audit, feature_names = base._build_rows()
    counts = Counter(r["season"] for r in rows)
    if any(counts[s] < 700 for s in TRAIN_SEASONS + (TEST_SEASON,)):
        raise RuntimeError(f"insufficient rows by season: {dict(counts)}")

    selected, leaderboard = _validation_select(rows)
    final_train = [r for r in rows if r["season"] in TRAIN_SEASONS]
    test = [dict(r) for r in rows if r["season"] == TEST_SEASON]

    hidden = tuple(int(v) for v in selected["hidden"])
    scaler, model = _fit(final_train, hidden, float(selected["alpha"]))
    raw = _raw_probs(scaler, model, test)
    for row, q in zip(test, raw):
        row["candidate"] = _geometric_pool(q, row["market"], float(selected["market_blend"]))

    ordered = sorted(test, key=lambda r: (r["competition_id"], r["date"], r["home_team"], r["away_team"]))
    random.Random(SEED).shuffle(ordered)
    sample = ordered[:TARGET]
    if len(sample) != TARGET:
        raise RuntimeError(f"random100 sample incomplete: {len(sample)}")

    sample_metrics = {
        "market": _metrics(sample, "market"),
        "formal": _metrics(sample, "formal"),
        "candidate": _metrics(sample, "candidate"),
    }
    full_metrics = {
        "market": _metrics(test, "market"),
        "formal": _metrics(test, "formal"),
        "candidate": _metrics(test, "candidate"),
    }

    by_comp = {}
    for cid in base.DOMAINS:
        rs = [r for r in sample if r["competition_id"] == cid]
        if rs:
            by_comp[cid] = {
                "count": len(rs),
                "market_top1": _metrics(rs, "market")["top1"],
                "formal_top1": _metrics(rs, "formal")["top1"],
                "candidate_top1": _metrics(rs, "candidate")["top1"],
            }

    target65 = sample_metrics["candidate"]["top1"] >= 0.65 - 1e-12
    beats_market_3pp = sample_metrics["candidate"]["top1"] >= sample_metrics["market"]["top1"] + 0.03 - 1e-12

    payload = {
        "schema_version": "V6.34.0-mlp-1x2-random100-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "formal_weight": 0,
        "classification": "DEVELOPMENT_RESEARCH_FIXED_UNFILTERED_RANDOM100_NO_PROMOTION",
        "purpose": "FAST_NEURAL_NETWORK_INFORMATION_CHALLENGE",
        "sample_contract": {
            "test_season": TEST_SEASON,
            "eligible_domains": list(base.DOMAINS),
            "seed": SEED,
            "sample_count": TARGET,
            "all_sampled_matches_scored": True,
            "confidence_filtering": False,
            "posthoc_league_dropping": False,
            "threshold_tuning": False,
            "seed_replacement": False,
        },
        "data_contract": {
            "reuses_v632_feature_builder": True,
            "new_data_pipeline": False,
            "feature_count": len(feature_names),
            "data_audit": data_audit,
            "rows_by_season": dict(sorted(counts.items())),
        },
        "model": {
            "library": "scikit-learn",
            "estimator": "MLPClassifier",
            "activation": "relu",
            "solver": "adam",
            "standard_scaler_fit_train_only": True,
            "max_iter": MAX_ITER,
            "learning_rate_init": LEARNING_RATE_INIT,
            "selection_folds": ["2022/23->2023/24", "2022/23+2023/24->2024/25"],
            "predeclared_grid": {
                "hidden_layer_sizes": [list(v) for v in ARCHITECTURES],
                "alpha": list(ALPHAS),
                "market_blend": list(MARKET_BLEND_WEIGHTS),
            },
            "selected": {k: selected[k] for k in ("hidden", "alpha", "market_blend", "mean_top1", "mean_logloss", "mean_rps", "proper_guard")},
            "final_n_iter": int(model.n_iter_),
            "final_training_loss": float(model.loss_),
            "validation_leaderboard_top12": leaderboard,
        },
        "full_2025_26_development_metrics": full_metrics,
        "random100": {
            "metrics": sample_metrics,
            "by_competition": by_comp,
            "candidate_vs_market_top1_pp": (sample_metrics["candidate"]["top1"] - sample_metrics["market"]["top1"]) * 100.0,
            "candidate_vs_formal_top1_pp": (sample_metrics["candidate"]["top1"] - sample_metrics["formal"]["top1"]) * 100.0,
            "target_65_reached": target65,
            "fast_gate_beats_market_by_at_least_3pp": beats_market_3pp,
        },
        "decision": (
            "ADVANCE_TO_FIXED_RANDOM300_CHALLENGE_WITHOUT_REDESIGN"
            if beats_market_3pp
            else "DO_NOT_ADVANCE_MLP_AS_CONFIGURED_NEW_NEURAL_SIGNAL_NOT_LARGE_ENOUGH"
        ),
        "governance": {
            "current_unchanged": True,
            "no_automatic_promotion": True,
            "do_not_tune_on_viewed_random100": True,
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "selected": payload["model"]["selected"],
        "random100": payload["random100"],
        "decision": payload["decision"],
        "out": str(OUT),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
