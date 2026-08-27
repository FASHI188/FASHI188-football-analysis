#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
R28_DIR = HERE.parent / "top1_r28_conditional_draw_duel"
sys.path.insert(0, str(R28_DIR))
import run_experiment_r28 as r28  # noqa: E402

r27 = r28.r27
r21 = r28.r21
r19 = r28.r19
r9 = r28.r9

# R29 keeps R28's actionable DRAW-vs-current-side target, but replaces the linear
# logistic boundary with one fixed shallow nonlinear tree ensemble. No model
# hyperparameter search is performed. Decision thresholds are predeclared and are
# selected on validation only; historical TEST is touched only after a viable rule
# is frozen.
DUEL_THRESHOLDS = [0.34, 0.36, 0.38, 0.40, 0.42, 0.44, 0.46, 0.48, 0.50, 0.52, 0.55, 0.58, 0.60, 0.65, 0.70]
MIN_VALIDATION_SWITCHES = 8
MIN_VALIDATION_NET = 3
MIN_POSITIVE_VALIDATION_BLOCKS = 2
MAX_NEGATIVE_VALIDATION_BLOCKS = 1
MIN_TEST_SWITCHES_FOR_BATCH005 = 5
MIN_TEST_NET_FOR_BATCH005 = 1

MODEL_PARAMS = {
    "learning_rate": 0.05,
    "max_iter": 140,
    "max_leaf_nodes": 7,
    "max_depth": 3,
    "min_samples_leaf": 80,
    "l2_regularization": 1.0,
    "random_state": 0,
}


def fit_nonlinear_duel(X, y):
    from sklearn.ensemble import HistGradientBoostingClassifier

    if sorted(set(y)) != [0, 1]:
        raise RuntimeError("R29 binary duel classes incomplete")
    m = HistGradientBoostingClassifier(**MODEL_PARAMS)
    m.fit(X, y)
    return m


def decorate(rows, k1, duel_model):
    p1s = r19.decorate_k1(k1, rows)
    classes = list(duel_model.classes_)
    j = classes.index(1)
    out = []
    for rec, p1 in zip(rows, p1s):
        if int(p1["top1"]) == 1:
            score = None
        else:
            score = float(duel_model.predict_proba([r27.switch_features(rec, p1)])[0][j])
        out.append({"date": rec["date"], "y": rec["y"], "K1": p1, "duel_p_draw": score})
    return out


