#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
R27_DIR = HERE.parent / "top1_r27_direct_draw_utility"
sys.path.insert(0, str(R27_DIR))
import run_experiment_r27 as r27  # noqa: E402

r21 = r27.r21
r19 = r27.r19
r9 = r27.r9

# R28 changes the target, not the underlying football features. R27's three-class
# utility task mixed the actionable DRAW-vs-current-side contest with a third class
# where neither choice could be correct. R28 removes that non-actionable class and
# learns only the conditional duel: if the result is either DRAW or K1's chosen side,
# which one wins? Training K1 probabilities remain chronological OOF.
OOF_BLOCKS = 5
DUEL_THRESHOLDS = [0.34, 0.36, 0.38, 0.40, 0.42, 0.44, 0.46, 0.48, 0.50, 0.52, 0.55, 0.58, 0.60]
MIN_VALIDATION_SWITCHES = 8
MIN_VALIDATION_NET = 3
MIN_POSITIVE_VALIDATION_BLOCKS = 2
MAX_NEGATIVE_VALIDATION_BLOCKS = 1
MIN_TEST_SWITCHES_FOR_BATCH005 = 5
MIN_TEST_NET_FOR_BATCH005 = 1


def build_oof_duel_training(train):
    chunks = r27.date_chunks(train, OOF_BLOCKS)
    X, y = [], []
    folds = []
    for fold in range(1, OOF_BLOCKS):
        prior_dates = set().union(*chunks[:fold])
        target_dates = chunks[fold]
        prior = [r for r in train if r["date"] in prior_dates]
        target = [r for r in train if r["date"] in target_dates]
        k1 = r27.fit_k1(prior)
        p1s = r19.decorate_k1(k1, target)
        counts = Counter()
        non_draw = actionable = excluded_both_wrong = 0
        for rec, p1 in zip(target, p1s):
            chosen = int(p1["top1"])
            if chosen == 1:
                continue
            non_draw += 1
            actual = int(rec["y"])
            if actual not in {1, chosen}:
                excluded_both_wrong += 1
                continue
            target_y = int(actual == 1)
            X.append(r27.switch_features(rec, p1))
            y.append(target_y)
            counts[target_y] += 1
            actionable += 1
        folds.append({
            "fold": fold,
            "prior_rows": len(prior),
            "target_rows": len(target),
            "non_draw_k1_rows": non_draw,
            "actionable_rows": actionable,
            "excluded_both_wrong": excluded_both_wrong,
            "targets": {"stay_chosen_side": int(counts[0]), "draw": int(counts[1])},
        })
    return X, y, folds


def fit_duel(X, y):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    if sorted(set(y)) != [0, 1]:
        raise RuntimeError("R28 binary duel classes incomplete")
    m = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=3000, random_state=0))
    m.fit(X, y)
    return m


