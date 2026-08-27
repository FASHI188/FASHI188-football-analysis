#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
R21_DIR = HERE.parent / "top1_r21_draw_head_gate"
sys.path.insert(0, str(R21_DIR))
import run_experiment_r21 as r21  # noqa: E402

r19 = r21.r19
r14 = r21.r14
r9 = r21.r9

# Mechanism change from R21/R26: do not model DRAW globally and do not gate it with
# hand-built context. Learn the direct top1 switch utility: when K1 currently picks
# HOME/AWAY, is replacing that pick with DRAW expected to gain a hit, lose a hit,
# or change nothing? Utility training uses chronological out-of-fold K1 predictions.
OOF_BLOCKS = 5
UTILITY_THRESHOLDS = [0.00, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10, 0.12]
MIN_VALIDATION_SWITCHES = 8
MIN_VALIDATION_NET = 3
MIN_POSITIVE_VALIDATION_BLOCKS = 2
MAX_NEGATIVE_VALIDATION_BLOCKS = 1
MIN_TEST_SWITCHES_FOR_BATCH005 = 5
MIN_TEST_NET_FOR_BATCH005 = 1

DERIVED_NAMES = [
    "k1_p_home",
    "k1_p_draw",
    "k1_p_away",
    "k1_chosen_p",
    "draw_minus_chosen",
    "home_away_abs_gap",
    "k1_entropy",
    "k1_chosen_home",
    "k1_chosen_away",
]


def fit_k1(rows):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    m = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=3000, random_state=0))
    m.fit([r9.feat_k1(r["raw"]) for r in rows], [r["y"] for r in rows])
    return m


def date_chunks(rows, n):
    dates = sorted({r["date"] for r in rows})
    return [set(x.tolist()) for x in np.array_split(np.asarray(dates, dtype=object), n)]


def switch_features(rec, p1):
    v = np.asarray([p1["p_home"], p1["p_draw"], p1["p_away"]], dtype=float)
    chosen = int(p1["top1"])
    chosen_p = float(v[chosen])
    entropy = float(-np.sum(np.clip(v, 1e-12, 1.0) * np.log(np.clip(v, 1e-12, 1.0))))
    derived = [
        float(v[0]),
        float(v[1]),
        float(v[2]),
        chosen_p,
        float(v[1] - chosen_p),
        float(abs(v[0] - v[2])),
        entropy,
        float(chosen == 0),
        float(chosen == 2),
    ]
    return list(r9.feat_k1(rec["raw"])) + list(r14.compact(rec["draw_features"])) + derived


def utility_target(rec, p1):
    chosen = int(p1["top1"])
    if chosen == 1:
        raise ValueError("utility_target requires a non-draw K1 pick")
    if int(rec["y"]) == 1:
        return 1  # switching to draw creates a hit
    if int(rec["y"]) == chosen:
        return -1  # switching to draw destroys a hit
    return 0  # both stay and switch are wrong


def build_oof_utility_training(train):
    chunks = date_chunks(train, OOF_BLOCKS)
    X, y, meta = [], [], []
    fold_summaries = []
    for fold in range(1, OOF_BLOCKS):
        prior_dates = set().union(*chunks[:fold])
        target_dates = chunks[fold]
        prior = [r for r in train if r["date"] in prior_dates]
        target = [r for r in train if r["date"] in target_dates]
        k1 = fit_k1(prior)
        p1s = r19.decorate_k1(k1, target)
        added = Counter()
        non_draw = 0
        for rec, p1 in zip(target, p1s):
            if int(p1["top1"]) == 1:
                continue
            non_draw += 1
            t = utility_target(rec, p1)
            X.append(switch_features(rec, p1))
            y.append(t)
            meta.append({"date": rec["date"], "target": t})
            added[t] += 1
        fold_summaries.append({
            "fold": fold,
            "prior_rows": len(prior),
            "target_rows": len(target),
            "non_draw_training_rows": non_draw,
            "utility_targets": {str(k): int(v) for k, v in sorted(added.items())},
        })
    return X, y, meta, fold_summaries


