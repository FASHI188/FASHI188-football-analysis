#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
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

# Small, predeclared search: context strength comes only from TRAIN; model/rule
# selection comes only from VALIDATION; TEST is touched once after freezing.
CONTEXT_QUANTILES = [0.50, 0.67, 0.80]
EDGE_THRESHOLDS = [0.00, 0.01, 0.02, 0.03]
MIN_VALIDATION_SWITCHES = 4
MIN_VALIDATION_NET = 2
MIN_POSITIVE_VALIDATION_BLOCKS = 2
MAX_NEGATIVE_VALIDATION_BLOCKS = 1
MIN_TEST_SWITCHES_FOR_BATCH005 = 3
MIN_TEST_NET_FOR_BATCH005 = 1


def compact_map(rec):
    vals = r14.compact(rec["draw_features"])
    return {name: float(value) for name, value in zip(r14.COMPACT_NAMES, vals)}


def components(rec):
    x = compact_map(rec)
    return {
        "draw_history": float(np.mean([
            x["competition_draw_rate"], x["team_draw_mean"], x["recent_draw_mean"]
        ])),
        "venue_draw": float(np.mean([
            x["home_venue_draw_rate"], x["away_venue_draw_rate"]
        ])),
        "low_score": float(np.mean([
            x["competition_low2_rate"], x["team_low2_mean"], x["xg_low_draw_mass"]
        ])),
        "parity": x["xg_parity"],
    }


def fit_context_normalizer(train):
    keys = ["draw_history", "venue_draw", "low_score", "parity"]
    matrix = {k: [] for k in keys}
    for rec in train:
        c = components(rec)
        for k in keys:
            matrix[k].append(c[k])
    norm = {}
    for k in keys:
        a = np.asarray(matrix[k], dtype=float)
        sd = float(np.std(a))
        norm[k] = {"mean": float(np.mean(a)), "std": sd if sd > 1e-12 else 1.0}
    return norm


def context_score(rec, norm):
    c = components(rec)
    z = [(c[k] - norm[k]["mean"]) / norm[k]["std"] for k in norm]
    return float(np.mean(z))


def decorate_subset(rows, k1, d1, norm):
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
            "context_score": context_score(src, norm),
            "components": components(src),
        })
    return out


def make_date_blocks(rows, n=4):
    dates = sorted({r["date"] for r in rows})
    chunks = np.array_split(np.asarray(dates, dtype=object), n)
    date_to_block = {}
    for i, chunk in enumerate(chunks):
        for d in chunk.tolist():
            date_to_block[d] = i
    return date_to_block


def decision(rows, context_cutoff, edge_threshold, date_to_block=None):
    base_hits = 0
    hits = 0
    switches = draw_hits = displaced_correct = 0
    blocks = {}
    if date_to_block is not None:
        for i in sorted(set(date_to_block.values())):
            blocks[str(i)] = {"switches": 0, "draw_hits": 0, "displaced_correct": 0, "net": 0}

    for r in rows:
        base = int(r["K1"]["top1"])
        base_correct = int(base == r["y"])
        base_hits += base_correct
        final = base
        activate = (
            base != 1
            and int(r["J2"]["top1"]) == 1
            and r["draw_edge"] >= edge_threshold
            and r["context_score"] >= context_cutoff
        )
        if activate:
            final = 1
            switches += 1
            is_draw = int(r["y"] == 1)
            lost_correct = int(base_correct and r["y"] != 1)
            draw_hits += is_draw
            displaced_correct += lost_correct
            if date_to_block is not None:
                b = str(date_to_block[r["date"]])
                blocks[b]["switches"] += 1
                blocks[b]["draw_hits"] += is_draw
                blocks[b]["displaced_correct"] += lost_correct
        hits += int(final == r["y"])

    for b in blocks.values():
        b["net"] = b["draw_hits"] - b["displaced_correct"]
    positive_blocks = sum(int(b["net"] > 0) for b in blocks.values())
    negative_blocks = sum(int(b["net"] < 0) for b in blocks.values())
    net = draw_hits - displaced_correct
    return {
        "count": len(rows),
        "baseline_hits": base_hits,
        "hits": hits,
        "gain_hits": hits - base_hits,
        "switches_to_draw": switches,
        "switch_draw_hits": draw_hits,
        "switch_displaced_correct_non_draw": displaced_correct,
        "switch_wrong_to_wrong": switches - draw_hits - displaced_correct,
        "switch_net": net,
        "switch_draw_precision": draw_hits / switches if switches else 0.0,
        "positive_time_blocks": positive_blocks,
        "negative_time_blocks": negative_blocks,
        "time_blocks": blocks,
    }


