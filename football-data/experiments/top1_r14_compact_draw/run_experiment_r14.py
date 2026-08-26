#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results"
R12_DIR = HERE.parent / "top1_r12_draw_features"
sys.path.insert(0, str(R12_DIR))
import run_experiment_r12 as r12  # noqa: E402

r9 = r12.r9

COMPACT_NAMES = [
    "competition_draw_rate",
    "team_draw_mean",
    "recent_draw_mean",
    "home_venue_draw_rate",
    "away_venue_draw_rate",
    "competition_low2_rate",
    "team_low2_mean",
    "xg_low_draw_mass",
    "xg_parity",
]
COMPACT_INDEX = [r12.FEATURE_NAMES.index(x) for x in COMPACT_NAMES]


def compact(draw_features):
    return [draw_features[i] for i in COMPACT_INDEX]


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


def paired(rows, a, b):
    gain = loss = 0
    for r in rows:
        ca = r[a]["top1"] == r["y"]
        cb = r[b]["top1"] == r["y"]
        gain += int(cb and not ca)
        loss += int(ca and not cb)
    return {"challenger_gain": gain, "challenger_loss": loss, "net_hits": gain - loss}


def run():
    DATA.mkdir(parents=True, exist_ok=True)
    r12.freeze_gate()
    src = json.loads((R12_DIR / "data" / "source_manifest_r12.json").read_text(encoding="utf-8"))
    (DATA / "source_manifest_r14.json").write_text(json.dumps({
        "schema_version": "football3-top1-r14-compact-draw-features",
        "status": "FROZEN_FROM_EXACT_R9B_SNAPSHOT",
        "classification": "DEVELOPMENT_OVERLAPPING_ERA_NOT_FRESH_CONFIRMATION",
        "formal_weight": 0,
        "r9b_hashes": src["observed_hashes"],
        "snapshot_rows": 20000,
        "strict_prior": True,
        "same_date_results_and_xg_withheld": True,
        "odds_used": False,
        "market_prices_used": False,
        "compact_features": COMPACT_NAMES,
        "hyperparameter_search_used": False,
        "regularization_C": 0.5,
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
    k2 = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=3000, random_state=0))
    k3 = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=3000, random_state=0))
    k1.fit([r9.feat_k1(r["raw"]) for r in train], y)
    k2.fit([r9.feat_k1(r["raw"]) + r["draw_features"] for r in train], y)
    k3.fit([r9.feat_k1(r["raw"]) + compact(r["draw_features"]) for r in train], y)

    for subset in (val, test):
        pp1 = decorate_probs(k1, [r9.feat_k1(r["raw"]) for r in subset])
        pp2 = decorate_probs(k2, [r9.feat_k1(r["raw"]) + r["draw_features"] for r in subset])
        pp3 = decorate_probs(k3, [r9.feat_k1(r["raw"]) + compact(r["draw_features"]) for r in subset])
        for rec, p1, p2, p3 in zip(subset, pp1, pp2, pp3):
            rec["K1"] = p1
            rec["K2"] = p2
            rec["K3"] = p3

    v1, v2, v3 = r9.metrics(val, "K1"), r9.metrics(val, "K2"), r9.metrics(val, "K3")
    t1, t2, t3 = r9.metrics(test, "K1"), r9.metrics(test, "K2"), r9.metrics(test, "K3")
    if v1["hits"] != 2064 or t1["hits"] != 1877 or v2["hits"] != 2053 or t2["hits"] != 1894:
        raise RuntimeError("R14 reproduction gate failed")

    summary = {
        "schema_version": "football3-top1-r14-compact-draw-features",
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
            "strict_prior_features": True,
            "odds_used": False,
            "market_prices_used": False,
            "manual_probability_adjustment": False,
            "hyperparameter_search_used": False,
            "compact_feature_set_chosen_from_mechanism_and_R12_validation_failure": True,
            "test_used_for_compact_feature_selection": False,
            "formal_promotion_allowed_from_this_run": False,
        },
        "models": {
            "K1": "R9b baseline",
            "K2": "R12 27-feature reference",
            "K3": "R14 compact 9-feature strict-prior draw challenger",
        },
        "compact_features": COMPACT_NAMES,
        "validation": {
            "K1": v1,
            "K2": v2,
            "K3": v3,
            "delta_K3_minus_K1": r12.delta(v3, v1),
            "delta_K3_minus_K2": r12.delta(v3, v2),
            "paired_K3_vs_K1": paired(val, "K1", "K3"),
            "draw_diag_K1": r12.draw_diag(val, "K1"),
            "draw_diag_K3": r12.draw_diag(val, "K3"),
        },
        "test": {
            "K1": t1,
            "K2": t2,
            "K3": t3,
            "delta_K3_minus_K1": r12.delta(t3, t1),
            "delta_K3_minus_K2": r12.delta(t3, t2),
            "paired_K3_vs_K1": paired(test, "K1", "K3"),
            "draw_diag_K1": r12.draw_diag(test, "K1"),
            "draw_diag_K3": r12.draw_diag(test, "K3"),
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_r14.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


def verify():
    s = json.loads((OUT / "summary_r14.json").read_text(encoding="utf-8"))
    g = s["governance"]
    assert g["coverage_required"] == 1.0
    assert g["same_date_results_and_xg_withheld"] and g["strict_prior_features"]
    assert not g["odds_used"] and not g["market_prices_used"]
    assert not g["hyperparameter_search_used"] and not g["test_used_for_compact_feature_selection"]
    assert not g["formal_promotion_allowed_from_this_run"]
    assert len(s["compact_features"]) == 9
    for split in ("validation", "test"):
        for model in ("K1", "K2", "K3"):
            assert s[split][model]["coverage"] == 1.0
    print("R14_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_experiment_r14.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
