from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

import evaluate_c069_r2_maxcard_coverage as r2
import run_c069_matched_pair_draw_state_r1 as r1run


SCHEMA_VERSION = "C069_MATCHED_PAIR_DRAW_STATE_R3_A03"
SEED = "A_SERIES_WYSCOUT_20260818_R1"
A03_START = 800
A03_STOP = 1200
FIXED_FOLDS = {
    "fold_1": ("2018-01-13", "2018-01-13", "2018-02-23"),
    "fold_2": ("2018-02-23", "2018-02-23", "2018-04-08"),
    "fold_3": ("2018-04-08", "2018-04-08", None),
}


def _load_a03(path: Path):
    z = zipfile.ZipFile(path)
    pkg = json.loads(z.read("PACKAGE.json"))
    manifest = [
        json.loads(line)
        for line in z.read("MANIFEST.jsonl").decode().splitlines()
        if line
    ]
    manifest = sorted(manifest, key=lambda x: x["rank"])
    if pkg.get("package_id") != "A03" or int(pkg.get("match_count", -1)) != 400:
        raise RuntimeError("A03 package identity mismatch")
    if pkg.get("seed") != SEED:
        raise RuntimeError("A03 seed mismatch")
    if int(pkg.get("source_global_rank_one_based_start", -1)) != 801:
        raise RuntimeError("A03 global start rank mismatch")
    if int(pkg.get("source_global_rank_one_based_stop", -1)) != 1200:
        raise RuntimeError("A03 global stop rank mismatch")
    if [int(x["rank"]) for x in manifest] != list(range(1, 401)):
        raise RuntimeError("A03 package rank sequence mismatch")
    if [int(x["source_global_rank_one_based"]) for x in manifest] != list(range(801, 1201)):
        raise RuntimeError("A03 source global rank sequence mismatch")

    ids = [str(x["match_id"]) for x in manifest]
    ids_sha = hashlib.sha256(("\n".join(ids) + "\n").encode()).hexdigest()
    if ids_sha != pkg.get("ids_sha256"):
        raise RuntimeError(f"A03 package ids sha mismatch {ids_sha} != {pkg.get('ids_sha256')}")

    comp_file = {int(x["match_id"]): x["competition_file"] for x in manifest}
    matches = [
        json.loads(line)
        for line in z.read("matches.jsonl").decode().splitlines()
        if line
    ]
    events = {
        int(Path(name).stem): json.loads(z.read(name))
        for name in z.namelist()
        if name.startswith("events/") and name.endswith(".json")
    }
    match_ids = {int(x["wyId"]) for x in matches}
    manifest_ids = {int(x["match_id"]) for x in manifest}
    if len(matches) != 400 or match_ids != manifest_ids:
        raise RuntimeError("A03 match payload coverage mismatch")
    if len(events) != 400 or set(events) != manifest_ids:
        raise RuntimeError("A03 event payload coverage mismatch")
    return comp_file, matches, events, ids_sha, [int(x) for x in ids]


def _expected_a03_ids_from_raw_matches(matches_zip: Path) -> list[int]:
    z = zipfile.ZipFile(matches_zip)
    ranked = []
    for name in z.namelist():
        if not name.startswith("matches_") or not name.endswith(".json"):
            continue
        for match in json.loads(z.read(name)):
            match_id = int(match["wyId"])
            selection_sha = hashlib.sha256(f"{SEED}|{match_id}".encode()).hexdigest()
            ranked.append((selection_sha, match_id))
    ranked.sort(key=lambda x: x[0])
    if len(ranked) < A03_STOP:
        raise RuntimeError(f"raw source universe too small {len(ranked)}")
    return [match_id for _, match_id in ranked[A03_START:A03_STOP]]


