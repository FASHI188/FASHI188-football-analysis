#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results"
R14_DIR = HERE.parent / "top1_r14_compact_draw"
sys.path.insert(0, str(R14_DIR))
import run_experiment_r14 as r14  # noqa: E402

r12 = r14.r12
r9 = r14.r9
THRESHOLDS = [None, 0.0, 0.005, 0.01, 0.02, 0.03, 0.05, 0.08, 0.12]


def decorate_probs(model, X):
    pr = model.predict_proba(X)
    classes = list(model[-1].classes_)
    out = []
    for row in pr:
        v = np.zeros(3, dtype=float)
        for cls, prob in zip(classes, row):
            v[int(cls)] = float(prob)
        v = np.clip(v, 1e-12, None)
        v /= v.sum()
        out.append(r9.decorate(v))
    return out


def build():
    DATA.mkdir(parents=True, exist_ok=True)
    r12.freeze_gate()
    src = json.loads((r12.DATA / "source_manifest_r12.json").read_text(encoding="utf-8"))
    (DATA / "source_manifest_r15.json").write_text(json.dumps({
        "schema_version": "football3-top1-r15-draw-activation-gate",
        "status": "FROZEN_FROM_EXACT_R9B_SNAPSHOT",
        "classification": "DEVELOPMENT_OVERLAPPING_ERA_NOT_FRESH_CONFIRMATION",
        "formal_weight": 0,
        "r9b_hashes": src["observed_hashes"],
        "snapshot_rows": 20000,
        "same_date_results_and_xg_withheld": True,
        "odds_used": False,
        "market_prices_used": False,
        "probability_model": "K1 baseline plus R14 K3 compact challenger used only as draw evidence",
        "decision_policy": "K1 Top1 unless K3 draw edge passes validation-selected threshold",
        "threshold_grid": ["OFF" if x is None else x for x in THRESHOLDS],
        "threshold_selected_on_validation_only": True,
        "test_used_for_threshold_selection": False,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    rows = r9.load()
    base_state = r9.S()
    draw_state = r12.DrawState()
    pred = []
    by = defaultdict(list)
    for row in rows:
        by[row["date"]].append(row)
    for ds in sorted(by):
        pending = []
        for row in sorted(by[ds], key=lambda x: x["game_id"]):
            raw = base_state.pred(row)
            df = draw_state.features(row, raw)
            pred.append({"date": ds, "y": r9.actual(row), "raw": raw, "draw_features": df})
            pending.append((row, raw))
        for row, raw in pending:
            base_state.update(row, raw)
            draw_state.update(row)

    b1 = r9.boundary(pred, r9.TARGET_BURN)
    b2 = r9.boundary(pred, b1 + r9.TARGET_TRAIN)
    b3 = r9.boundary(pred, b2 + r9.TARGET_VAL)
    train, val, test = pred[b1:b2], pred[b2:b3], pred[b3:]

    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    y = [r["y"] for r in train]
    k1 = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=3000, random_state=0))
    k3 = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=3000, random_state=0))
    k1.fit([r9.feat_k1(r["raw"]) for r in train], y)
    k3.fit([r9.feat_k1(r["raw"]) + r14.compact(r["draw_features"]) for r in train], y)

    for subset in (val, test):
        p1 = decorate_probs(k1, [r9.feat_k1(r["raw"]) for r in subset])
        p3 = decorate_probs(k3, [r9.feat_k1(r["raw"]) + r14.compact(r["draw_features"]) for r in subset])
        for rec, a, b in zip(subset, p1, p3):
            rec["K1"] = a
            rec["K3"] = b
    return b1, train, val, test


