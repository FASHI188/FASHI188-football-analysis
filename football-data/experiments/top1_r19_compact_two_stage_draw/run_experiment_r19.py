#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
R14_DIR = HERE.parent / "top1_r14_compact_draw"
sys.path.insert(0, str(R14_DIR))
import run_experiment_r14 as r14  # noqa: E402

r12 = r14.r12
r9 = r14.r9


def decorate_k1(model, rows):
    return r14.decorate_probs(model, [r9.feat_k1(r["raw"]) for r in rows])


def binary_draw_features(rec):
    # Dedicated draw objective, but only strict-prior features already frozen in R14.
    return r9.feat_k1(rec["raw"]) + r14.compact(rec["draw_features"])


def recompose(p1, p_draw):
    pd = float(np.clip(p_draw, 1e-9, 1 - 1e-9))
    ha = float(p1["p_home"] + p1["p_away"])
    if ha <= 0:
        rh = ra = 0.5
    else:
        rh = float(p1["p_home"] / ha)
        ra = float(p1["p_away"] / ha)
    v = np.asarray([(1 - pd) * rh, pd, (1 - pd) * ra], dtype=float)
    v = np.clip(v, 1e-12, None)
    v /= v.sum()
    return r9.decorate(v)


def paired(rows, a, b):
    gain = loss = 0
    for r in rows:
        ca = r[a]["top1"] == r["y"]
        cb = r[b]["top1"] == r["y"]
        gain += int(cb and not ca)
        loss += int(ca and not cb)
    return {"challenger_gain": gain, "challenger_loss": loss, "net_hits": gain - loss}


def draw_diag(rows, key):
    from sklearn.metrics import average_precision_score, roc_auc_score
    y = np.asarray([int(r["y"] == 1) for r in rows], dtype=int)
    p = np.asarray([float(r[key]["p_draw"]) for r in rows], dtype=float)
    picks = np.asarray([int(r[key]["top1"] == 1) for r in rows], dtype=int)
    hits = int(((y == 1) & (picks == 1)).sum())
    npicks = int(picks.sum())
    ndraw = int(y.sum())
    return {
        "roc_auc": float(roc_auc_score(y, p)),
        "average_precision": float(average_precision_score(y, p)),
        "actual_draws": ndraw,
        "top1_draw_picks": npicks,
        "top1_draw_hits": hits,
        "top1_draw_precision": (hits / npicks) if npicks else None,
        "top1_draw_recall": (hits / ndraw) if ndraw else None,
    }


def build_history():
    r12.freeze_gate()
    rows = r9.load()
    base = r9.S()
    draw = r12.DrawState()
    pred = []
    by = defaultdict(list)
    for row in rows:
        by[row["date"]].append(row)
    for day in sorted(by):
        pending = []
        for row in sorted(by[day], key=lambda z: z["game_id"]):
            raw = base.pred(row)
            df = draw.features(row, raw)
            pred.append({"date": day, "y": r9.actual(row), "raw": raw, "draw_features": df})
            pending.append((row, raw))
        for row, raw in pending:
            base.update(row, raw)
            draw.update(row)
    return pred


def run():
    pred = build_history()
    b1 = r9.boundary(pred, r9.TARGET_BURN)
    b2 = r9.boundary(pred, b1 + r9.TARGET_TRAIN)
    b3 = r9.boundary(pred, b2 + r9.TARGET_VAL)
    train, val, test = pred[b1:b2], pred[b2:b3], pred[b3:]

    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    y3 = [r["y"] for r in train]
    yd = [int(r["y"] == 1) for r in train]

    k1 = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=3000, random_state=0))
    d1 = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=3000, random_state=0))
    k1.fit([r9.feat_k1(r["raw"]) for r in train], y3)
    d1.fit([binary_draw_features(r) for r in train], yd)

    for subset in (val, test):
        p1s = decorate_k1(k1, subset)
        pds = d1.predict_proba([binary_draw_features(r) for r in subset])[:, list(d1[-1].classes_).index(1)]
        for rec, p1, pd in zip(subset, p1s, pds):
            rec["K1"] = p1
            rec["J2"] = recompose(p1, pd)

    v1, vj = r9.metrics(val, "K1"), r9.metrics(val, "J2")
    t1, tj = r9.metrics(test, "K1"), r9.metrics(test, "J2")
    if v1["hits"] != 2064 or t1["hits"] != 1877:
        raise RuntimeError("R19 K1 reproduction gate failed")

    scaler = d1[0]
    clf = d1[-1]
    feature_names = [f"k1_{i}" for i in range(len(r9.feat_k1(train[0]["raw"])))] + list(r14.COMPACT_NAMES)
    coefs = sorted(
        [{"feature": n, "coef_standardized": float(c)} for n, c in zip(feature_names, clf.coef_[0])],
        key=lambda x: abs(x["coef_standardized"]), reverse=True,
    )

    summary = {
        "schema_version": "football3-top1-r19-compact-two-stage-draw",
        "status": "COMPLETE",
        "classification": "DEVELOPMENT_OVERLAPPING_ERA_NOT_FRESH_CONFIRMATION",
        "formal_weight": 0,
        "governance": {
            "snapshot_rows": 20000,
            "burn_in": b1,
            "train": len(train),
            "validation": len(val),
            "test": len(test),
            "strict_prior_features": True,
            "same_date_results_and_xg_withheld": True,
            "odds_used": False,
            "market_prices_used": False,
            "manual_probability_adjustment": False,
            "hyperparameter_search_used": False,
            "binary_draw_regularization_C": 0.5,
            "test_used_for_model_selection": False,
            "fresh_cohorts_used_for_model_selection": False,
            "formal_promotion_allowed_from_this_run": False,
        },
        "model": {
            "K1": "R9b 3-way baseline",
            "J2": "dedicated binary draw head on K1 strict-prior features + R14 compact 9; K1 home/away odds ratio preserved exactly",
            "compact_features": list(r14.COMPACT_NAMES),
        },
        "validation": {
            "K1": v1,
            "J2": vj,
            "delta_J2_minus_K1": r12.delta(vj, v1),
            "paired_J2_vs_K1": paired(val, "K1", "J2"),
            "draw_diag_K1": draw_diag(val, "K1"),
            "draw_diag_J2": draw_diag(val, "J2"),
        },
        "test": {
            "K1": t1,
            "J2": tj,
            "delta_J2_minus_K1": r12.delta(tj, t1),
            "paired_J2_vs_K1": paired(test, "K1", "J2"),
            "draw_diag_K1": draw_diag(test, "K1"),
            "draw_diag_J2": draw_diag(test, "J2"),
        },
        "binary_draw_head": {
            "intercept": float(clf.intercept_[0]),
            "standardizer_scale_min": float(np.min(scaler.scale_)),
            "top_coefficients": coefs[:12],
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_r19.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def verify():
    s = json.loads((OUT / "summary_r19.json").read_text(encoding="utf-8"))
    g = s["governance"]
    assert g["snapshot_rows"] == 20000
    assert g["strict_prior_features"] and g["same_date_results_and_xg_withheld"]
    assert not g["odds_used"] and not g["market_prices_used"]
    assert not g["hyperparameter_search_used"] and not g["test_used_for_model_selection"]
    assert not g["fresh_cohorts_used_for_model_selection"]
    assert not g["formal_promotion_allowed_from_this_run"]
    assert s["validation"]["K1"]["hits"] == 2064
    assert s["test"]["K1"]["hits"] == 1877
    print("R19_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_experiment_r19.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
