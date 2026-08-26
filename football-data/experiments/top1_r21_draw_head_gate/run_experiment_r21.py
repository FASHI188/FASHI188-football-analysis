#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
R19_DIR = HERE.parent / "top1_r19_compact_two_stage_draw"
sys.path.insert(0, str(R19_DIR))
import run_experiment_r19 as r19  # noqa: E402

r14 = r19.r14
r12 = r19.r12
r9 = r19.r9

THRESHOLDS = [0.00, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10, 0.12, 0.15, 0.20]


def fit_models(pred, b1, b2):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    train = pred[b1:b2]
    k1 = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=3000, random_state=0))
    d1 = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=3000, random_state=0))
    k1.fit([r9.feat_k1(r["raw"]) for r in train], [r["y"] for r in train])
    d1.fit([r19.binary_draw_features(r) for r in train], [int(r["y"] == 1) for r in train])
    return k1, d1


def decorate_subset(rows, k1, d1):
    p1s = r19.decorate_k1(k1, rows)
    classes = list(d1[-1].classes_)
    j = classes.index(1)
    pds = d1.predict_proba([r19.binary_draw_features(r) for r in rows])[:, j]
    out = []
    for src, p1, pd in zip(rows, p1s, pds):
        j2 = r19.recompose(p1, pd)
        out.append({
            "date": src["date"],
            "y": src["y"],
            "K1": p1,
            "J2": j2,
            "draw_edge": float(j2["p_draw"] - max(j2["p_home"], j2["p_away"])),
        })
    return out


def decision(rows, threshold):
    recs = []
    switched = switch_hits = switch_losses = 0
    for r in rows:
        k = int(r["K1"]["top1"])
        g = k
        if threshold is not None and k != 1 and int(r["J2"]["top1"]) == 1 and r["draw_edge"] >= threshold:
            g = 1
            switched += 1
            switch_hits += int(r["y"] == 1)
            switch_losses += int(k == r["y"] and r["y"] != 1)
        recs.append({"y": r["y"], "top1": g})
    hits = sum(int(z["top1"] == z["y"]) for z in recs)
    picks = [0, 0, 0]; phits = [0, 0, 0]
    for z in recs:
        picks[z["top1"]] += 1
        phits[z["top1"]] += int(z["top1"] == z["y"])
    return {
        "threshold": threshold,
        "count": len(rows),
        "hits": hits,
        "top1_accuracy": hits / len(rows),
        "top1_picks": {"home": picks[0], "draw": picks[1], "away": picks[2]},
        "top1_hits": {"home": phits[0], "draw": phits[1], "away": phits[2]},
        "switches_to_draw": switched,
        "switch_draw_hits": switch_hits,
        "switch_displaced_correct_non_draw": switch_losses,
        "switch_net": switch_hits - switch_losses,
    }


def choose(validation):
    candidates = [decision(validation, None)] + [decision(validation, x) for x in THRESHOLDS]
    # Validation-only selection. Tie-break: prefer fewer interventions, then higher threshold.
    def rank(x):
        disabled = 1 if x["threshold"] is None else 0
        thr = 999.0 if x["threshold"] is None else float(x["threshold"])
        return (x["hits"], -x["switches_to_draw"], disabled, thr)
    selected = max(candidates, key=rank)
    return selected, candidates


def run():
    pred = r19.build_history()
    b1 = r9.boundary(pred, r9.TARGET_BURN)
    b2 = r9.boundary(pred, b1 + r9.TARGET_TRAIN)
    b3 = r9.boundary(pred, b2 + r9.TARGET_VAL)
    val0, test0 = pred[b2:b3], pred[b3:]
    k1, d1 = fit_models(pred, b1, b2)
    val = decorate_subset(val0, k1, d1)
    test = decorate_subset(test0, k1, d1)

    k1v = r9.metrics(val, "K1"); k1t = r9.metrics(test, "K1")
    if k1v["hits"] != 2064 or k1t["hits"] != 1877:
        raise RuntimeError("R21 K1 reproduction gate failed")
    selected, scan = choose(val)
    threshold = selected["threshold"]
    test_gate = decision(test, threshold)
    val_gain = selected["hits"] - k1v["hits"]
    test_gain = test_gate["hits"] - k1t["hits"]

    summary = {
        "schema_version": "football3-top1-r21-draw-head-gate",
        "status": "COMPLETE",
        "classification": "DEVELOPMENT_VALIDATION_SELECTED_DECISION_GATE",
        "formal_weight": 0,
        "governance": {
            "snapshot_rows": 20000,
            "strict_prior_features": True,
            "same_date_results_and_xg_withheld": True,
            "odds_used": False,
            "market_prices_used": False,
            "manual_probability_adjustment": False,
            "threshold_selected_on_validation_only": True,
            "test_used_for_threshold_selection": False,
            "fresh_cohorts_used_for_threshold_selection": False,
            "threshold_grid_predeclared_in_code": THRESHOLDS,
            "disabled_gate_included_as_validation_control": True,
            "formal_promotion_allowed_from_this_run": False,
        },
        "model": {
            "K1": "R9b baseline",
            "J2": "R19 dedicated binary draw head; K1 H/A odds ratio preserved",
            "G2": "K1 top1 by default; switch only non-draw K1 pick to draw when J2 draw is top1 and draw_edge exceeds validation-selected threshold",
        },
        "selected_threshold": threshold,
        "gate_enabled": threshold is not None,
        "validation": {
            "K1": k1v,
            "G2": selected,
            "gain_hits": val_gain,
            "gain_top1_pp": 100 * (selected["top1_accuracy"] - k1v["top1_accuracy"]),
            "threshold_scan": scan,
        },
        "test": {
            "K1": k1t,
            "G2": test_gate,
            "gain_hits": test_gain,
            "gain_top1_pp": 100 * (test_gate["top1_accuracy"] - k1t["top1_accuracy"]),
        },
        "interpretation_flags": {
            "positive_validation_gain": val_gain > 0,
            "positive_test_gain": test_gain > 0,
            "candidate_may_be_frozen_for_future_forward_confirmation": val_gain > 0,
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_r21.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def verify():
    s = json.loads((OUT / "summary_r21.json").read_text(encoding="utf-8"))
    g = s["governance"]
    assert g["strict_prior_features"] and g["same_date_results_and_xg_withheld"]
    assert not g["odds_used"] and not g["market_prices_used"]
    assert g["threshold_selected_on_validation_only"] and not g["test_used_for_threshold_selection"]
    assert g["disabled_gate_included_as_validation_control"]
    assert not g["formal_promotion_allowed_from_this_run"]
    assert s["validation"]["K1"]["hits"] == 2064 and s["test"]["K1"]["hits"] == 1877
    print("R21_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_experiment_r21.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