def decorate(rows, k1, duel_model):
    p1s = r19.decorate_k1(k1, rows)
    classes = list(duel_model[-1].classes_)
    j = classes.index(1)
    out = []
    for rec, p1 in zip(rows, p1s):
        if int(p1["top1"]) == 1:
            score = None
        else:
            score = float(duel_model.predict_proba([r27.switch_features(rec, p1)])[0][j])
        out.append({"date": rec["date"], "y": rec["y"], "K1": p1, "duel_p_draw": score})
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
        actual = int(r["y"])
        base_correct = int(base == actual)
        base_hits += base_correct
        final = base
        activate = base != 1 and r["duel_p_draw"] is not None and r["duel_p_draw"] >= threshold
        if activate:
            final = 1
            switches += 1
            is_draw = int(actual == 1)
            lost = int(base_correct and actual != 1)
            draw_hits += is_draw
            displaced += lost
            if date_to_block is not None:
                b = str(date_to_block[r["date"]])
                blocks[b]["switches"] += 1
                blocks[b]["draw_hits"] += is_draw
                blocks[b]["displaced_correct"] += lost
        correct = int(final == actual)
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

    # Exact baseline reproduction.
    k1, _ = r21.fit_models(pred, b1, b2)
    val_k1 = [{"date": r["date"], "y": r["y"], "K1": p} for r, p in zip(val0, r19.decorate_k1(k1, val0))]
    k1v = r9.metrics(val_k1, "K1")
    if k1v["hits"] != 2064:
        raise RuntimeError("R28 K1 validation reproduction gate failed")

    r27_summary = json.loads((R27_DIR / "results" / "summary_r27.json").read_text(encoding="utf-8"))
    if r27_summary["batch005_decision"]["eligible"] or r27_summary["selected_direct_utility_rule"] is not None:
        raise RuntimeError("R28 requires frozen R27 failure control")

    X, yd, fold_summaries = build_oof_duel_training(train)
    duel_model = fit_duel(X, yd)
    val = decorate(val0, k1, duel_model)
    val_blocks = r27.make_date_blocks(val, 4)

    candidates = []
    for thr in DUEL_THRESHOLDS:
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
        test = decorate(test0, k1, duel_model)
        k1t = r9.metrics(test, "K1")
        if k1t["hits"] != 1877:
            raise RuntimeError("R28 K1 test reproduction gate failed")
        test_blocks = r27.make_date_blocks(test, 4)
        test_result = decision(test, selected["threshold"], test_blocks)
        batch005_eligible = (
            test_result["switches_to_draw"] >= MIN_TEST_SWITCHES_FOR_BATCH005
            and test_result["switch_net"] >= MIN_TEST_NET_FOR_BATCH005
        )
        stop_reason = None if batch005_eligible else "FROZEN_CONDITIONAL_DUEL_FAILED_HISTORICAL_TEST_CONFIRMATION"
    else:
        selected = None
        test_result = None
        batch005_eligible = False
        stop_reason = "NO_VALIDATION_ROBUST_CONDITIONAL_DRAW_DUEL"

    clf = duel_model[-1]
    scaler = duel_model[0]
    feature_names = (
        [f"k1_raw_{i}" for i in range(len(r9.feat_k1(train[0]["raw"])))]
        + list(r27.r14.COMPACT_NAMES)
        + list(r27.DERIVED_NAMES)
    )
    coefs = sorted(
        [{"feature": n, "coef_standardized": float(c)} for n, c in zip(feature_names, clf.coef_[0])],
        key=lambda z: abs(z["coef_standardized"]), reverse=True,
    )[:20]

    summary = {
        "schema_version": "football3-top1-r28-conditional-draw-duel",
        "status": "COMPLETE",
        "classification": "DEVELOPMENT_TARGET_REFORMULATION_BEFORE_BATCH005",
        "formal_weight": 0,
        "governance": {
            "base_commit": "4f3f190077b23969d0ce525eaaeabdca99665692",
            "snapshot_rows": 20000,
            "strict_prior_features": True,
            "same_date_results_and_xg_withheld": True,
            "odds_used": False,
            "market_prices_used": False,
            "duel_training_uses_chronological_oof_k1": True,
            "duel_oof_blocks": OOF_BLOCKS,
            "hyperparameter_search_used": False,
            "candidate_grid_predeclared": True,
            "candidate_selected_on_validation_only": True,
            "test_evaluated_only_after_viable_validation_freeze": True,
            "test_used_for_candidate_selection": False,
            "batch004_used_for_candidate_selection": False,
            "batch005_used": False,
            "formal_promotion_allowed_from_this_run": False,
        },
        "question": "Does removing R27's non-actionable both-wrong class reveal a stable conditional DRAW-vs-K1-side decision boundary?",
        "mechanism": {
            "baseline": "R9b K1",
            "training_rows": "K1 non-draw OOF rows where actual result is either DRAW or K1's chosen side",
            "excluded_training_rows": "actual is the opposite non-draw side, because both stay and switch would lose",
            "target": "P(DRAW | actual in {DRAW, K1 chosen side})",
            "features": "same strict-prior R27 feature set",
            "decision": "preserve natural K1 DRAW; switch HOME/AWAY to DRAW only above validation-frozen conditional duel probability",
        },
        "oof_duel_training": {
            "rows": len(X),
            "targets": {"stay_chosen_side": int(sum(int(x == 0) for x in yd)), "draw": int(sum(int(x == 1) for x in yd))},
            "folds": fold_summaries,
            "standardizer_scale_min": float(np.min(scaler.scale_)),
            "top_coefficients": coefs,
        },
        "selection_contract": {
            "duel_thresholds": DUEL_THRESHOLDS,
            "min_validation_switches": MIN_VALIDATION_SWITCHES,
            "min_validation_net": MIN_VALIDATION_NET,
            "min_positive_validation_time_blocks": MIN_POSITIVE_VALIDATION_BLOCKS,
            "max_negative_validation_time_blocks": MAX_NEGATIVE_VALIDATION_BLOCKS,
            "min_test_switches_for_batch005": MIN_TEST_SWITCHES_FOR_BATCH005,
            "min_test_net_for_batch005": MIN_TEST_NET_FOR_BATCH005,
        },
        "controls": {
            "K1_validation_hits": k1v["hits"],
            "R27_stop_reason": r27_summary["batch005_decision"]["stop_reason"],
        },
        "validation_candidates": candidates,
        "selected_conditional_duel_rule": selected,
        "historical_test_confirmation": test_result,
        "batch005_decision": {
            "eligible": batch005_eligible,
            "action": "SPEND_BATCH005_ON_FROZEN_CONDITIONAL_DUEL" if batch005_eligible else "DO_NOT_SPEND_BATCH005",
            "stop_reason": stop_reason,
        },
    }

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_r28.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def verify():
    s = json.loads((OUT / "summary_r28.json").read_text(encoding="utf-8"))
    g = s["governance"]
    assert g["strict_prior_features"] and g["same_date_results_and_xg_withheld"]
    assert not g["odds_used"] and not g["market_prices_used"]
    assert g["duel_training_uses_chronological_oof_k1"] and not g["hyperparameter_search_used"]
    assert g["candidate_selected_on_validation_only"] and not g["test_used_for_candidate_selection"]
    assert not g["batch004_used_for_candidate_selection"] and not g["batch005_used"]
    assert not g["formal_promotion_allowed_from_this_run"]
    assert s["controls"]["K1_validation_hits"] == 2064
    assert len(s["validation_candidates"]) == len(DUEL_THRESHOLDS)
    if s["batch005_decision"]["eligible"]:
        assert s["selected_conditional_duel_rule"] is not None
        assert s["historical_test_confirmation"]["switches_to_draw"] >= MIN_TEST_SWITCHES_FOR_BATCH005
        assert s["historical_test_confirmation"]["switch_net"] >= MIN_TEST_NET_FOR_BATCH005
    print("R28_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_experiment_r28.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