def _merge_three(evaluator, a01: Path, a02: Path, a03: Path, matches_zip: Path):
    cf1, m1, e1 = evaluator._load_package(a01, "A01", evaluator.A01_IDS_SHA)
    cf2, m2, e2 = evaluator._load_package(a02, "A02", evaluator.A02_IDS_SHA)
    cf3, m3, e3, a03_ids_sha, a03_ids_ordered = _load_a03(a03)
    expected_a03_ids = _expected_a03_ids_from_raw_matches(matches_zip)
    if a03_ids_ordered != expected_a03_ids:
        raise RuntimeError("A03 ids do not equal frozen global ranks 801-1200")

    ids1 = {int(x["wyId"]) for x in m1}
    ids2 = {int(x["wyId"]) for x in m2}
    ids3 = {int(x["wyId"]) for x in m3}
    if ids1 & ids2 or ids1 & ids3 or ids2 & ids3:
        raise RuntimeError("A01/A02/A03 overlap")
    if len(ids1 | ids2 | ids3) != 1200:
        raise RuntimeError("A01+A02+A03 union count mismatch")

    comp_file = {**cf1, **cf2, **cf3}
    events = {**e1, **e2, **e3}
    matches = m1 + m2 + m3
    union_sha = hashlib.sha256(
        ("\n".join(map(str, sorted(ids1 | ids2 | ids3))) + "\n").encode()
    ).hexdigest()
    return comp_file, matches, events, union_sha, a03_ids_sha


def _pair_rows(frame: pd.DataFrame, meta: list[dict]) -> pd.DataFrame:
    if not meta:
        return frame.iloc[0:0].copy()
    lookup = frame.set_index("match_id", drop=False)
    rows = []
    for pair in meta:
        for role, key in (("D", "draw_match_id"), ("OW", "onegoal_match_id")):
            match_id = int(pair[key])
            row = lookup.loc[match_id]
            if isinstance(row, pd.DataFrame):
                raise RuntimeError(f"duplicate match id in frame {match_id}")
            item = row.to_dict()
            item["pair_id"] = pair["pair_id"]
            item["pair_role"] = role
            rows.append(item)
    return pd.DataFrame(rows)


def _pair_balance(meta: list[dict]) -> dict:
    if not meta:
        return {
            "mean_abs_q_draw_cond_diff": None,
            "mean_abs_ha_gap_diff": None,
            "mean_abs_lambda_total_diff": None,
            "mean_distance": None,
        }
    return {
        "mean_abs_q_draw_cond_diff": float(np.mean([x["abs_q_draw_cond_diff"] for x in meta])),
        "mean_abs_ha_gap_diff": float(np.mean([x["abs_ha_gap_diff"] for x in meta])),
        "mean_abs_lambda_total_diff": float(np.mean([x["abs_lambda_total_diff"] for x in meta])),
        "mean_distance": float(np.mean([x["distance"] for x in meta])),
    }


