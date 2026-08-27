#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
R38_DIR = HERE.parent / "top1_r38_prior_match_process_form"
R40C_DIR = HERE.parent / "top1_r40c_role_aware_expected_xi"
sys.path.insert(0, str(R38_DIR))
sys.path.insert(0, str(R40C_DIR))
import run_experiment_r38 as r38  # noqa: E402
import run_experiment_r40c as r40c  # noqa: E402

r9 = r40c.r9
r33 = r40c.r33

PROCESS_BASE_NAMES = list(r38.FEATURE_SETS["MATCH_PROCESS_FORM_COMBINED"])
ROLE_BASE_NAMES = list(r40c.POSITIONAL_RESULT_NAMES)
PROCESS_NAMES = [f"process__{x}" for x in PROCESS_BASE_NAMES]
ROLE_NAMES = [f"role__{x}" for x in ROLE_BASE_NAMES]
FUSION_NAMES = PROCESS_NAMES + ROLE_NAMES

MIN_VALIDATION_GAIN_HITS = 3
MIN_POSITIVE_VALIDATION_BLOCKS = 2
MAX_NEGATIVE_VALIDATION_BLOCKS = 1
MAX_VALIDATION_LOGLOSS_WORSEN = 0.001
MIN_REUSED_TEST_GAIN_HITS = 1
MIN_POSITIVE_REUSED_TEST_BLOCKS = 2
MAX_NEGATIVE_REUSED_TEST_BLOCKS = 1
MAX_REUSED_TEST_LOGLOSS_WORSEN = 0.001


def merge_histories():
    p38, process_source, process_coverage = r38.build_history()
    p40, player_source = r40c.build_history()
    if len(p38) != len(p40):
        raise RuntimeError(f"R41 history length mismatch: {len(p38)} vs {len(p40)}")
    out = []
    for i, (a, b) in enumerate(zip(p38, p40)):
        if a["date"] != b["date"] or int(a["y"]) != int(b["y"]):
            raise RuntimeError(f"R41 row identity mismatch at {i}")
        for k in ("p_home", "p_draw", "p_away", "xg_mu_home", "xg_mu_away"):
            if abs(float(a["raw"][k]) - float(b["raw"][k])) > 1e-12:
                raise RuntimeError(f"R41 raw baseline mismatch at {i} field={k}")
        cf = {}
        for name in PROCESS_BASE_NAMES:
            cf[f"process__{name}"] = float(a["context_features"][name])
        for name in ROLE_BASE_NAMES:
            cf[f"role__{name}"] = float(b["context_features"][name])
        out.append({"date": a["date"], "y": int(a["y"]), "raw": a["raw"], "context_features": cf})
    meta = {
        "process_source": process_source,
        "process_coverage": process_coverage,
        "player_source": player_source,
        "row_count": len(out),
    }
    return out, meta


def x_for(rec):
    return list(r9.feat_k1(rec["raw"])) + [float(rec["context_features"][n]) for n in FUSION_NAMES]


def fit_model(train):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=0.5, max_iter=3000, random_state=0),
    )
    model.fit([x_for(r) for r in train], [r["y"] for r in train])
    return model


def decorate(model, rows):
    probs = model.predict_proba([x_for(r) for r in rows])
    classes = list(model[-1].classes_)
    out = []
    for src, row_probs in zip(rows, probs):
        v = np.zeros(3, dtype=float)
        for cls, p in zip(classes, row_probs):
            v[int(cls)] = float(p)
        out.append({"date": src["date"], "y": src["y"], "P": r9.decorate(v)})
    return out


def gate(base_metrics, cand_metrics, paired, min_gain, min_pos, max_neg, max_logloss):
    gain = int(cand_metrics["hits"] - base_metrics["hits"])
    logdelta = float(cand_metrics["logloss"] - base_metrics["logloss"])
    return {
        "pass": bool(
            gain >= min_gain
            and paired["positive_time_blocks"] >= min_pos
            and paired["negative_time_blocks"] <= max_neg
            and logdelta <= max_logloss
        ),
        "gain_hits": gain,
        "gain_top1_pp": 100.0 * (cand_metrics["top1_accuracy"] - base_metrics["top1_accuracy"]),
        "logloss_delta": logdelta,
        "brier_delta": float(cand_metrics["brier"] - base_metrics["brier"]),
        "rps_delta": float(cand_metrics["rps"] - base_metrics["rps"]),
        "paired": paired,
    }


