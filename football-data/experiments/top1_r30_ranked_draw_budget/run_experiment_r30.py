#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
R29_DIR = HERE.parent / "top1_r29_nonlinear_draw_duel"
sys.path.insert(0, str(R29_DIR))
import run_experiment_r29 as r29  # noqa: E402

r28 = r29.r28
r27 = r29.r27
r21 = r29.r21
r19 = r29.r19
r9 = r29.r9

RANK_FRACTIONS = [0.002, 0.004, 0.008, 0.015, 0.03, 0.06]
MIN_VALIDATION_SWITCHES = 8
MIN_VALIDATION_NET = 3
MIN_POSITIVE_VALIDATION_BLOCKS = 2
MAX_NEGATIVE_VALIDATION_BLOCKS = 1
MIN_TEST_SWITCHES_FOR_BATCH005 = 5
MIN_TEST_NET_FOR_BATCH005 = 1


def ranked_decision(rows, fraction, date_to_block=None):
    eligible = [(i, float(r["duel_p_draw"])) for i, r in enumerate(rows) if int(r["K1"]["top1"]) != 1 and r["duel_p_draw"] is not None]
    k = max(1, int(math.ceil(len(eligible) * float(fraction)))) if eligible else 0
    ranked = sorted(eligible, key=lambda z: (-z[1], rows[z[0]]["date"], z[0]))
    selected = {i for i, _ in ranked[:k]}

    base_hits = hits = switches = draw_hits = displaced = 0
    blocks = {}
    if date_to_block is not None:
        for i in sorted(set(date_to_block.values())):
            blocks[str(i)] = {"switches": 0, "draw_hits": 0, "displaced_correct": 0, "net": 0}

    for i, r in enumerate(rows):
        base = int(r["K1"]["top1"])
        actual = int(r["y"])
        base_correct = int(base == actual)
        base_hits += base_correct
        final = base
        if i in selected:
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
        hits += int(final == actual)

    for b in blocks.values():
        b["net"] = b["draw_hits"] - b["displaced_correct"]
    return {
        "fraction": float(fraction),
        "eligible_non_draw_rows": len(eligible),
        "count": len(rows),
        "baseline_hits": base_hits,
        "hits": hits,
        "gain_hits": hits - base_hits,
        "top1_accuracy": hits / len(rows),
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

    k1, _ = r21.fit_models(pred, b1, b2)
    val_k1 = [{"date": r["date"], "y": r["y"], "K1": p} for r, p in zip(val0, r19.decorate_k1(k1, val0))]
    k1v = r9.metrics(val_k1, "K1")
    if k1v["hits"] != 2064:
        raise RuntimeError("R30 K1 validation reproduction gate failed")

    r29_summary = json.loads((R29_DIR / "results" / "summary_r29.json").read_text(encoding="utf-8"))
    if r29_summary["batch005_decision"]["eligible"] or r29_summary["selected_nonlinear_duel_rule"] is not None:
        raise RuntimeError("R30 requires frozen R29 failure control")

    X, yd, _ = r28.build_oof_duel_training(train)
    duel_model = r29.fit_nonlinear_duel(X, yd)
    val = r29.decorate(val0, k1, duel_model)
    val_blocks = r27.make_date_blocks(val, 4)

    candidates = []
    for frac in RANK_FRACTIONS:
        m = ranked_decision(val, frac, val_blocks)
        viable = (
            m["switches_to_draw"] >= MIN_VALIDATION_SWITCHES
            and m["switch_net"] >= MIN_VALIDATION_NET
            and m["positive_time_blocks"] >= MIN_POSITIVE_VALIDATION_BLOCKS
            and m["negative_time_blocks"] <= MAX_NEGATIVE_VALIDATION_BLOCKS
        )
        candidates.append({"viable": viable, **m})

    viable = [x for x in candidates if x["viable"]]
    if viable:
        selected = max(viable, key=lambda x: (x["switch_net"], x["positive_time_blocks"], -x["negative_time_blocks"], x["switch_draw_precision"], -x["switches_to_draw"], -x["fraction"]))
        test = r29.decorate(test0, k1, duel_model)
        k1t = r9.metrics(test, "K1")
        if k1t["hits"] != 1877:
            raise RuntimeError("R30 K1 test reproduction gate failed")
        test_blocks = r27.make_date_blocks(test, 4)
        test_result = ranked_decision(test, selected["fraction"], test_blocks)
        batch005_eligible = test_result["switches_to_draw"] >= MIN_TEST_SWITCHES_FOR_BATCH005 and test_result["switch_net"] >= MIN_TEST_NET_FOR_BATCH005
        stop_reason = None if batch005_eligible else "FROZEN_RANK_BUDGET_FAILED_HISTORICAL_TEST_CONFIRMATION"
    else:
        selected = None
        test_result = None
        batch005_eligible = False
        stop_reason = "NO_VALIDATION_ROBUST_DRAW_RANKING_SIGNAL"

    summary = {
        "schema_version": "football3-top1-r30-ranked-draw-budget",
        "status": "COMPLETE",
        "classification": "DEVELOPMENT_RANKING_DIAGNOSTIC_BEFORE_BATCH005",
        "formal_weight": 0,
        "governance": {
            "base_commit": "da0b64709aa0a5b330bf65fde1785f7aeb031c3a",
            "snapshot_rows": 20000,
            "strict_prior_features": True,
            "same_date_results_and_xg_withheld": True,
            "odds_used": False,
            "market_prices_used": False,
            "nonlinear_model_fixed_from_r29": True,
            "rank_fraction_grid_predeclared": True,
            "candidate_selected_on_validation_only": True,
            "test_used_for_candidate_selection": False,
            "test_ranking_uses_labels": False,
            "batch004_used_for_candidate_selection": False,
            "batch005_used": False,
            "formal_promotion_allowed_from_this_run": False,
        },
        "question": "Does R29 contain useful relative ranking signal for DRAW even though absolute probability thresholds failed?",
        "selection_contract": {
            "rank_fractions": RANK_FRACTIONS,
            "min_validation_switches": MIN_VALIDATION_SWITCHES,
            "min_validation_net": MIN_VALIDATION_NET,
            "min_positive_validation_time_blocks": MIN_POSITIVE_VALIDATION_BLOCKS,
            "max_negative_validation_time_blocks": MAX_NEGATIVE_VALIDATION_BLOCKS,
            "min_test_switches_for_batch005": MIN_TEST_SWITCHES_FOR_BATCH005,
            "min_test_net_for_batch005": MIN_TEST_NET_FOR_BATCH005,
        },
        "controls": {"K1_validation_hits": k1v["hits"], "R29_stop_reason": r29_summary["batch005_decision"]["stop_reason"]},
        "validation_candidates": candidates,
        "selected_rank_budget_rule": selected,
        "historical_test_confirmation": test_result,
        "batch005_decision": {
            "eligible": batch005_eligible,
            "action": "SPEND_BATCH005_ON_FROZEN_RANK_BUDGET" if batch005_eligible else "DO_NOT_SPEND_BATCH005",
            "stop_reason": stop_reason,
        },
        "next_if_fail": "STOP_DRAW_ONLY_GATE_ENGINEERING_AND_ADD_NEW_PREMATCH_INFORMATION_FAMILIES",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_r30.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def verify():
    s = json.loads((OUT / "summary_r30.json").read_text(encoding="utf-8"))
    g = s["governance"]
    assert g["strict_prior_features"] and g["same_date_results_and_xg_withheld"]
    assert not g["odds_used"] and not g["market_prices_used"]
    assert g["nonlinear_model_fixed_from_r29"] and g["rank_fraction_grid_predeclared"]
    assert g["candidate_selected_on_validation_only"] and not g["test_used_for_candidate_selection"]
    assert not g["test_ranking_uses_labels"] and not g["batch005_used"]
    assert s["controls"]["K1_validation_hits"] == 2064
    assert len(s["validation_candidates"]) == len(RANK_FRACTIONS)
    print("R30_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_experiment_r30.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