def fit_utility(X, y):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    classes = sorted(set(y))
    if classes != [-1, 0, 1]:
        raise RuntimeError(f"R27 utility classes incomplete: {classes}")
    m = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=3000, random_state=0))
    m.fit(X, y)
    return m


def utility_scores(model, rows, p1s):
    out = []
    classes = list(model[-1].classes_)
    ipos = classes.index(1)
    ineg = classes.index(-1)
    for rec, p1 in zip(rows, p1s):
        if int(p1["top1"]) == 1:
            out.append(None)
            continue
        prob = model.predict_proba([switch_features(rec, p1)])[0]
        out.append(float(prob[ipos] - prob[ineg]))
    return out


def decorate(rows, k1, utility_model):
    p1s = r19.decorate_k1(k1, rows)
    us = utility_scores(utility_model, rows, p1s)
    return [
        {"date": rec["date"], "y": rec["y"], "K1": p1, "utility_score": u}
        for rec, p1, u in zip(rows, p1s, us)
    ]


def make_date_blocks(rows, n=4):
    dates = sorted({r["date"] for r in rows})
    chunks = np.array_split(np.asarray(dates, dtype=object), n)
    out = {}
    for i, chunk in enumerate(chunks):
        for d in chunk.tolist():
            out[d] = i
    return out


def decision(rows, threshold, date_to_block=None):
    base_hits = hits = 0
    switches = draw_hits = displaced = 0
    final_picks = [0, 0, 0]
    final_hits = [0, 0, 0]
    blocks = {}
    if date_to_block is not None:
        for i in sorted(set(date_to_block.values())):
            blocks[str(i)] = {"switches": 0, "draw_hits": 0, "displaced_correct": 0, "net": 0}

    for r in rows:
        base = int(r["K1"]["top1"])
        base_correct = int(base == int(r["y"]))
        base_hits += base_correct
        final = base
        activate = threshold is not None and base != 1 and r["utility_score"] is not None and r["utility_score"] >= threshold
        if activate:
            final = 1
            switches += 1
            is_draw = int(int(r["y"]) == 1)
            lost = int(base_correct and int(r["y"]) != 1)
            draw_hits += is_draw
            displaced += lost
            if date_to_block is not None:
                b = str(date_to_block[r["date"]])
                blocks[b]["switches"] += 1
                blocks[b]["draw_hits"] += is_draw
                blocks[b]["displaced_correct"] += lost
        correct = int(final == int(r["y"]))
        hits += correct
        final_picks[final] += 1
        final_hits[final] += correct

    for b in blocks.values():
        b["net"] = b["draw_hits"] - b["displaced_correct"]
    return {
        "threshold": threshold,
        "count": len(rows),
        "baseline_hits": base_hits,
        "hits": hits,
        "gain_hits": hits - base_hits,
        "top1_accuracy": hits / len(rows),
        "top1_picks": {"home": final_picks[0], "draw": final_picks[1], "away": final_picks[2]},
        "top1_hits": {"home": final_hits[0], "draw": final_hits[1], "away": final_hits[2]},
        "switches_to_draw": switches,
        "switch_draw_hits": draw_hits,
        "switch_displaced_correct_non_draw": displaced,
        "switch_wrong_to_wrong": switches - draw_hits - displaced,
        "switch_net": draw_hits - displaced,
        "switch_draw_precision": draw_hits / switches if switches else 0.0,
        "positive_time_blocks": sum(int(b["net"] > 0) for b in blocks.values()),
        "negative_time_blocks": sum(int(b["net"] < 0) for b in blocks.values()),
        "time_blocks": blocks,
    }


