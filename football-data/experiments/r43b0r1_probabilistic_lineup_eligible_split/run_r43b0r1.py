#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
ROOT = HERE.parents[2]
BASE_DIR = ROOT / "football-data" / "experiments" / "r43b0_probabilistic_lineup_baseline"
sys.path.insert(0, str(BASE_DIR))
import run_r43b0_probabilistic_lineup as b0  # noqa: E402

FAILED_RUN_ID = 33100412771
DIAGNOSTIC_RUN_ID = 33100765212
SOURCE_HEAD = "9ae6aaae49c61cf00ae1c3808fc0a9db125302e0"
BURN_SIDES = 4000
TRAIN_SIDES = 8000
VAL_SIDES = 4000
MODEL_C = b0.MODEL_C


def boundary(ordered: list[dict], target: int) -> int:
    if not ordered:
        return 0
    i = min(max(1, int(target)), len(ordered) - 1)
    while i < len(ordered) and ordered[i]["date"] == ordered[i - 1]["date"]:
        i += 1
    return i


def assign_eligible_side_phases(sides: list[dict], examples: list[dict]) -> dict:
    ordered = sorted(sides, key=lambda s: (s["date"], s["fixture_id"], s["team_id"]))
    b1 = boundary(ordered, BURN_SIDES)
    b2 = boundary(ordered, b1 + TRAIN_SIDES)
    b3 = boundary(ordered, b2 + VAL_SIDES)
    if not (0 < b1 < b2 < b3 < len(ordered)):
        raise RuntimeError(f"eligible-side split impossible: n={len(ordered)} cuts={(b1,b2,b3)}")
    for i, s in enumerate(ordered):
        phase = "burn" if i < b1 else "train" if i < b2 else "val" if i < b3 else "test"
        s["phase"] = phase
        for x in examples[s["example_start"]:s["example_end"]]:
            x["phase"] = phase
    return {
        "eligible_sides": len(ordered),
        "cuts": {"burn_end": b1, "train_end": b2, "val_end": b3},
        "side_counts": {
            "burn": b1,
            "train": b2 - b1,
            "val": b3 - b2,
            "test": len(ordered) - b3,
        },
        "dates": {
            p: {
                "first": min((s["date"] for s in ordered if s["phase"] == p), default=None),
                "last": max((s["date"] for s in ordered if s["phase"] == p), default=None),
            }
            for p in ("burn", "train", "val", "test")
        },
        "date_safe": True,
    }


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = b0.load_matches()
    fixture_ids = {r["fixture_id"] for r in rows}
    player_map, source_meta = b0.prepare_player_rows(fixture_ids)

    # First construct every eligible evaluation side using strictly prior history.
    # The placeholder phase has no modeling role. Split is assigned only after
    # eligibility is known, using date/order and source coverage, never outcome quality.
    placeholder = {r["fixture_id"]: "pool" for r in rows}
    examples, sides = b0.build_examples(rows, player_map, placeholder)
    split = assign_eligible_side_phases(sides, examples)

    train = [x for x in examples if x["phase"] == "train"]
    val = [x for x in examples if x["phase"] == "val"]
    test = [x for x in examples if x["phase"] == "test"]
    val_sides = [s for s in sides if s["phase"] == "val"]
    test_sides = [s for s in sides if s["phase"] == "test"]
    if not train or not val or not test or len(test_sides) < 1000:
        raise RuntimeError(
            f"empty/undersized eligible split train={len(train)} val={len(val)} test={len(test)} test_sides={len(test_sides)}"
        )

    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=MODEL_C, max_iter=3000, random_state=0),
    )
    model.fit(
        np.asarray([x["features"] for x in train], dtype=float),
        np.asarray([x["y"] for x in train], dtype=int),
    )

    for phase_name in ("val", "test"):
        for s in [z for z in sides if z["phase"] == phase_name]:
            xs = examples[s["example_start"]:s["example_end"]]
            raw = model.predict_proba(np.asarray([x["features"] for x in xs], dtype=float))[:, 1]
            probs = b0.project_sum(raw)
            for x, q in zip(xs, probs):
                x["model_p"] = float(q)

    val_b = b0.binary_metrics(val, "baseline_p")
    val_m = b0.binary_metrics(val, "model_p")
    test_b = b0.binary_metrics(test, "baseline_p")
    test_m = b0.binary_metrics(test, "model_p")
    val_bo = b0.side_metrics(val_sides, examples, "baseline_p")
    val_mo = b0.side_metrics(val_sides, examples, "model_p")
    test_bo = b0.side_metrics(test_sides, examples, "baseline_p")
    test_mo = b0.side_metrics(test_sides, examples, "model_p")
    test_last = b0.last_xi_metrics(test_sides)
    blocks = b0.time_blocks(test_sides, examples)
    pos_blocks = sum(1 for x in blocks if x["delta_overlap"] > 0)
    neg_blocks = sum(1 for x in blocks if x["delta_overlap"] < 0)

    passed = bool(
        len(test_sides) >= 1000
        and test_m["logloss"] < test_b["logloss"]
        and test_m["brier"] < test_b["brier"]
        and test_mo["mean_xi_overlap"] >= test_bo["mean_xi_overlap"]
        and pos_blocks >= 2
        and neg_blocks <= 1
    )

    result = {
        "schema_version": "football3-r43b0r1-probabilistic-starter-eligible-side-split-v1",
        "status": "COMPLETE",
        "classification": "STRICT_CHRONOLOGICAL_LAGGED_LINEUP_PROBABILITY_BASELINE_ELIGIBLE_SOURCE_COHORT",
        "formal_weight": 0,
        "source_head": SOURCE_HEAD,
        "failed_prelabel_run": {
            "run_id": FAILED_RUN_ID,
            "error": "empty chronological split",
            "cause": "fixture-index split extended beyond fixture_players source coverage; diagnostic found eligible lineup sides only through 2026-02-10, so the absolute newest-fixture test phase had zero eligible sides",
        },
        "diagnostic_run_id": DIAGNOSTIC_RUN_ID,
        "governance": {
            "target_result_used_as_feature": False,
            "target_current_match_lineup_used_as_feature": False,
            "target_current_match_lineup_used_only_as_evaluation_label": True,
            "same_date_updates_before_prediction": False,
            "current_injury_status_used": False,
            "retrospective_availability_status_used_as_feature": False,
            "closing_odds_used": False,
            "random_split": False,
            "parameter_search": False,
            "test_metrics_inspected_before_r1_split_fix": False,
            "split_fix_uses_only_source_eligibility_date_and_order": True,
            "eligible_side_requires_exact_11_evaluation_label": True,
            "candidate_pool_prior_history_only": True,
            "r42l_lock_modified": False,
        },
        "design": {
            "candidate_pool_lookback_matches": b0.LOOKBACK_POOL,
            "candidate_pool_max_players": b0.MAX_CANDIDATES,
            "feature_lookback_matches": b0.FEATURE_LOOKBACK,
            "recency_decay": b0.DECAY,
            "baseline_beta_smoothing": b0.SMOOTH,
            "minimum_prior_team_matches": b0.MIN_HISTORY,
            "model": "StandardScaler + LogisticRegression binary P(start)",
            "model_C": MODEL_C,
            "features": b0.FEATURE_NAMES,
            "split_contract": "chronological eligible team-side samples; fixed targets burn=4000, train=8000, val=4000; boundaries advanced to next date; remainder untouched test",
            "probability_projection": "single scalar logit offset per team-side so sum_i P(start_i)=11; ranking preserved",
        },
        "source": source_meta,
        "split": split,
        "sample": {
            "candidate_examples_total": len(examples),
            "candidate_examples_train": len(train),
            "candidate_examples_val": len(val),
            "candidate_examples_test": len(test),
            "side_samples_total": len(sides),
            "side_samples_train": sum(s["phase"] == "train" for s in sides),
            "side_samples_val": len(val_sides),
            "side_samples_test": len(test_sides),
        },
        "validation": {
            "baseline_player_probability": val_b,
            "model_player_probability": val_m,
            "delta_logloss": val_m["logloss"] - val_b["logloss"],
            "delta_brier": val_m["brier"] - val_b["brier"],
            "baseline_xi": val_bo,
            "model_xi": val_mo,
            "delta_mean_xi_overlap": val_mo["mean_xi_overlap"] - val_bo["mean_xi_overlap"],
        },
        "test": {
            "baseline_player_probability": test_b,
            "model_player_probability": test_m,
            "delta_logloss": test_m["logloss"] - test_b["logloss"],
            "delta_brier": test_m["brier"] - test_b["brier"],
            "last_completed_xi": test_last,
            "baseline_weighted_xi": test_bo,
            "model_probabilistic_xi": test_mo,
            "delta_mean_xi_overlap_vs_weighted_baseline": test_mo["mean_xi_overlap"] - test_bo["mean_xi_overlap"],
            "delta_mean_xi_overlap_vs_last_xi": test_mo["mean_xi_overlap"] - test_last["mean_xi_overlap"],
            "time_blocks": blocks,
            "positive_overlap_blocks": pos_blocks,
            "negative_overlap_blocks": neg_blocks,
        },
        "gate": {
            "min_test_sides": 1000,
            "require_logloss_improve": True,
            "require_brier_improve": True,
            "require_xi_overlap_nonworse": True,
            "min_positive_overlap_blocks": 2,
            "max_negative_overlap_blocks": 1,
            "passed": passed,
            "action": "PROMOTE_R43B0R1_START_PROBABILITY_MECHANISM_TO_AVAILABILITY_INTEGRATION" if passed else "DO_NOT_PROMOTE_R43B0R1_AND_DO_NOT_RETUNE_ON_THIS_TEST",
        },
        "limitations": [
            "The eligible cohort ends where fixture_players coverage ends, not at the R9b xG snapshot end; it is therefore historical development evidence, not a fresh forward test.",
            "The candidate pool cannot include players never observed in a prior completed match, so new transfers/youth debuts remain an explicit cold-start problem.",
            "fixture_players does not prove unused-bench membership, so B0R1 estimates P(start), not P(bench).",
            "This stage changes no 1X2 probabilities and leaves the frozen R42L forward lock untouched.",
        ],
    }
    p = OUT / "summary_r43b0r1_probabilistic_lineup.json"
    p.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def verify() -> None:
    x = json.loads((OUT / "summary_r43b0r1_probabilistic_lineup.json").read_text(encoding="utf-8"))
    assert x["status"] == "COMPLETE"
    assert x["formal_weight"] == 0
    assert x["governance"]["target_current_match_lineup_used_as_feature"] is False
    assert x["governance"]["same_date_updates_before_prediction"] is False
    assert x["governance"]["retrospective_availability_status_used_as_feature"] is False
    assert x["governance"]["test_metrics_inspected_before_r1_split_fix"] is False
    assert x["governance"]["split_fix_uses_only_source_eligibility_date_and_order"] is True
    assert x["split"]["date_safe"] is True
    assert x["sample"]["side_samples_test"] >= 1000
    print("R43B0R1 contract verified")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run()
    elif cmd == "verify":
        verify()
    else:
        raise SystemExit(f"unknown command: {cmd}")