def run():
    pred = r19.build_history()
    b1 = r9.boundary(pred, r9.TARGET_BURN)
    b2 = r9.boundary(pred, b1 + r9.TARGET_TRAIN)
    b3 = r9.boundary(pred, b2 + r9.TARGET_VAL)
    train, val0, test0 = pred[b1:b2], pred[b2:b3], pred[b3:]

    k1, _ = r21.fit_models(pred, b1, b2)
    val_k1 = [{"date": r["date"], "y": r["y"], "K1": p} for r, p in zip(val0, r19.decorate_k1(k1, val0))]
    k1v = r9.metrics(val_k1, "K1")
    if k1v["hits"] != 2064:
        raise RuntimeError("R29 K1 validation reproduction gate failed")

    r28_summary = json.loads((R28_DIR / "results" / "summary_r28.json").read_text(encoding="utf-8"))
    if r28_summary["batch005_decision"]["eligible"] or r28_summary["selected_conditional_duel_rule"] is not None:
        raise RuntimeError("R29 requires frozen R28 failure control")

    X, yd, fold_summaries = r28.build_oof_duel_training(train)
    duel_model = fit_nonlinear_duel(X, yd)
    val = decorate(val0, k1, duel_model)
    val_blocks = r27.make_date_blocks(val, 4)

    candidates = []
    for thr in DUEL_THRESHOLDS:
        m = r28.decision(val, thr, val_blocks)
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
            raise RuntimeError("R29 K1 test reproduction gate failed")
        test_blocks = r27.make_date_blocks(test, 4)
        test_result = r28.decision(test, selected["threshold"], test_blocks)
        batch005_eligible = (
            test_result["switches_to_draw"] >= MIN_TEST_SWITCHES_FOR_BATCH005
            and test_result["switch_net"] >= MIN_TEST_NET_FOR_BATCH005
        )
        stop_reason = None if batch005_eligible else "FROZEN_NONLINEAR_DUEL_FAILED_HISTORICAL_TEST_CONFIRMATION"
    else:
        selected = None
        test_result = None
        batch005_eligible = False
        stop_reason = "NO_VALIDATION_ROBUST_NONLINEAR_DRAW_DUEL"

    summary = {
        "schema_version": "football3-top1-r29-nonlinear-draw-duel",
        "status": "COMPLETE",
        "classification": "DEVELOPMENT_NONLINEAR_MECHANISM_BEFORE_BATCH005",
        "formal_weight": 0,
        "governance": {
            "base_commit": "d6f24d77599c199281b3d0d18e51c44fdcaf8348",
            "snapshot_rows": 20000,
            "strict_prior_features": True,
            "same_date_results_and_xg_withheld": True,
            "odds_used": False,
            "market_prices_used": False,
            "duel_training_uses_chronological_oof_k1": True,
            "duel_oof_blocks": r28.OOF_BLOCKS,
            "model_hyperparameter_search_used": False,
            "fixed_model_params_predeclared": True,
            "candidate_grid_predeclared": True,
            "candidate_selected_on_validation_only": True,
            "test_evaluated_only_after_viable_validation_freeze": True,
            "test_used_for_candidate_selection": False,
            "batch004_used_for_candidate_selection": False,
            "batch005_used": False,
            "formal_promotion_allowed_from_this_run": False,
        },
        "question": "Was R28 failing because the DRAW-vs-K1 decision boundary is nonlinear rather than because strict-prior features contain no usable signal?",
        "mechanism": {
            "baseline": "R9b K1",
            "training_target": "same actionable conditional DRAW-vs-K1-side target as R28",
            "features": "same strict-prior R27/R28 feature set",
            "model": "fixed shallow HistGradientBoostingClassifier",
            "model_params": MODEL_PARAMS,
            "decision": "preserve natural K1 DRAW; switch HOME/AWAY to DRAW only above validation-frozen nonlinear duel probability",
        },
        "oof_duel_training": {
            "rows": len(X),
            "stay_chosen_side": int(sum(int(x == 0) for x in yd)),
            "draw": int(sum(int(x == 1) for x in yd)),
            "folds": fold_summaries,
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
            "R28_stop_reason": r28_summary["batch005_decision"]["stop_reason"],
        },
        "validation_candidates": candidates,
        "selected_nonlinear_duel_rule": selected,
        "historical_test_confirmation": test_result,
        "batch005_decision": {
            "eligible": batch005_eligible,
            "action": "SPEND_BATCH005_ON_FROZEN_NONLINEAR_DUEL" if batch005_eligible else "DO_NOT_SPEND_BATCH005",
            "stop_reason": stop_reason,
        },
    }

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_r29.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def verify():
    s = json.loads((OUT / "summary_r29.json").read_text(encoding="utf-8"))
    g = s["governance"]
    assert g["strict_prior_features"] and g["same_date_results_and_xg_withheld"]
    assert not g["odds_used"] and not g["market_prices_used"]
    assert g["duel_training_uses_chronological_oof_k1"]
    assert not g["model_hyperparameter_search_used"] and g["fixed_model_params_predeclared"]
    assert g["candidate_selected_on_validation_only"] and not g["test_used_for_candidate_selection"]
    assert not g["batch004_used_for_candidate_selection"] and not g["batch005_used"]
    assert not g["formal_promotion_allowed_from_this_run"]
    assert s["controls"]["K1_validation_hits"] == 2064
    assert len(s["validation_candidates"]) == len(DUEL_THRESHOLDS)
    if s["batch005_decision"]["eligible"]:
        assert s["selected_nonlinear_duel_rule"] is not None
        assert s["historical_test_confirmation"]["switches_to_draw"] >= MIN_TEST_SWITCHES_FOR_BATCH005
        assert s["historical_test_confirmation"]["switch_net"] >= MIN_TEST_NET_FOR_BATCH005
    print("R29_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_experiment_r29.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