def diagnostic_family(rows, family, cutoff, edge_threshold=0.0):
    switches = draw_hits = displaced = 0
    for r in rows:
        base = int(r["K1"]["top1"])
        if (
            base != 1
            and int(r["J2"]["top1"]) == 1
            and r["draw_edge"] >= edge_threshold
            and r["components"][family] >= cutoff
        ):
            switches += 1
            draw_hits += int(r["y"] == 1)
            displaced += int(base == r["y"] and r["y"] != 1)
    return {
        "switches": switches,
        "draw_hits": draw_hits,
        "displaced_correct": displaced,
        "net": draw_hits - displaced,
        "draw_precision": draw_hits / switches if switches else 0.0,
    }


def run():
    pred = r19.build_history()
    b1 = r9.boundary(pred, r9.TARGET_BURN)
    b2 = r9.boundary(pred, b1 + r9.TARGET_TRAIN)
    b3 = r9.boundary(pred, b2 + r9.TARGET_VAL)
    train0, val0, test0 = pred[b1:b2], pred[b2:b3], pred[b3:]

    k1, d1 = r21.fit_models(pred, b1, b2)
    norm = fit_context_normalizer(train0)
    train_scores = np.asarray([context_score(r, norm) for r in train0], dtype=float)
    context_cutoffs = {str(q): float(np.quantile(train_scores, q)) for q in CONTEXT_QUANTILES}

    val = decorate_subset(val0, k1, d1, norm)
    test = decorate_subset(test0, k1, d1, norm)
    k1v = r9.metrics(val, "K1")
    k1t = r9.metrics(test, "K1")
    if k1v["hits"] != 2064 or k1t["hits"] != 1877:
        raise RuntimeError("R26 R21/K1 reproduction gate failed")

    # R21 global control must reproduce before any contextual conclusion is trusted.
    global_selected, global_scan = r21.choose(val)
    global_test = r21.decision(test, global_selected["threshold"])
    if global_selected["threshold"] != 0.08 or global_selected["switches_to_draw"] != 1:
        raise RuntimeError("R26 R21 global-control reproduction gate failed")

    val_blocks = make_date_blocks(val, 4)
    test_blocks = make_date_blocks(test, 4)
    candidates = []
    for q in CONTEXT_QUANTILES:
        cutoff = context_cutoffs[str(q)]
        for edge in EDGE_THRESHOLDS:
            m = decision(val, cutoff, edge, val_blocks)
            viable = (
                m["switches_to_draw"] >= MIN_VALIDATION_SWITCHES
                and m["switch_net"] >= MIN_VALIDATION_NET
                and m["positive_time_blocks"] >= MIN_POSITIVE_VALIDATION_BLOCKS
                and m["negative_time_blocks"] <= MAX_NEGATIVE_VALIDATION_BLOCKS
            )
            candidates.append({
                "context_quantile": q,
                "context_cutoff": cutoff,
                "edge_threshold": edge,
                "viable": viable,
                **m,
            })

    viable = [x for x in candidates if x["viable"]]
    if viable:
        # Validation-only freeze. Prefer net hits, temporal consistency, support, then stronger context/edge.
        selected = max(
            viable,
            key=lambda x: (
                x["switch_net"],
                x["positive_time_blocks"],
                -x["negative_time_blocks"],
                x["switches_to_draw"],
                x["context_quantile"],
                x["edge_threshold"],
            ),
        )
        test_result = decision(test, selected["context_cutoff"], selected["edge_threshold"], test_blocks)
        batch005_eligible = (
            test_result["switches_to_draw"] >= MIN_TEST_SWITCHES_FOR_BATCH005
            and test_result["switch_net"] >= MIN_TEST_NET_FOR_BATCH005
        )
        stop_reason = None if batch005_eligible else "FROZEN_CONTEXT_RULE_FAILED_HISTORICAL_TEST_CONFIRMATION"
    else:
        selected = None
        test_result = None
        batch005_eligible = False
        stop_reason = "NO_VALIDATION_ROBUST_CONTEXT_ACTIVATION"

    # Descriptive-only mechanism audit. Family cutoffs are train-only 67th percentiles;
    # these rows are never allowed to choose/freeze the primary candidate above.
    family_cutoffs = {}
    family_audit = {}
    for fam in ["draw_history", "venue_draw", "low_score", "parity"]:
        a = np.asarray([components(r)[fam] for r in train0], dtype=float)
        cutoff = float(np.quantile(a, 0.67))
        family_cutoffs[fam] = cutoff
        family_audit[fam] = {
            "train_q67_cutoff": cutoff,
            "validation_edge0": diagnostic_family(val, fam, cutoff, 0.0),
            "test_edge0": diagnostic_family(test, fam, cutoff, 0.0),
        }

    summary = {
        "schema_version": "football3-top1-r26-r21-context-audit",
        "status": "COMPLETE",
        "classification": "DEVELOPMENT_CONTEXT_CONDITION_AUDIT_BEFORE_BATCH005",
        "formal_weight": 0,
        "governance": {
            "base_commit": "1704b8c264fc9f43bc80e48d5d4158796f4026d6",
            "snapshot_rows": 20000,
            "strict_prior_features": True,
            "same_date_results_and_xg_withheld": True,
            "odds_used": False,
            "market_prices_used": False,
            "context_normalization_fit_on_train_only": True,
            "context_cutoffs_fit_on_train_only": True,
            "candidate_grid_predeclared": True,
            "candidate_selected_on_validation_only": True,
            "test_used_for_candidate_selection": False,
            "batch004_used_for_candidate_selection": False,
            "batch005_used": False,
            "formal_promotion_allowed_from_this_run": False,
        },
        "question": "Can the R21 binary draw head activate DRAW only in strong pre-match draw contexts, rather than through one global edge threshold?",
        "context_definition": {
            "components": {
                "draw_history": ["competition_draw_rate", "team_draw_mean", "recent_draw_mean"],
                "venue_draw": ["home_venue_draw_rate", "away_venue_draw_rate"],
                "low_score": ["competition_low2_rate", "team_low2_mean", "xg_low_draw_mass"],
                "parity": ["xg_parity"],
            },
            "consensus": "mean of TRAIN-standardized draw_history, venue_draw, low_score, parity",
            "normalizer": norm,
            "train_quantile_cutoffs": context_cutoffs,
        },
        "selection_contract": {
            "context_quantiles": CONTEXT_QUANTILES,
            "edge_thresholds": EDGE_THRESHOLDS,
            "min_validation_switches": MIN_VALIDATION_SWITCHES,
            "min_validation_net": MIN_VALIDATION_NET,
            "min_positive_validation_time_blocks": MIN_POSITIVE_VALIDATION_BLOCKS,
            "max_negative_validation_time_blocks": MAX_NEGATIVE_VALIDATION_BLOCKS,
            "min_test_switches_for_batch005": MIN_TEST_SWITCHES_FOR_BATCH005,
            "min_test_net_for_batch005": MIN_TEST_NET_FOR_BATCH005,
        },
        "r21_global_control": {
            "validation_selected_threshold": global_selected["threshold"],
            "validation": global_selected,
            "test": global_test,
            "validation_scan": global_scan,
        },
        "validation_candidates": candidates,
        "selected_context_rule": selected,
        "historical_test_confirmation": test_result,
        "descriptive_family_audit": family_audit,
        "batch005_decision": {
            "eligible": batch005_eligible,
            "action": "SPEND_BATCH005_ON_FROZEN_CONTEXT_RULE" if batch005_eligible else "DO_NOT_SPEND_BATCH005",
            "stop_reason": stop_reason,
        },
    }

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_r26.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def verify():
    s = json.loads((OUT / "summary_r26.json").read_text(encoding="utf-8"))
    g = s["governance"]
    assert g["strict_prior_features"] and g["same_date_results_and_xg_withheld"]
    assert not g["odds_used"] and not g["market_prices_used"]
    assert g["context_normalization_fit_on_train_only"] and g["context_cutoffs_fit_on_train_only"]
    assert g["candidate_selected_on_validation_only"] and not g["test_used_for_candidate_selection"]
    assert not g["batch004_used_for_candidate_selection"] and not g["batch005_used"]
    assert not g["formal_promotion_allowed_from_this_run"]
    assert s["r21_global_control"]["validation_selected_threshold"] == 0.08
    assert len(s["validation_candidates"]) == len(CONTEXT_QUANTILES) * len(EDGE_THRESHOLDS)
    if s["batch005_decision"]["eligible"]:
        assert s["selected_context_rule"] is not None
        assert s["historical_test_confirmation"]["switches_to_draw"] >= MIN_TEST_SWITCHES_FOR_BATCH005
        assert s["historical_test_confirmation"]["switch_net"] >= MIN_TEST_NET_FOR_BATCH005
    print("R26_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_experiment_r26.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
