#!/usr/bin/env python3
"""V6.52.0 selective rich-market Top-1 override.

Default decision is always closing-market Top-1. A fixed rich-market CatBoost model
(depth=4, draw sample weight=1.30) is used only to propose an alternate class.
Probability vectors are NOT modified; therefore Brier/log-loss/RPS remain exactly the
market's. This challenger asks only whether rare, high-conviction disagreement can
improve Top-1.

Selection discipline:
- model spec is fixed from V6.51 historical work; no A100 use;
- train 2022/23, choose one decision rule on 2023/24 only;
- retrain 2022/23+2023/24, validate that fixed rule on untouched 2024/25;
- A100 labels open only if 2024/25 holdout uplift >= +0.5pp, selection uplift > 0,
  and both selection/holdout have minimum intervention support;
- B300/C100 never read.

Research only; CURRENT V5.0.1 unchanged; formal_weight=0.
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from catboost import CatBoostClassifier

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "validation", ROOT / "engine"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import validate_rich_market_catboost_full500_v6510 as v651  # noqa: E402

OUT = ROOT / "manifests" / "v6_selective_rich_override_full500_v6520_status.json"
MODEL_DEPTH = 4
DRAW_WEIGHT = 1.30
ADV_THRESHOLDS = (0.00, 0.025, 0.05, 0.075, 0.10, 0.125, 0.15)
MARKET_MARGIN_CAPS = (0.05, 0.10, 0.15, 0.20, 0.30, 1.00)
MIN_SELECTION_OVERRIDES = 20
MIN_HOLDOUT_OVERRIDES = 15
HOLDOUT_REQUIRED_UPLIFT_PP = 0.5


def fit_model(train: list[dict[str, Any]]) -> CatBoostClassifier:
    x = np.asarray([r["x"] for r in train], dtype=float)
    y = np.asarray([r["y"] for r in train], dtype=int)
    sw = np.where(y == 1, DRAW_WEIGHT, 1.0)
    m = CatBoostClassifier(
        loss_function="MultiClass", iterations=v651.ITERATIONS, depth=MODEL_DEPTH,
        learning_rate=v651.LEARNING_RATE, l2_leaf_reg=v651.L2,
        random_seed=6520, verbose=False, allow_writing_files=False, thread_count=-1,
    )
    m.fit(x, y, sample_weight=sw)
    return m


def evaluate(rows: list[dict[str, Any]], model: CatBoostClassifier, adv_threshold: float, margin_cap: float) -> dict[str, Any]:
    x = np.asarray([r["x"] for r in rows], dtype=float)
    y = np.asarray([r["y"] for r in rows], dtype=int)
    market = np.asarray([r["market"] for r in rows], dtype=float)
    p = np.asarray(model.predict_proba(x), dtype=float)
    market_pick = market.argmax(axis=1)
    model_pick = p.argmax(axis=1)
    sorted_market = np.sort(market, axis=1)
    market_margin = sorted_market[:,-1] - sorted_market[:,-2]
    idx = np.arange(len(rows))
    model_adv = p[idx, model_pick] - p[idx, market_pick]
    override = (model_pick != market_pick) & (model_adv >= adv_threshold - 1e-12) & (market_margin <= margin_cap + 1e-12)
    candidate_pick = market_pick.copy(); candidate_pick[override] = model_pick[override]
    market_hits = int(np.sum(market_pick == y)); candidate_hits = int(np.sum(candidate_pick == y))
    wins = int(np.sum(override & (candidate_pick == y) & (market_pick != y)))
    losses = int(np.sum(override & (candidate_pick != y) & (market_pick == y)))
    neutral = int(np.sum(override)) - wins - losses
    by_league = {}
    for cid in sorted({str(r["competition_id"]) for r in rows}):
        mask = np.asarray([str(r["competition_id"]) == cid for r in rows])
        by_league[cid] = {
            "n": int(mask.sum()),
            "market_hits": int(np.sum((market_pick == y) & mask)),
            "candidate_hits": int(np.sum((candidate_pick == y) & mask)),
            "overrides": int(np.sum(override & mask)),
        }
    return {
        "count": len(rows), "market_hits": market_hits, "candidate_hits": candidate_hits,
        "market_top1": market_hits/len(rows), "candidate_top1": candidate_hits/len(rows),
        "uplift_pp": 100.0*(candidate_hits-market_hits)/len(rows),
        "overrides": int(override.sum()), "override_wins": wins, "override_losses": losses,
        "override_neutral": neutral, "net_override_gain": wins-losses,
        "predicted_counts": dict(Counter(str(int(x)) for x in candidate_pick)),
        "actual_counts": dict(Counter(str(int(x)) for x in y)),
        "by_league": by_league,
        "proper_scores": "identical_to_market_by_construction",
    }


def load_a100_labels() -> np.ndarray:
    labels = []
    with v651.LABELS.open("r", encoding="utf-8") as h:
        for _ in range(100):
            r = json.loads(h.readline())
            if r.get("partition") != v651.PART or int(r["full_index"]) != len(labels):
                raise RuntimeError("A100 label contract changed")
            labels.append(int(r["label"]))
    return np.asarray(labels, dtype=int)


def eval_a100(arows: list[dict[str, Any]], y: np.ndarray, model: CatBoostClassifier, adv: float, cap: float) -> dict[str, Any]:
    rows = []
    for r, yy in zip(arows, y):
        z = dict(r); z["y"] = int(yy); rows.append(z)
    return evaluate(rows, model, adv, cap)


def main() -> int:
    hist, hist_audit = v651.build_historical()
    train1 = [r for r in hist if r["season"] == "2022/23"]
    sel = [r for r in hist if r["season"] == "2023/24"]
    model1 = fit_model(train1)
    board = []
    for adv in ADV_THRESHOLDS:
        for cap in MARKET_MARGIN_CAPS:
            met = evaluate(sel, model1, adv, cap)
            board.append({"adv_threshold": adv, "market_margin_cap": cap, "selection": met})
    eligible = [x for x in board if x["selection"]["overrides"] >= MIN_SELECTION_OVERRIDES]
    if not eligible:
        raise RuntimeError("no V6.52 selection rule has minimum override support")
    eligible.sort(key=lambda x: (x["selection"]["net_override_gain"], x["selection"]["uplift_pp"], -x["selection"]["overrides"], x["adv_threshold"], -x["market_margin_cap"]), reverse=True)
    chosen = eligible[0]

    train2 = [r for r in hist if r["season"] in {"2022/23","2023/24"}]
    hold = [r for r in hist if r["season"] == "2024/25"]
    model2 = fit_model(train2)
    holdout = evaluate(hold, model2, float(chosen["adv_threshold"]), float(chosen["market_margin_cap"]))
    historical_gate = bool(
        chosen["selection"]["uplift_pp"] > 0.0 and
        holdout["uplift_pp"] >= HOLDOUT_REQUIRED_UPLIFT_PP - 1e-12 and
        holdout["overrides"] >= MIN_HOLDOUT_OVERRIDES
    )

    payload: dict[str, Any] = {
        "schema_version": "V6.52.0-selective-rich-override-full500-r1", "status": "PASS",
        "formal_current_version": "V5.0.1", "formal_weight": 0,
        "governance": {
            "model_spec_fixed_before_selection": True,
            "rule_selected_only_on_2023_24": True,
            "holdout_2024_25_untouched_until_rule_fixed": True,
            "A100_values_used_for_rule_selection": False,
            "probability_vector_modified": False,
            "B_CONFIRM300_labels_read": False, "C_SEALED100_labels_read": False,
            "CURRENT_unchanged": True,
        },
        "historical_audit": hist_audit,
        "model": {"depth": MODEL_DEPTH, "draw_weight": DRAW_WEIGHT, "iterations": v651.ITERATIONS, "learning_rate": v651.LEARNING_RATE, "l2": v651.L2},
        "rule_grid": {"adv_thresholds": ADV_THRESHOLDS, "market_margin_caps": MARKET_MARGIN_CAPS, "minimum_selection_overrides": MIN_SELECTION_OVERRIDES, "minimum_holdout_overrides": MIN_HOLDOUT_OVERRIDES},
        "selected_rule": chosen,
        "holdout_2024_25": holdout,
        "historical_gate": historical_gate,
        "selection_leaderboard_top10": eligible[:10],
    }
    if not historical_gate:
        payload["A_FAST100"] = {"status": "NOT_OPENED_HISTORICAL_HOLDOUT_GATE_FAILED"}
        payload["next_step"] = "DO_NOT_OPEN_B300"
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2)); return 0

    arows, aaudit = v651.load_a100_features(); y = load_a100_labels()
    model_all = fit_model(hist)
    amet = eval_a100(arows, y, model_all, float(chosen["adv_threshold"]), float(chosen["market_margin_cap"]))
    a_gate = {
        "required_candidate_hits": 63, "required_uplift_vs_market_pp": 3.0,
        "candidate_hits": amet["candidate_hits"], "market_hits": amet["market_hits"],
        "uplift_vs_market_pp": amet["uplift_pp"],
        "top1_gate": amet["candidate_hits"] >= 63,
        "uplift_gate": amet["uplift_pp"] >= 3.0 - 1e-12,
        "proper_score_guard": True,
    }
    a_gate["A_FAST100_passed"] = bool(a_gate["top1_gate"] and a_gate["uplift_gate"])
    payload["A_FAST100"] = {"status": "SCORED_AFTER_HISTORICAL_HOLDOUT_GATE", "feature_audit": aaudit, "metrics": amet, "gate": a_gate}
    payload["next_step"] = "OPEN_B_CONFIRM300" if a_gate["A_FAST100_passed"] else "DO_NOT_OPEN_B300"
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
