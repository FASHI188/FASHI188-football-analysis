#!/usr/bin/env python3
"""V6.42.0 specialized draw-vs-decisive hurdle challenge.

Instead of asking one 3-class model to learn home/draw/away simultaneously, train a
binary model whose only task is DRAW vs DECISIVE. If it does not call a draw, the
closing market chooses the stronger decisive side (home vs away).

This differs from the old V6.0.1 draw-ratio rule: the draw decision is learned from
strict pre-match market/form/xG/shot/Elo/player and optional market-path features,
not from manually lowering the market draw probability ratio.

Selection is historical only. A_FAST100 labels are opened only after a two-fold
historical gate. B300/C100 remain closed. Market probability vector is unchanged.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from catboost import CatBoostClassifier

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "validation", ROOT / "engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import validate_market_error_meta_gold500_v6400 as v640  # noqa: E402

OUT = ROOT / "manifests" / "v6_specialized_draw_hurdle_gold500_v6420_status.json"
SEED = 642100
FEATURE_SPECS = v640.FEATURE_SPECS
DEPTHS = (3, 5)
L2S = (20.0, 50.0)
DRAW_WEIGHTS = (1.0, 1.3, 1.6)
DRAW_THRESHOLDS = (0.22, 0.25, 0.28, 0.31, 0.34, 0.37, 0.40)
ITERATIONS = 360
LEARNING_RATE = 0.035
HIST_MEAN_UPLIFT_MIN_PP = 0.50
HIST_MIN_FOLD_UPLIFT_PP = 0.00
FAST_REQUIRED_HITS = 63
FAST_REQUIRED_UPLIFT_PP = 3.0


def fit_draw_model(rows: list[dict[str, Any]], spec: str, depth: int, l2: float, draw_weight: float) -> CatBoostClassifier:
    y = [1 if int(r["y"]) == 1 else 0 for r in rows]
    if len(set(y)) != 2:
        raise RuntimeError(f"draw target collapsed: {Counter(y)}")
    weights = [float(draw_weight) if label == 1 else 1.0 for label in y]
    model = CatBoostClassifier(
        loss_function="Logloss", iterations=ITERATIONS, depth=int(depth),
        learning_rate=LEARNING_RATE, l2_leaf_reg=float(l2), random_seed=SEED,
        random_strength=0.4, bootstrap_type="Bayesian", bagging_temperature=0.4,
        allow_writing_files=False, verbose=False, thread_count=2,
    )
    model.fit([v640.feature_vector(r, spec) for r in rows], y, sample_weight=weights)
    return model


def predict_draw(rows: list[dict[str, Any]], spec: str, model: CatBoostClassifier) -> list[float]:
    return [float(x) for x in model.predict_proba([v640.feature_vector(r, spec) for r in rows])[:, 1]]


def market_decisive_side(row: dict[str, Any]) -> int:
    p = row["market"]
    return 0 if float(p[0]) >= float(p[2]) else 2


def market_pick(row: dict[str, Any]) -> int:
    return max(range(3), key=lambda i: float(row["market"][i]))


def score(rows: list[dict[str, Any]], pdraw: list[float], threshold: float) -> dict[str, Any]:
    market_hits = candidate_hits = 0
    draw_calls = draw_hits = draw_false = 0
    overrides = wins = losses = neutral = 0
    predicted = Counter(); actual = Counter(); detail = []
    for row, pd in zip(rows, pdraw):
        y = int(row["y"])
        mp = market_pick(row)
        cp = 1 if float(pd) >= float(threshold) else market_decisive_side(row)
        market_ok = mp == y; candidate_ok = cp == y
        market_hits += int(market_ok); candidate_hits += int(candidate_ok)
        predicted[str(cp)] += 1; actual[str(y)] += 1
        if cp == 1:
            draw_calls += 1
            draw_hits += int(y == 1)
            draw_false += int(y != 1)
        if cp != mp:
            overrides += 1
            if (not market_ok) and candidate_ok:
                wins += 1
            elif market_ok:
                losses += 1
            else:
                neutral += 1
        detail.append({
            "p_draw": float(pd), "market_pick": mp, "candidate_pick": cp,
            "market_correct": market_ok, "candidate_correct": candidate_ok,
        })
    n = len(rows)
    return {
        "count": n, "market_hits": market_hits, "candidate_hits": candidate_hits,
        "market_top1": market_hits / n, "candidate_top1": candidate_hits / n,
        "uplift_pp": (candidate_hits - market_hits) * 100.0 / n,
        "draw_call_count": draw_calls, "draw_hit_count": draw_hits,
        "draw_precision": draw_hits / draw_calls if draw_calls else None,
        "draw_false_count": draw_false,
        "override_count": overrides, "override_wins": wins, "override_losses": losses,
        "override_neutral": neutral, "override_net": wins - losses,
        "predicted_counts": dict(predicted), "actual_counts": dict(actual), "detail": detail,
    }


def strip_score(s: dict[str, Any]) -> dict[str, Any]:
    x = dict(s); x.pop("detail", None); return x


def select_candidate(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    aggregate: dict[tuple[Any, ...], dict[str, Any]] = {}
    for fold_id, (train_seasons, val_season) in enumerate(v640.FOLDS):
        tr = [r for r in rows if r["season"] in train_seasons]
        va = [r for r in rows if r["season"] == val_season]
        if len(tr) < 900 or len(va) < 900:
            raise RuntimeError(f"fold coverage too small train={len(tr)} val={len(va)}")
        for spec in FEATURE_SPECS:
            for depth in DEPTHS:
                for l2 in L2S:
                    for dw in DRAW_WEIGHTS:
                        model = fit_draw_model(tr, spec, depth, l2, dw)
                        pdraw = predict_draw(va, spec, model)
                        for threshold in DRAW_THRESHOLDS:
                            s = strip_score(score(va, pdraw, threshold))
                            key = (spec, depth, l2, dw, threshold)
                            rec = aggregate.setdefault(key, {
                                "spec": spec, "depth": depth, "l2": l2,
                                "draw_weight": dw, "draw_threshold": threshold, "folds": [],
                            })
                            rec["folds"].append({
                                "fold_id": fold_id, "train": list(train_seasons), "validate": val_season, **s
                            })

    leaderboard = []
    for rec in aggregate.values():
        folds = rec["folds"]
        if len(folds) != len(v640.FOLDS):
            continue
        mean_uplift = sum(float(f["uplift_pp"]) for f in folds) / len(folds)
        min_uplift = min(float(f["uplift_pp"]) for f in folds)
        leaderboard.append({
            **rec,
            "mean_uplift_pp": mean_uplift,
            "min_fold_uplift_pp": min_uplift,
            "mean_override_net": sum(int(f["override_net"]) for f in folds) / len(folds),
            "mean_draw_calls": sum(int(f["draw_call_count"]) for f in folds) / len(folds),
            "mean_draw_precision": sum(float(f["draw_precision"] or 0.0) for f in folds) / len(folds),
            "historical_gate": mean_uplift >= HIST_MEAN_UPLIFT_MIN_PP and min_uplift >= HIST_MIN_FOLD_UPLIFT_PP,
        })
    if not leaderboard:
        raise RuntimeError("empty V6.42 leaderboard")
    leaderboard.sort(key=lambda x: (
        0 if x["historical_gate"] else 1,
        -float(x["mean_uplift_pp"]), -float(x["min_fold_uplift_pp"]),
        -float(x["mean_override_net"]), -float(x["mean_draw_precision"]),
        float(x["mean_draw_calls"]), FEATURE_SPECS.index(x["spec"]),
        int(x["depth"]), float(x["l2"]), float(x["draw_weight"]), float(x["draw_threshold"]),
    ))
    return leaderboard[0], leaderboard


def main() -> int:
    historical, build_audit = v640.build_historical_rows()
    selected, leaderboard = select_candidate(historical)
    payload: dict[str, Any] = {
        "schema_version": "V6.42.0-specialized-draw-hurdle-gold500-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS", "formal_current_version": "V5.0.1", "formal_weight": 0,
        "classification": "DEVELOPMENT_RESEARCH_SPECIALIZED_DRAW_VS_DECISIVE_HURDLE",
        "governance_contract": {
            "probability_vector_changed": False,
            "proper_scores_equal_market_by_construction": True,
            "manual_draw_probability": False,
            "A_FAST100_opened_only_after_historical_gate": True,
            "B_CONFIRM300_labels_read": False, "B_CONFIRM300_scored": False,
            "C_SEALED100_labels_present": False,
            "confidence_filtering": False, "league_dropping": False,
            "seed_replacement": False, "A100_parameter_tuning": False,
            "CURRENT_unchanged": True,
        },
        "architecture": {
            "draw_head": "binary CatBoost DRAW vs DECISIVE",
            "non_draw_decision": "closing market chooses stronger home/away side",
            "probabilities": "unchanged closing-market 1X2",
            "feature_specs": list(FEATURE_SPECS), "depths": list(DEPTHS),
            "l2_grid": list(L2S), "draw_weight_grid": list(DRAW_WEIGHTS),
            "draw_threshold_grid": list(DRAW_THRESHOLDS),
            "rolling_folds": [{"train": list(a), "validate": b} for a, b in v640.FOLDS],
            "historical_gate": {"mean_uplift_min_pp": HIST_MEAN_UPLIFT_MIN_PP, "min_fold_uplift_pp": HIST_MIN_FOLD_UPLIFT_PP},
        },
        "build_audit": build_audit,
        "historical_selection": {"selected": selected, "leaderboard": leaderboard},
    }

    if not bool(selected["historical_gate"]):
        payload["fast100"] = {"opened": False, "reason": "historical gate failed; A_FAST100 labels not read"}
        payload["decision"] = "HISTORICAL_GATE_FAILED_A100_NOT_OPENED"
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({
            "status": payload["status"], "decision": payload["decision"],
            "historical_rows": build_audit["joined_by_season"],
            "selected": {k: selected[k] for k in ("spec", "depth", "l2", "draw_weight", "draw_threshold", "mean_uplift_pp", "min_fold_uplift_pp", "mean_draw_calls", "mean_draw_precision", "historical_gate")},
        }, ensure_ascii=False, indent=2))
        return 0

    final_train = [r for r in historical if r["season"] in v640.TRAIN_SEASONS]
    model = fit_draw_model(final_train, str(selected["spec"]), int(selected["depth"]), float(selected["l2"]), float(selected["draw_weight"]))
    fast = v640.build_fast_rows()
    v640.attach_fast_labels(fast)
    pdraw = predict_draw(fast, str(selected["spec"]), model)
    s = score(fast, pdraw, float(selected["draw_threshold"]))
    fast_gate = int(s["candidate_hits"]) >= FAST_REQUIRED_HITS and float(s["uplift_pp"]) >= FAST_REQUIRED_UPLIFT_PP

    changed = []
    for row, item in zip(fast, s["detail"]):
        if int(item["candidate_pick"]) != int(item["market_pick"]):
            changed.append({
                "gold_index": int(row["gold_index"]), "competition_id": row["competition_id"],
                "date": row["date"], "home_team": row["home_team"], "away_team": row["away_team"],
                "actual_result": int(row["y"]), "market_pick": int(item["market_pick"]),
                "candidate_pick": int(item["candidate_pick"]), "p_draw": float(item["p_draw"]),
                "market_correct": bool(item["market_correct"]), "candidate_correct": bool(item["candidate_correct"]),
                "market": row["market"],
            })

    payload["fast100"] = {
        "opened": True,
        "market": {"count": 100, "hits": int(s["market_hits"]), "top1": float(s["market_top1"])},
        "candidate": {"count": 100, "hits": int(s["candidate_hits"]), "top1": float(s["candidate_top1"])},
        "proper_scores": "identical to market by construction",
        "candidate_vs_market_top1_pp": float(s["uplift_pp"]),
        "draw_call_count": int(s["draw_call_count"]), "draw_hit_count": int(s["draw_hit_count"]),
        "draw_precision": s["draw_precision"], "draw_false_count": int(s["draw_false_count"]),
        "override_count": int(s["override_count"]), "override_wins": int(s["override_wins"]),
        "override_losses": int(s["override_losses"]), "override_neutral": int(s["override_neutral"]),
        "override_net": int(s["override_net"]), "predicted_counts": s["predicted_counts"],
        "actual_counts": s["actual_counts"],
        "required_hits": FAST_REQUIRED_HITS, "required_uplift_pp": FAST_REQUIRED_UPLIFT_PP,
        "gate_passed": bool(fast_gate), "changed_pick_audit": changed,
    }
    payload["decision"] = "OPEN_CONFIRM300" if fast_gate else "FAST100_FAILED_CONFIRM300_NOT_OPENED"
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"], "decision": payload["decision"],
        "historical_rows": build_audit["joined_by_season"],
        "selected": {k: selected[k] for k in ("spec", "depth", "l2", "draw_weight", "draw_threshold", "mean_uplift_pp", "min_fold_uplift_pp", "mean_draw_calls", "mean_draw_precision", "historical_gate")},
        "fast100": {k: payload["fast100"][k] for k in ("market", "candidate", "candidate_vs_market_top1_pp", "draw_call_count", "draw_hit_count", "draw_precision", "override_net", "predicted_counts", "actual_counts", "gate_passed")},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