def run():
    pred = r19.build_history()
    b1 = r9.boundary(pred, r9.TARGET_BURN)
    b2 = r9.boundary(pred, b1 + r9.TARGET_TRAIN)
    b3 = r9.boundary(pred, b2 + r9.TARGET_VAL)
    train, val0, test0 = pred[b1:b2], pred[b2:b3], pred[b3:]

    # Frozen full-train K1 for validation/test plus the old R21 binary head solely
    # as a reproduction control. R27's utility model does not consume J2.
    k1, d1 = r21.fit_models(pred, b1, b2)
    val_r21 = r21.decorate_subset(val0, k1, d1)
    k1v = r9.metrics(val_r21, "K1")
    if k1v["hits"] != 2064:
        raise RuntimeError("R27 K1 validation reproduction gate failed")
    old_selected, _ = r21.choose(val_r21)
    if old_selected["threshold"] != 0.08 or old_selected["switches_to_draw"] != 1 or old_selected["switch_net"] != 1:
        raise RuntimeError("R27 R21 validation-control reproduction gate failed")

    X, yu, meta, fold_summaries = build_oof_utility_training(train)
    utility_model = fit_utility(X, yu)
    val = decorate(val0, k1, utility_model)
    val_blocks = make_date_blocks(val, 4)

    candidates = []
    for thr in UTILITY_THRESHOLDS:
        m = decision(val, thr, val_blocks)
        viable = (
            m["switches_to_draw"] >= MIN_VALIDATION_SWITCHES
            and m["switch_net"] >= MIN_VALIDATION_NET
            and m["positive_time_blocks"] >= MIN_POSITIVE_VALIDATION_BLOCKS
            and m["negative_time_blocks"] <= MAX_NEGATIVE_VALIDATION_BLOCKS
        )
        candidates.append({"viable": viable, **m})

    viable = [x for x in candidates if x["viable"]]
    if viable:
        selected = max(
            viable,
            key=lambda x: (
                x["switch_net"],
                x["positive_time_blocks"],
                -x["negative_time_blocks"],
                x["switch_draw_precision"],
                -x["switches_to_draw"],
                x["threshold"],
            ),
        )
        # Historical TEST is evaluated only after the validation rule is frozen.
        test = decorate(test0, k1, utility_model)
        k1t = r9.metrics(test, "K1")
        if k1t["hits"] != 1877:
            raise RuntimeError("R27 K1 test reproduction gate failed")
        test_blocks = make_date_blocks(test, 4)
        test_result = decision(test, selected["threshold"], test_blocks)
        batch005_eligible = (
            test_result["switches_to_draw"] >= MIN_TEST_SWITCHES_FOR_BATCH005
            and test_result["switch_net"] >= MIN_TEST_NET_FOR_BATCH005
        )
        stop_reason = None if batch005_eligible else "FROZEN_DIRECT_UTILITY_RULE_FAILED_HISTORICAL_TEST_CONFIRMATION"
    else:
        selected = None
        test_result = None
        batch005_eligible = False
        stop_reason = "NO_VALIDATION_ROBUST_DIRECT_DRAW_UTILITY"

    scaler = utility_model[0]
    clf = utility_model[-1]
    feature_names = (
        [f"k1_raw_{i}" for i in range(len(r9.feat_k1(train[0]["raw"])))]
        + list(r14.COMPACT_NAMES)
        + DERIVED_NAMES
    )
    coef_rows = []
    for cls, coef in zip(clf.classes_, clf.coef_):
        for name, c in zip(feature_names, coef):
            coef_rows.append({"class": int(cls), "feature": name, "coef_standardized": float(c)})
    coef_rows = sorted(coef_rows, key=lambda z: abs(z["coef_standardized"]), reverse=True)[:20]

    summary = {
        "schema_version": "football3-top1-r27-direct-draw-utility",
        "status": "COMPLETE",
        "classification": "DEVELOPMENT_MECHANISM_CHANGE_BEFORE_BATCH005",
        "formal_weight": 0,
        "governance": {
            "base_commit": "ff63aee321d58ff85fff043249cc6cd1d0e9372b",
            "snapshot_rows": 20000,
            "strict_prior_features": True,
            "same_date_results_and_xg_withheld": True,
            "odds_used": False,
            "market_prices_used": False,
            "utility_training_uses_chronological_oof_k1": True,
            "utility_oof_blocks": OOF_BLOCKS,
            "utility_hyperparameter_search_used": False,
            "candidate_grid_predeclared": True,
            "candidate_selected_on_validation_only": True,
            "test_evaluated_only_after_viable_validation_freeze": True,
            "test_used_for_candidate_selection": False,
            "batch004_used_for_candidate_selection": False,
            "batch005_used": False,
            "formal_promotion_allowed_from_this_run": False,
        },
        "question": "Can a direct expected-hit-utility model learn when DRAW should replace K1's non-draw Top1, instead of estimating DRAW globally or using a hand-built context gate?",
        "mechanism": {
            "baseline": "R9b K1",
            "training_target": {
                "1": "switching K1 non-draw Top1 to DRAW creates a hit",
                "-1": "switching destroys a correct K1 non-draw hit",
                "0": "both K1 stay and DRAW switch are wrong",
            },
            "score": "P(utility=+1) - P(utility=-1)",
            "features": "strict-prior K1 raw features + R14 compact 9 draw features + out-of-fold K1 probability/margin/entropy features",
            "decision": "preserve natural K1 DRAW; for K1 HOME/AWAY only, switch to DRAW when direct utility score exceeds validation-frozen threshold",
        },
        "oof_utility_training": {
            "rows": len(X),
            "target_counts": {str(k): int(v) for k, v in sorted(Counter(yu).items())},
            "folds": fold_summaries,
            "standardizer_scale_min": float(np.min(scaler.scale_)),
            "top_coefficients": coef_rows,
        },
        "selection_contract": {
            "utility_thresholds": UTILITY_THRESHOLDS,
            "min_validation_switches": MIN_VALIDATION_SWITCHES,
            "min_validation_net": MIN_VALIDATION_NET,
            "min_positive_validation_time_blocks": MIN_POSITIVE_VALIDATION_BLOCKS,
            "max_negative_validation_time_blocks": MAX_NEGATIVE_VALIDATION_BLOCKS,
            "min_test_switches_for_batch005": MIN_TEST_SWITCHES_FOR_BATCH005,
            "min_test_net_for_batch005": MIN_TEST_NET_FOR_BATCH005,
        },
        "controls": {
            "K1_validation_hits": k1v["hits"],
            "R21_validation_selected_threshold": old_selected["threshold"],
            "R21_validation_switches": old_selected["switches_to_draw"],
            "R21_validation_net": old_selected["switch_net"],
        },
        "validation_candidates": candidates,
        "selected_direct_utility_rule": selected,
        "historical_test_confirmation": test_result,
        "batch005_decision": {
            "eligible": batch005_eligible,
            "action": "SPEND_BATCH005_ON_FROZEN_DIRECT_UTILITY_RULE" if batch005_eligible else "DO_NOT_SPEND_BATCH005",
            "stop_reason": stop_reason,
        },
    }

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_r27.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def verify():
    s = json.loads((OUT / "summary_r27.json").read_text(encoding="utf-8"))
    g = s["governance"]
    assert g["strict_prior_features"] and g["same_date_results_and_xg_withheld"]
    assert not g["odds_used"] and not g["market_prices_used"]
    assert g["utility_training_uses_chronological_oof_k1"]
    assert not g["utility_hyperparameter_search_used"]
    assert g["candidate_selected_on_validation_only"] and not g["test_used_for_candidate_selection"]
    assert not g["batch004_used_for_candidate_selection"] and not g["batch005_used"]
    assert not g["formal_promotion_allowed_from_this_run"]
    assert s["controls"]["K1_validation_hits"] == 2064
    assert s["controls"]["R21_validation_selected_threshold"] == 0.08
    assert len(s["validation_candidates"]) == len(UTILITY_THRESHOLDS)
    if s["batch005_decision"]["eligible"]:
        assert s["selected_direct_utility_rule"] is not None
        assert s["historical_test_confirmation"]["switches_to_draw"] >= MIN_TEST_SWITCHES_FOR_BATCH005
        assert s["historical_test_confirmation"]["switch_net"] >= MIN_TEST_NET_FOR_BATCH005
    print("R27_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_experiment_r27.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