def run():
    pred, source_meta = merge_histories()
    b1 = r9.boundary(pred, r9.TARGET_BURN)
    b2 = r9.boundary(pred, b1 + r9.TARGET_TRAIN)
    b3 = r9.boundary(pred, b2 + r9.TARGET_VAL)
    train, val, test = pred[b1:b2], pred[b2:b3], pred[b3:]

    k1 = r33.baseline_model(train)
    val_base = r33.baseline_decorate(k1, val)
    base_v = r33.metrics(val_base)
    if base_v["hits"] != 2064:
        raise RuntimeError(f"R41 K1 validation reproduction failed: {base_v['hits']}")

    model = fit_model(train)
    val_cand = decorate(model, val)
    cand_v = r33.metrics(val_cand)
    pair_v = r33.paired_blocks(val_base, val_cand)
    val_gate = gate(
        base_v, cand_v, pair_v,
        MIN_VALIDATION_GAIN_HITS,
        MIN_POSITIVE_VALIDATION_BLOCKS,
        MAX_NEGATIVE_VALIDATION_BLOCKS,
        MAX_VALIDATION_LOGLOSS_WORSEN,
    )

    reused_test = None
    action = "STOP_R41_NO_VALIDATION_ROBUST_FUSION_GAIN"
    if val_gate["pass"]:
        test_base = r33.baseline_decorate(k1, test)
        base_t = r33.metrics(test_base)
        if base_t["hits"] != 1877:
            raise RuntimeError(f"R41 K1 historical test reproduction failed: {base_t['hits']}")
        test_cand = decorate(model, test)
        cand_t = r33.metrics(test_cand)
        pair_t = r33.paired_blocks(test_base, test_cand)
        test_gate = gate(
            base_t, cand_t, pair_t,
            MIN_REUSED_TEST_GAIN_HITS,
            MIN_POSITIVE_REUSED_TEST_BLOCKS,
            MAX_NEGATIVE_REUSED_TEST_BLOCKS,
            MAX_REUSED_TEST_LOGLOSS_WORSEN,
        )
        reused_test = {
            "classification": "REUSED_HISTORICAL_TEST_NOT_INDEPENDENT_CONFIRMATION",
            "baseline": base_t,
            "candidate": cand_t,
            **test_gate,
        }
        action = (
            "FREEZE_R41_AND_RUN_DISJOINT_EARLIER_ERA_REPLICATION"
            if test_gate["pass"]
            else "STOP_R41_FUSION_NOT_STABLE_ON_REUSED_HISTORICAL_TEST"
        )

    summary = {
        "schema_version": "football3-top1-r41-process-role-fusion-v1",
        "status": "COMPLETE",
        "classification": "DEVELOPMENT_FIXED_FUSION_OF_TWO_PREVIOUSLY_IDENTIFIED_STRICT_PRIOR_FAMILIES",
        "formal_weight": 0,
        "governance": {
            "base_commit": "3fbf300b004bb90f22d90bc202c4cdba1ccc7d9e",
            "snapshot_rows": 20000,
            "strict_prior_features": True,
            "same_date_updates_withheld_in_both_component_builders": True,
            "current_match_starting_xi_used": False,
            "current_match_injury_or_suspension_used": False,
            "odds_used": False,
            "market_prices_used": False,
            "feature_set_predeclared": True,
            "candidate_grid_used": False,
            "model_hyperparameter_search_used": False,
            "reused_historical_test_is_not_independent_confirmation": True,
            "formal_promotion_allowed_from_this_run": False,
        },
        "question": "Can frozen prior match-process form and role-aware expected-XI result strength combine into a more time-stable Top1 signal than either family alone?",
        "feature_family": {
            "name": "PROCESS_PLUS_ROLE_AWARE_EXPECTED_XI_RESULT",
            "process_features": PROCESS_BASE_NAMES,
            "role_features": ROLE_BASE_NAMES,
            "model": "StandardScaler + multinomial LogisticRegression(C=0.5, random_state=0)",
            "source": source_meta,
        },
        "selection_contract": {
            "validation": {
                "min_gain_hits": MIN_VALIDATION_GAIN_HITS,
                "min_positive_blocks": MIN_POSITIVE_VALIDATION_BLOCKS,
                "max_negative_blocks": MAX_NEGATIVE_VALIDATION_BLOCKS,
                "max_logloss_worsen": MAX_VALIDATION_LOGLOSS_WORSEN,
            },
            "reused_historical_test_directional_gate": {
                "min_gain_hits": MIN_REUSED_TEST_GAIN_HITS,
                "min_positive_blocks": MIN_POSITIVE_REUSED_TEST_BLOCKS,
                "max_negative_blocks": MAX_NEGATIVE_REUSED_TEST_BLOCKS,
                "max_logloss_worsen": MAX_REUSED_TEST_LOGLOSS_WORSEN,
                "scientific_status": "supportive_only_not_confirmation",
            },
        },
        "controls": {
            "K1_validation": base_v,
            "R38": "process family previously showed +12 validation / +11 historical-test hits but failed 2-negative-block test stability gate",
            "R40C": "role-aware result family previously showed +4 validation / +7 historical-test hits but failed historical-test logloss gate",
        },
        "validation": {
            "baseline": base_v,
            "candidate": cand_v,
            **val_gate,
        },
        "reused_historical_test": reused_test,
        "decision": {
            "action": action,
            "formal_promotion": False,
            "next_if_supportive": "Run an untouched disjoint earlier-era replication with the exact frozen R41 feature list and C=0.5; do not retune on the reused historical test.",
            "next_if_fail": "Do not tune fusion weights on this reused test; return to new timestamp-auditable information collection.",
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary_r41.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def verify():
    s = json.loads((OUT / "summary_r41.json").read_text(encoding="utf-8"))
    g = s["governance"]
    assert s["status"] == "COMPLETE"
    assert g["strict_prior_features"] and g["same_date_updates_withheld_in_both_component_builders"]
    assert not g["current_match_starting_xi_used"] and not g["current_match_injury_or_suspension_used"]
    assert not g["odds_used"] and not g["market_prices_used"]
    assert g["feature_set_predeclared"] and not g["candidate_grid_used"] and not g["model_hyperparameter_search_used"]
    assert g["reused_historical_test_is_not_independent_confirmation"] and not g["formal_promotion_allowed_from_this_run"]
    assert s["validation"]["baseline"]["hits"] == 2064
    if s["reused_historical_test"] is not None:
        assert s["reused_historical_test"]["baseline"]["hits"] == 1877
        assert s["reused_historical_test"]["classification"] == "REUSED_HISTORICAL_TEST_NOT_INDEPENDENT_CONFIRMATION"
    print("R41_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: run_experiment_r41.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
