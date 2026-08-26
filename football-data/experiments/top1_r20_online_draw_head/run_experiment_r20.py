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

WINDOW = 8041
BLOCK = 256
C = 0.5


def new_binary_model():
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return make_pipeline(StandardScaler(), LogisticRegression(C=C, max_iter=3000, random_state=0))


def new_k1_model():
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=3000, random_state=0))


def pdraw(model, rows):
    classes = list(model[-1].classes_)
    j = classes.index(1)
    return model.predict_proba([r19.binary_draw_features(r) for r in rows])[:, j]


def score_block(pred, start, end, k1, d_static, d_online):
    rows = pred[start:end]
    p1s = r19.decorate_k1(k1, rows)
    pds = pdraw(d_static, rows)
    pdo = pdraw(d_online, rows)
    out = []
    for src, p1, ps, po in zip(rows, p1s, pds, pdo):
        out.append({
            "date": src["date"],
            "y": src["y"],
            "K1": p1,
            "J2_STATIC": r19.recompose(p1, ps),
            "J2_ONLINE": r19.recompose(p1, po),
        })
    return out


def metrics_bundle(rows):
    return {
        "K1": r9.metrics(rows, "K1"),
        "J2_STATIC": r9.metrics(rows, "J2_STATIC"),
        "J2_ONLINE": r9.metrics(rows, "J2_ONLINE"),
        "delta_online_minus_K1": r12.delta(r9.metrics(rows, "J2_ONLINE"), r9.metrics(rows, "K1")),
        "delta_online_minus_static": r12.delta(r9.metrics(rows, "J2_ONLINE"), r9.metrics(rows, "J2_STATIC")),
        "paired_online_vs_K1": r19.paired(rows, "K1", "J2_ONLINE"),
        "draw_diag_K1": r19.draw_diag(rows, "K1"),
        "draw_diag_static": r19.draw_diag(rows, "J2_STATIC"),
        "draw_diag_online": r19.draw_diag(rows, "J2_ONLINE"),
    }


def segment(pred, start, end, label, k1, d_static):
    cursor = start
    scored = []
    blocks = []
    while cursor < end:
        target = min(end, cursor + BLOCK)
        nxt = end if target == end else r9.boundary(pred, target)
        if nxt <= cursor:
            raise RuntimeError(f"non-advancing block boundary {label} cursor={cursor} target={target} nxt={nxt}")

        w0 = max(0, cursor - WINDOW)
        train = pred[w0:cursor]
        # Cursor is always a date boundary, so every training label is strictly before block dates.
        if not train or train[-1]["date"] >= pred[cursor]["date"]:
            raise RuntimeError(f"online chronology gate failed {label} cursor={cursor}")
        d_online = new_binary_model()
        d_online.fit([r19.binary_draw_features(r) for r in train], [int(r["y"] == 1) for r in train])
        z = score_block(pred, cursor, nxt, k1, d_static, d_online)
        scored.extend(z)
        bm = metrics_bundle(z)
        blocks.append({
            "start_index": cursor,
            "end_index": nxt,
            "train_start_index": w0,
            "train_count": len(train),
            "first_date": z[0]["date"],
            "last_date": z[-1]["date"],
            "count": len(z),
            "K1_hits": bm["K1"]["hits"],
            "STATIC_hits": bm["J2_STATIC"]["hits"],
            "ONLINE_hits": bm["J2_ONLINE"]["hits"],
            "ONLINE_draw_picks": bm["J2_ONLINE"]["top1_picks"]["draw"],
            "ONLINE_draw_hits": bm["J2_ONLINE"]["top1_hits"]["draw"],
            "delta_online_top1_pp": bm["delta_online_minus_K1"]["top1_pp"],
        })
        cursor = nxt
    return scored, blocks


def run():
    pred = r19.build_history()
    b1 = r9.boundary(pred, r9.TARGET_BURN)
    b2 = r9.boundary(pred, b1 + r9.TARGET_TRAIN)
    b3 = r9.boundary(pred, b2 + r9.TARGET_VAL)
    train = pred[b1:b2]

    k1 = new_k1_model()
    k1.fit([r9.feat_k1(r["raw"]) for r in train], [r["y"] for r in train])
    d_static = new_binary_model()
    d_static.fit([r19.binary_draw_features(r) for r in train], [int(r["y"] == 1) for r in train])

    val_rows, val_blocks = segment(pred, b2, b3, "validation", k1, d_static)
    test_rows, test_blocks = segment(pred, b3, len(pred), "test", k1, d_static)
    all_rows = val_rows + test_rows

    v = metrics_bundle(val_rows)
    t = metrics_bundle(test_rows)
    a = metrics_bundle(all_rows)
    if v["K1"]["hits"] != 2064 or t["K1"]["hits"] != 1877:
        raise RuntimeError("R20 K1 reproduction gate failed")
    if v["J2_STATIC"]["hits"] != 2063 or t["J2_STATIC"]["hits"] != 1891:
        raise RuntimeError("R20 R19-static reproduction gate failed")

    summary = {
        "schema_version": "football3-top1-r20-online-draw-head",
        "status": "COMPLETE",
        "classification": "STRICT_ROLLING_OUT_OF_SAMPLE_DIAGNOSTIC",
        "formal_weight": 0,
        "governance": {
            "snapshot_rows": 20000,
            "burn_in": b1,
            "original_train": len(train),
            "validation": len(val_rows),
            "test": len(test_rows),
            "rolling_window_matches": WINDOW,
            "retrain_block_target_matches": BLOCK,
            "block_boundaries_date_safe": True,
            "same_date_labels_withheld": True,
            "only_prior_labels_used_for_each_online_refit": True,
            "strict_prior_features": True,
            "odds_used": False,
            "market_prices_used": False,
            "manual_probability_adjustment": False,
            "hyperparameter_search_used": False,
            "test_used_for_parameter_selection": False,
            "fresh_cohorts_used_for_parameter_selection": False,
            "formal_promotion_allowed_from_this_run": False,
        },
        "models": {
            "K1": "R9b frozen classifier on original train",
            "J2_STATIC": "R19 frozen binary draw head",
            "J2_ONLINE": f"same R19 binary draw head refit every ~{BLOCK} matches on immediately prior {WINDOW} matches; K1 H/A ratio preserved",
        },
        "validation": v,
        "test": t,
        "tail_combined": a,
        "validation_blocks": val_blocks,
        "test_blocks": test_blocks,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_r20.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def verify():
    s = json.loads((OUT / "summary_r20.json").read_text(encoding="utf-8"))
    g = s["governance"]
    assert g["rolling_window_matches"] == WINDOW and g["retrain_block_target_matches"] == BLOCK
    assert g["block_boundaries_date_safe"] and g["same_date_labels_withheld"]
    assert g["only_prior_labels_used_for_each_online_refit"] and g["strict_prior_features"]
    assert not g["odds_used"] and not g["market_prices_used"]
    assert not g["hyperparameter_search_used"] and not g["test_used_for_parameter_selection"]
    assert not g["formal_promotion_allowed_from_this_run"]
    assert s["validation"]["K1"]["hits"] == 2064 and s["test"]["K1"]["hits"] == 1877
    print("R20_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_experiment_r20.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