def run(a01: Path, a02: Path, a03: Path, matches_zip: Path, out: Path) -> None:
    evaluator = r1run._load_evaluator()
    evaluator._is_goal = r1run._score_event
    calipers = dict(evaluator.MATCH_CALIPERS)

    comp_file, matches, events, union_sha, a03_ids_sha = _merge_three(
        evaluator, a01, a02, a03, matches_zip
    )
    rows, skipped_nonregular = evaluator._build_rows(comp_file, matches, events)
    eligible = rows[
        (rows["hn"] >= evaluator.MIN_PRIOR_TEAM_MATCHES)
        & (rows["an"] >= evaluator.MIN_PRIOR_TEAM_MATCHES)
        & (rows["target"].isin(["D", "OW"]))
    ].copy()
    eligible = eligible.sort_values(["dt", "match_id"]).reset_index(drop=True)

    source = {
        "packages": ["A01", "A02", "A03"],
        "source_matches": 1200,
        "union_ids_sha256_sorted": union_sha,
        "a03_ids_sha256_ordered": a03_ids_sha,
        "regular_matches": int(len(rows)),
        "skipped_nonregular_matches": int(skipped_nonregular),
        "eligible_rows": int(len(eligible)),
        "eligible_draws": int((eligible["target"] == "D").sum()),
        "eligible_onegoal_wins": int((eligible["target"] == "OW").sum()),
    }

    fold_results: dict[str, dict] = {}
    coverage_failures: list[str] = []
    prepared: dict[str, tuple[pd.DataFrame, pd.DataFrame]] = {}

    for name, (train_end_s, test_start_s, test_end_s) in FIXED_FOLDS.items():
        train_end = pd.Timestamp(train_end_s).date()
        test_start = pd.Timestamp(test_start_s).date()
        test_end = pd.Timestamp(test_end_s).date() if test_end_s else None

        train = eligible[eligible["date"] < train_end].copy()
        test = eligible[eligible["date"] >= test_start].copy()
        if test_end is not None:
            test = test[test["date"] < test_end].copy()

        train_meta, train_certificate = r2._optimal_pairs(
            train, f"{name}-r3-train", calipers
        )
        test_meta, test_certificate = r2._optimal_pairs(
            test, f"{name}-r3-test", calipers
        )
        train_pairs = _pair_rows(train, train_meta)
        test_pairs = _pair_rows(test, test_meta)
        train_n = len(train_meta)
        test_n = len(test_meta)
        if train_n != train_certificate or test_n != test_certificate:
            raise RuntimeError(f"max-cardinality certificate mismatch {name}")

        train_pass = train_n >= evaluator.MIN_TRAIN_PAIRS
        test_pass = test_n >= evaluator.MIN_TEST_PAIRS
        if not train_pass:
            coverage_failures.append(f"{name}:train")
        if not test_pass:
            coverage_failures.append(f"{name}:test")

        fold_results[name] = {
            "train_date_max_exclusive": train_end_s,
            "test_date_min_inclusive": test_start_s,
            "test_date_max_exclusive": test_end_s,
            "train_target_rows": int(len(train)),
            "test_target_rows": int(len(test)),
            "train_draws": int((train["target"] == "D").sum()),
            "train_onegoal_wins": int((train["target"] == "OW").sum()),
            "test_draws": int((test["target"] == "D").sum()),
            "test_onegoal_wins": int((test["target"] == "OW").sum()),
            "train_pairs": int(train_n),
            "test_pairs": int(test_n),
            "train_maximum_cardinality_certificate": int(train_certificate),
            "test_maximum_cardinality_certificate": int(test_certificate),
            "train_pair_balance": _pair_balance(train_meta),
            "test_pair_balance": _pair_balance(test_meta),
            "frozen_gate": {
                "minimum_train_pairs": int(evaluator.MIN_TRAIN_PAIRS),
                "minimum_test_pairs": int(evaluator.MIN_TEST_PAIRS),
                "train_pass": bool(train_pass),
                "test_pass": bool(test_pass),
                "fold_pass": bool(train_pass and test_pass),
            },
        }
        prepared[name] = (train_pairs, test_pairs)

    boundary = {
        "post_view_development_only": True,
        "protected_samples_used": False,
        "new_A_packages_opened": ["A03"],
        "formal_weight": 0,
        "scientific_pass": False,
        "confirmation_pass": False,
        "formal_promotion_allowed": False,
    }

    if coverage_failures:
        result = {
            "schema_version": SCHEMA_VERSION,
            "status": "STOP_COVERAGE",
            "verdict": "A03_EXPANSION_STILL_INSUFFICIENT_FOR_FROZEN_MATCHED_PAIR_GATE",
            "source": source,
            "contract": {
                "fixed_fold_dates": FIXED_FOLDS,
                "exact_competition": True,
                "calipers": calipers,
                "matching_objective": "lexicographic_maximum_cardinality_then_minimum_total_distance",
                "minimum_train_pairs_each_fold": int(evaluator.MIN_TRAIN_PAIRS),
                "minimum_test_pairs_each_fold": int(evaluator.MIN_TEST_PAIRS),
            },
            "folds": fold_results,
            "coverage_failures": coverage_failures,
            "boundary": {
                **boundary,
                "model_fitting_performed": False,
                "model_scoring_performed": False,
                "feature_effect_claim_allowed": False,
            },
        }
    else:
        pooled_rows = []
        pooled_pb = []
        pooled_pc = []
        for name in FIXED_FOLDS:
            train_pairs, test_pairs = prepared[name]
            (
                p_baseline,
                p_candidate,
                metric_baseline,
                metric_candidate,
                delta,
                pair_baseline,
                pair_candidate,
            ) = evaluator._fit_fold(train_pairs, test_pairs)
            fold_results[name].update(
                {
                    "status": "EVALUATED",
                    "baseline": {**metric_baseline, "pair_accuracy": pair_baseline},
                    "candidate": {**metric_candidate, "pair_accuracy": pair_candidate},
                    "delta_candidate_minus_baseline": delta,
                    "paired_feature_differences": evaluator._paired_feature_diffs(test_pairs),
                }
            )
            pooled_rows.append(test_pairs.copy())
            pooled_pb.append(p_baseline)
            pooled_pc.append(p_candidate)

        all_test = pd.concat(pooled_rows, ignore_index=True)
        p_baseline = np.concatenate(pooled_pb)
        p_candidate = np.concatenate(pooled_pc)
        metric_baseline = evaluator._metric(all_test["y"].to_numpy(int), p_baseline)
        metric_candidate = evaluator._metric(all_test["y"].to_numpy(int), p_candidate)
        pair_baseline = evaluator._pair_accuracy(all_test, p_baseline)
        pair_candidate = evaluator._pair_accuracy(all_test, p_candidate)
        bootstrap = evaluator._bootstrap_pair_delta(all_test, p_baseline, p_candidate)
        fold_logloss_wins = sum(
            1
            for value in fold_results.values()
            if value["delta_candidate_minus_baseline"]["log_loss"] < 0
        )
        delta = {
            "log_loss": metric_candidate["log_loss"] - metric_baseline["log_loss"],
            "brier": metric_candidate["brier"] - metric_baseline["brier"],
            "auc": metric_candidate["auc"] - metric_baseline["auc"],
            "accuracy": metric_candidate["accuracy"] - metric_baseline["accuracy"],
            "pair_accuracy": pair_candidate - pair_baseline,
        }
        signal = (
            delta["log_loss"] < 0
            and bootstrap["ci90_high"] < 0
            and fold_logloss_wins >= 2
            and delta["brier"] <= 0
            and pair_candidate > pair_baseline
        )
        result = {
            "schema_version": SCHEMA_VERSION,
            "status": "POSTVIEW_A03_DEVELOPMENT_COMPLETE",
            "verdict": (
                "A03_MATCHED_PAIR_STATE_DYNAMICS_DEVELOPMENT_SIGNAL"
                if signal
                else "A03_MATCHED_PAIR_STATE_DYNAMICS_STABLE_INCREMENT_NOT_ESTABLISHED"
            ),
            "source": source,
            "contract": {
                "fixed_fold_dates": FIXED_FOLDS,
                "exact_competition": True,
                "calipers": calipers,
                "matching_objective": "lexicographic_maximum_cardinality_then_minimum_total_distance",
                "minimum_train_pairs_each_fold": int(evaluator.MIN_TRAIN_PAIRS),
                "minimum_test_pairs_each_fold": int(evaluator.MIN_TEST_PAIRS),
                "baseline_features": evaluator.BASELINE_FEATURES,
                "candidate_features": evaluator.CANDIDATE_FEATURES,
                "logistic_C": evaluator.LOGISTIC_C,
                "bootstrap_reps": evaluator.BOOTSTRAP_REPS,
                "bootstrap_seed": evaluator.BOOTSTRAP_SEED,
            },
            "folds": fold_results,
            "coverage_failures": [],
            "pooled": {
                "test_rows": int(len(all_test)),
                "test_pairs": int(all_test["pair_id"].nunique()),
                "baseline": {**metric_baseline, "pair_accuracy": pair_baseline},
                "candidate": {**metric_candidate, "pair_accuracy": pair_candidate},
                "delta_candidate_minus_baseline": delta,
                "fold_logloss_wins": int(fold_logloss_wins),
                "bootstrap_logloss_delta": bootstrap,
                "paired_feature_differences": evaluator._paired_feature_diffs(all_test),
                "development_signal": bool(signal),
            },
            "boundary": {
                **boundary,
                "model_fitting_performed": True,
                "model_scoring_performed": True,
                "feature_effect_claim_allowed": True,
                "claim_scope": "post-view development only; not scientific confirmation or formal promotion",
            },
        }

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--a01", required=True)
    parser.add_argument("--a02", required=True)
    parser.add_argument("--a03", required=True)
    parser.add_argument("--matches-zip", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    run(
        Path(args.a01),
        Path(args.a02),
        Path(args.a03),
        Path(args.matches_zip),
        Path(args.out),
    )
