#!/usr/bin/env python3
"""V6.41.0 single-stage composite market-action challenge.

V6.40 used two separately trained heads: detect a market error, then select an
alternate. V6.41 removes that decomposition and trains one multiclass model on
the exact final action space:

  0 KEEP   = keep closing-market Top-1
  1 SECOND = replace Top-1 with market second-ranked outcome
  2 THIRD  = replace Top-1 with market third-ranked outcome

For every historical match exactly one action is correct. This directly aligns
the training target with Top-1 decision utility and preserves the market 1X2
probability vector unchanged. A conservative KEEP bias is selected on historical
rolling validation; all matches are still scored.

Research only. A_FAST100 is opened only if historical gate passes. B300/C100
remain closed. CURRENT V5.0.1 unchanged and formal_weight=0.
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

OUT = ROOT / "manifests" / "v6_composite_market_action_gold500_v6410_status.json"
SEED = 641100
FEATURE_SPECS = v640.FEATURE_SPECS
DEPTHS = (3, 5)
L2S = (20.0, 50.0)
KEEP_BIASES = (1.00, 1.05, 1.10, 1.20, 1.30, 1.50)
ITERATIONS = 360
LEARNING_RATE = 0.035
HIST_MEAN_UPLIFT_MIN_PP = 0.50
HIST_MIN_FOLD_UPLIFT_PP = 0.00
FAST_REQUIRED_HITS = 63
FAST_REQUIRED_UPLIFT_PP = 3.0


def action_target(row: dict[str, Any]) -> int:
    order = v640.order_market(row["market"])
    y = int(row["y"])
    if y == order[0]:
        return 0
    if y == order[1]:
        return 1
    if y == order[2]:
        return 2
    raise RuntimeError(f"invalid action target y={y} order={order}")


def fit_model(rows: list[dict[str, Any]], spec: str, depth: int, l2: float) -> CatBoostClassifier:
    y = [action_target(r) for r in rows]
    if len(set(y)) != 3:
        raise RuntimeError(f"action target collapsed: {Counter(y)}")
    model = CatBoostClassifier(
        loss_function="MultiClass", iterations=ITERATIONS, depth=int(depth),
        learning_rate=LEARNING_RATE, l2_leaf_reg=float(l2), random_seed=SEED,
        random_strength=0.4, bootstrap_type="Bayesian", bagging_temperature=0.4,
        allow_writing_files=False, verbose=False, thread_count=2,
    )
    model.fit([v640.feature_vector(r, spec) for r in rows], y)
    return model


def predict_actions(rows: list[dict[str, Any]], spec: str, model: CatBoostClassifier) -> list[list[float]]:
    probs = model.predict_proba([v640.feature_vector(r, spec) for r in rows])
    return [[float(x) for x in p] for p in probs]


def score(rows: list[dict[str, Any]], probs: list[list[float]], keep_bias: float) -> dict[str, Any]:
    market_hits = candidate_hits = overrides = wins = losses = neutral = 0
    predicted = Counter(); actual = Counter(); override_to = Counter(); action_counts = Counter()
    detail = []
    for row, p in zip(rows, probs):
        order = v640.order_market(row["market"])
        keep_score = float(p[0]) * float(keep_bias)
        alt_action = 1 if float(p[1]) >= float(p[2]) else 2
        action = 0 if keep_score >= float(p[alt_action]) else alt_action
        mp = order[0]
        cp = order[action]
        y = int(row["y"])
        market_ok = mp == y; candidate_ok = cp == y
        market_hits += int(market_ok); candidate_hits += int(candidate_ok)
        predicted[str(cp)] += 1; actual[str(y)] += 1; action_counts[str(action)] += 1
        if action != 0:
            overrides += 1; override_to[str(cp)] += 1
            if (not market_ok) and candidate_ok:
                wins += 1
            elif market_ok:
                losses += 1
            else:
                neutral += 1
        detail.append({
            "market_pick": mp, "candidate_pick": cp, "action": action,
            "p_keep": float(p[0]), "p_second": float(p[1]), "p_third": float(p[2]),
            "market_correct": market_ok, "candidate_correct": candidate_ok,
        })
    n = len(rows)
    return {
        "count": n, "market_hits": market_hits, "candidate_hits": candidate_hits,
        "market_top1": market_hits / n, "candidate_top1": candidate_hits / n,
        "uplift_pp": (candidate_hits - market_hits) * 100.0 / n,
        "override_count": overrides, "override_rate": overrides / n,
        "override_wins": wins, "override_losses": losses, "override_neutral": neutral,
        "override_net": wins - losses, "predicted_counts": dict(predicted),
        "actual_counts": dict(actual), "action_counts": dict(action_counts),
        "override_to_counts": dict(override_to), "detail": detail,
    }


def strip_score(s: dict[str, Any]) -> dict[str, Any]:
    out = dict(s); out.pop("detail", None); return out


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
                    model = fit_model(tr, spec, depth, l2)
                    probs = predict_actions(va, spec, model)
                    for bias in KEEP_BIASES:
                        s = strip_score(score(va, probs, bias))
                        key = (spec, depth, l2, bias)
                        rec = aggregate.setdefault(key, {
                            "spec": spec, "depth": depth, "l2": l2, "keep_bias": bias, "folds": []
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
            "mean_override_count": sum(int(f["override_count"]) for f in folds) / len(folds),
            "historical_gate": mean_uplift >= HIST_MEAN_UPLIFT_MIN_PP and min_uplift >= HIST_MIN_FOLD_UPLIFT_PP,
        })
    if not leaderboard:
        raise RuntimeError("empty V6.41 leaderboard")
    leaderboard.sort(key=lambda x: (
        0 if x["historical_gate"] else 1,
        -float(x["mean_uplift_pp"]), -float(x["min_fold_uplift_pp"]),
        -float(x["mean_override_net"]), float(x["mean_override_count"]),
        FEATURE_SPECS.index(x["spec"]), int(x["depth"]), float(x["l2"]), float(x["keep_bias"]),
    ))
    return leaderboard[0], leaderboard


def main() -> int:
    historical, build_audit = v640.build_historical_rows()
    selected, leaderboard = select_candidate(historical)
    payload: dict[str, Any] = {
        "schema_version": "V6.41.0-composite-market-action-gold500-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS", "formal_current_version": "V5.0.1", "formal_weight": 0,
        "classification": "DEVELOPMENT_RESEARCH_SINGLE_STAGE_COMPOSITE_MARKET_ACTION",
        "governance_contract": {
            "probability_vector_changed": False,
            "proper_scores_equal_market_by_construction": True,
            "A_FAST100_opened_only_after_historical_gate": True,
            "B_CONFIRM300_labels_read": False, "B_CONFIRM300_scored": False,
            "C_SEALED100_labels_present": False,
            "confidence_filtering": False, "league_dropping": False,
            "seed_replacement": False, "A100_parameter_tuning": False,
            "CURRENT_unchanged": True,
        },
        "architecture": {
            "action_space": {"0": "KEEP_MARKET_TOP1", "1": "USE_MARKET_SECOND", "2": "USE_MARKET_THIRD"},
            "model": "single CatBoost MultiClass over composite actions",
            "decision": "argmax action after multiplying KEEP probability by historically selected keep_bias",
            "probabilities": "unchanged closing-market 1X2",
            "feature_specs": list(FEATURE_SPECS), "depths": list(DEPTHS),
            "l2_grid": list(L2S), "keep_bias_grid": list(KEEP_BIASES),
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
            "selected": {k: selected[k] for k in ("spec", "depth", "l2", "keep_bias", "mean_uplift_pp", "min_fold_uplift_pp", "mean_override_count", "historical_gate")},
        }, ensure_ascii=False, indent=2))
        return 0

    final_train = [r for r in historical if r["season"] in v640.TRAIN_SEASONS]
    model = fit_model(final_train, str(selected["spec"]), int(selected["depth"]), float(selected["l2"]))
    fast = v640.build_fast_rows()
    v640.attach_fast_labels(fast)
    probs = predict_actions(fast, str(selected["spec"]), model)
    s = score(fast, probs, float(selected["keep_bias"]))
    fast_gate = int(s["candidate_hits"]) >= FAST_REQUIRED_HITS and float(s["uplift_pp"]) >= FAST_REQUIRED_UPLIFT_PP

    changed = []
    for row, item in zip(fast, s["detail"]):
        if int(item["candidate_pick"]) != int(item["market_pick"]):
            changed.append({
                "gold_index": int(row["gold_index"]), "competition_id": row["competition_id"],
                "date": row["date"], "home_team": row["home_team"], "away_team": row["away_team"],
                "actual_result": int(row["y"]), "market_pick": int(item["market_pick"]),
                "candidate_pick": int(item["candidate_pick"]), "action": int(item["action"]),
                "market_correct": bool(item["market_correct"]), "candidate_correct": bool(item["candidate_correct"]),
                "action_probability": [float(item["p_keep"]), float(item["p_second"]), float(item["p_third"])],
                "market": row["market"],
            })

    payload["fast100"] = {
        "opened": True,
        "market": {"count": 100, "hits": int(s["market_hits"]), "top1": float(s["market_top1"])},
        "candidate": {"count": 100, "hits": int(s["candidate_hits"]), "top1": float(s["candidate_top1"])},
        "proper_scores": "identical to market by construction",
        "candidate_vs_market_top1_pp": float(s["uplift_pp"]),
        "override_count": int(s["override_count"]), "override_wins": int(s["override_wins"]),
        "override_losses": int(s["override_losses"]), "override_neutral": int(s["override_neutral"]),
        "override_net": int(s["override_net"]), "predicted_counts": s["predicted_counts"],
        "actual_counts": s["actual_counts"], "action_counts": s["action_counts"],
        "override_to_counts": s["override_to_counts"],
        "required_hits": FAST_REQUIRED_HITS, "required_uplift_pp": FAST_REQUIRED_UPLIFT_PP,
        "gate_passed": bool(fast_gate), "changed_pick_audit": changed,
    }
    payload["decision"] = "OPEN_CONFIRM300" if fast_gate else "FAST100_FAILED_CONFIRM300_NOT_OPENED"
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"], "decision": payload["decision"],
        "historical_rows": build_audit["joined_by_season"],
        "selected": {k: selected[k] for k in ("spec", "depth", "l2", "keep_bias", "mean_uplift_pp", "min_fold_uplift_pp", "mean_override_count", "historical_gate")},
        "fast100": {k: payload["fast100"][k] for k in ("market", "candidate", "candidate_vs_market_top1_pp", "override_count", "override_wins", "override_losses", "override_neutral", "override_net", "predicted_counts", "actual_counts", "gate_passed")},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