def policy(rows, threshold):
    hits = 0
    picks = [0, 0, 0]
    hit_by = [0, 0, 0]
    activations = activation_hits = 0
    changed_from_correct = changed_from_wrong = 0
    for r in rows:
        base = int(r["K1"]["top1"])
        final = base
        k3 = r["K3"]
        edge = k3["p_draw"] - max(k3["p_home"], k3["p_away"])
        activate = threshold is not None and base != 1 and k3["top1"] == 1 and edge >= threshold
        if activate:
            activations += 1
            if r["y"] == 1:
                activation_hits += 1
            if base == r["y"]:
                changed_from_correct += 1
            else:
                changed_from_wrong += 1
            final = 1
        hits += int(final == r["y"])
        picks[final] += 1
        hit_by[final] += int(final == r["y"])
    return {
        "count": len(rows),
        "hits": hits,
        "top1_accuracy": hits / len(rows),
        "top1_picks": {"home": picks[0], "draw": picks[1], "away": picks[2]},
        "top1_hits": {"home": hit_by[0], "draw": hit_by[1], "away": hit_by[2]},
        "activations": activations,
        "activation_hits": activation_hits,
        "activation_precision": activation_hits / activations if activations else 0.0,
        "changed_from_correct": changed_from_correct,
        "changed_from_wrong": changed_from_wrong,
        "net_hit_change_vs_K1": changed_from_wrong - changed_from_correct,
    }


def run():
    b1, train, val, test = build()
    k1v = r9.metrics(val, "K1")
    k1t = r9.metrics(test, "K1")
    k3v = r9.metrics(val, "K3")
    k3t = r9.metrics(test, "K3")
    if k1v["hits"] != 2064 or k1t["hits"] != 1877 or k3v["hits"] != 2055 or k3t["hits"] != 1891:
        raise RuntimeError("R15 reproduction gate failed")

    grid = []
    for t in THRESHOLDS:
        m = policy(val, t)
        grid.append({"threshold": "OFF" if t is None else t, **m})

    # Max validation hits; ties prefer fewer interventions, then the more conservative threshold.
    def rank(x):
        t = x["threshold"]
        numeric = 1e9 if t == "OFF" else float(t)
        return (x["hits"], -x["activations"], numeric)

    selected_row = max(grid, key=rank)
    selected = None if selected_row["threshold"] == "OFF" else float(selected_row["threshold"])
    vt = policy(val, selected)
    tt = policy(test, selected)

    summary = {
        "schema_version": "football3-top1-r15-draw-activation-gate",
        "status": "COMPLETE",
        "classification": "DEVELOPMENT_OVERLAPPING_ERA_NOT_FRESH_CONFIRMATION",
        "formal_weight": 0,
        "governance": {
            "snapshot_rows": 20000,
            "burn_in": b1,
            "train": len(train),
            "validation": len(val),
            "test": len(test),
            "coverage_required": 1.0,
            "same_date_results_and_xg_withheld": True,
            "odds_used": False,
            "market_prices_used": False,
            "manual_match_adjustment": False,
            "decision_policy_not_probability_recalibration": True,
            "threshold_selected_on_validation_only": True,
            "test_used_for_threshold_selection": False,
            "formal_promotion_allowed_from_this_run": False,
        },
        "models": {
            "K1": "R9b baseline probability model and default Top1",
            "K3": "R14 compact draw-evidence model",
            "G1": "R15 decision gate: preserve K1 except validated K3 draw activation",
        },
        "threshold_grid_validation": grid,
        "selected_threshold": "OFF" if selected is None else selected,
        "validation": {
            "K1": k1v,
            "K3": k3v,
            "G1": vt,
            "delta_G1_minus_K1_top1_pp": (vt["top1_accuracy"] - k1v["top1_accuracy"]) * 100,
        },
        "test": {
            "K1": k1t,
            "K3": k3t,
            "G1": tt,
            "delta_G1_minus_K1_top1_pp": (tt["top1_accuracy"] - k1t["top1_accuracy"]) * 100,
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_r15.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


def verify():
    s = json.loads((OUT / "summary_r15.json").read_text(encoding="utf-8"))
    g = s["governance"]
    assert g["coverage_required"] == 1.0
    assert g["same_date_results_and_xg_withheld"]
    assert not g["odds_used"] and not g["market_prices_used"]
    assert g["decision_policy_not_probability_recalibration"]
    assert g["threshold_selected_on_validation_only"] and not g["test_used_for_threshold_selection"]
    assert not g["formal_promotion_allowed_from_this_run"]
    assert len(s["threshold_grid_validation"]) == len(THRESHOLDS)
    print("R15_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_experiment_r15.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
