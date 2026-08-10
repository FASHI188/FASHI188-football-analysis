#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import random
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_sha(obj) -> str:
    return sha_text(json.dumps(obj, sort_keys=True, separators=(",", ":")))


def load_parent(path: Path):
    spec = importlib.util.spec_from_file_location("r39c_parent", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import frozen R39C parent")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def outcome(hg: int, ag: int) -> int:
    return 0 if hg > ag else 1 if hg == ag else 2


def policy_decision(q1, p_draw: float, threshold: float) -> int:
    if p_draw >= threshold:
        return 1
    return 0 if float(q1[0]) >= float(q1[2]) else 2


def decision_metrics(decisions, actual):
    n = len(actual)
    hits = sum(int(d == y) for d, y in zip(decisions, actual))
    draw_pred = sum(int(d == 1) for d in decisions)
    actual_draw = sum(int(y == 1) for y in actual)
    tp = sum(int(d == 1 and y == 1) for d, y in zip(decisions, actual))
    precision = tp / draw_pred if draw_pred else 0.0
    recall = tp / actual_draw if actual_draw else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "rows": n,
        "hits": hits,
        "accuracy": hits / n if n else 0.0,
        "predicted_draw_count": draw_pred,
        "actual_draw_count": actual_draw,
        "draw_true_positive": tp,
        "draw_precision": precision,
        "draw_recall": recall,
        "draw_f1": f1,
    }


def choose_threshold(policy_rows, p_draw, labels):
    q1s = [x["q1"] for x in policy_rows]
    actual = [outcome(*labels[x["identity"]]) for x in policy_rows]
    market_dec = [int(np.argmax(np.asarray(q, dtype=float))) for q in q1s]
    market = decision_metrics(market_dec, actual)

    thresholds = sorted(set(float(x) for x in p_draw), reverse=True)
    candidates = []
    for threshold in thresholds:
        dec = [policy_decision(q, p, threshold) for q, p in zip(q1s, p_draw)]
        m = decision_metrics(dec, actual)
        if m["predicted_draw_count"] < 1:
            continue
        if m["accuracy"] + 1e-15 < market["accuracy"]:
            continue
        if m["draw_f1"] <= 0:
            continue
        candidates.append((threshold, m))

    if not candidates:
        return None, market, {
            "candidate_threshold_count": len(thresholds),
            "feasible_threshold_count": 0,
            "selection_rule": "max draw_f1 subject to full 1X2 accuracy >= market; ties accuracy, precision, fewer draws, higher threshold",
        }

    candidates.sort(
        key=lambda item: (
            item[1]["draw_f1"],
            item[1]["accuracy"],
            item[1]["draw_precision"],
            -item[1]["predicted_draw_count"],
            item[0],
        ),
        reverse=True,
    )
    threshold, chosen = candidates[0]
    return threshold, market, {
        "candidate_threshold_count": len(thresholds),
        "feasible_threshold_count": len(candidates),
        "selection_rule": "max draw_f1 subject to full 1X2 accuracy >= market; ties accuracy, precision, fewer draws, higher threshold",
        "selected_policy_metrics": chosen,
    }


def cluster_bootstrap_accuracy(dates, base_dec, cand_dec, actual, samples: int, seed: int):
    groups = defaultdict(list)
    for i, d in enumerate(dates):
        groups[d].append(i)
    keys = sorted(groups)
    rng = random.Random(seed)
    diffs = []
    for _ in range(samples):
        chosen = [keys[rng.randrange(len(keys))] for _j in range(len(keys))]
        idx = [i for k in chosen for i in groups[k]]
        if not idx:
            continue
        b = sum(int(base_dec[i] == actual[i]) for i in idx) / len(idx)
        c = sum(int(cand_dec[i] == actual[i]) for i in idx) / len(idx)
        diffs.append(c - b)
    diffs.sort()

    def q(frac):
        pos = min(len(diffs) - 1, max(0, int(frac * (len(diffs) - 1))))
        return diffs[pos]

    return {
        "method": "match_date_cluster_bootstrap",
        "clusters": len(keys),
        "samples": len(diffs),
        "seed": seed,
        "mean": sum(diffs) / len(diffs),
        "p05": q(0.05),
        "p50": q(0.50),
        "p95": q(0.95),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prereg", type=Path, required=True)
    ap.add_argument("--sanitized-dir", type=Path, required=True)
    ap.add_argument("--original-dir", type=Path, required=True)
    ap.add_argument("--parent-code", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    pre = json.loads(args.prereg.read_text(encoding="utf-8"))
    assert pre["status"] == "PRE_REGISTERED_BEFORE_R39D_HOLDOUT_LABEL_ACCESS"
    assert pre["hard_limits"]["formal_weight"] == 0
    parent = load_parent(args.parent_code)
    rows, _max_dt = parent.load_feature_rows(args.sanitized_dir, pre)

    holdout_start = datetime.fromisoformat(pre["identity_binding"]["holdout_start"])
    policy_start = holdout_start - timedelta(days=pre["policy_selection"]["policy_window_days"])
    training = [x for x in rows.values() if x["dt"] < holdout_start]
    holdout = [x for x in rows.values() if x["dt"] >= holdout_start]
    ordered = sorted(holdout, key=lambda x: sha_text(f'51139|{x["identity"]}'))
    old100 = ordered[:100]
    test100 = ordered[100:200]

    old_sha = sha_text("\n".join(sorted(x["identity"] for x in old100)) + "\n")
    test_sha = sha_text("\n".join(sorted(x["identity"] for x in test100)) + "\n")
    ib = pre["identity_binding"]
    assert len(training) == ib["training_eligible_rows"]
    assert len(holdout) == ib["holdout_eligible_rows"]
    assert old_sha == ib["excluded_r39c_identity_set_sha256"]
    assert test_sha == ib["identity_set_sha256"]
    assert not ({x["identity"] for x in old100} & {x["identity"] for x in test100})

    all_training_ids = {x["identity"] for x in training}
    labels, train_access, holdout_before = parent.read_training_labels(
        args.original_dir, all_training_ids, holdout_start
    )
    assert holdout_before == 0
    assert train_access == len(training) == len(labels)

    dev = sorted(
        [x for x in training if x["dt"] < policy_start],
        key=lambda z: (z["dt"], z["identity"]),
    )
    policy = sorted(
        [x for x in training if policy_start <= x["dt"] < holdout_start],
        key=lambda z: (z["dt"], z["identity"]),
    )
    assert dev and policy
    assert max(x["dt"] for x in dev) < min(x["dt"] for x in policy)
    assert max(x["dt"] for x in policy) < holdout_start

    X_dev = np.asarray([x["features"] for x in dev], dtype=float)
    y_dev = np.asarray(
        [1 if labels[x["identity"]][0] == labels[x["identity"]][1] else 0 for x in dev],
        dtype=float,
    )
    Xd, dev_mean, dev_std = parent.standardize(X_dev)
    beta_dev, diag_dev = parent.fit_logistic(
        Xd,
        y_dev,
        l2=pre["probability_model"]["l2_lambda"],
        max_iter=pre["probability_model"]["maximum_iterations"],
        tol=pre["probability_model"]["coefficient_delta_tolerance"],
    )
    X_policy = np.asarray([x["features"] for x in policy], dtype=float)
    pp = parent.predict_logistic((X_policy - dev_mean) / dev_std, beta_dev)
    threshold, policy_market, threshold_diag = choose_threshold(policy, pp, labels)

    policy_receipt = {
        "schema": "r39d-policy-selection-freeze",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "policy_start": policy_start.isoformat(),
        "holdout_start": holdout_start.isoformat(),
        "development_rows": len(dev),
        "development_draws": int(y_dev.sum()),
        "policy_rows": len(policy),
        "policy_draws": sum(
            int(labels[x["identity"]][0] == labels[x["identity"]][1]) for x in policy
        ),
        "r39c_old100_labels_accessed_for_policy": 0,
        "r39d_holdout_labels_accessed_before_policy_freeze": 0,
        "development_model_diagnostics": diag_dev,
        "market_policy_metrics": policy_market,
        "threshold_selection": threshold_diag,
        "selected_threshold": threshold,
    }
    policy_receipt["policy_receipt_sha256"] = canonical_sha(policy_receipt)
    (args.out_dir / "policy_selection_freeze_r39d.json").write_text(
        json.dumps(policy_receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if threshold is None:
        result = {
            "status": "STOP_R39D_NO_FEASIBLE_DECISION_POLICY_BEFORE_HOLDOUT_LABELS",
            "identity_set_sha256": test_sha,
            "policy_receipt_sha256": policy_receipt["policy_receipt_sha256"],
            "training_labels_accessed": train_access,
            "r39c_old100_holdout_labels_accessed": 0,
            "r39d_holdout_labels_accessed": 0,
            "formal_weight": 0,
        }
        (args.out_dir / "result_r39d.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    X_all = np.asarray([x["features"] for x in training], dtype=float)
    y_all = np.asarray(
        [1 if labels[x["identity"]][0] == labels[x["identity"]][1] else 0 for x in training],
        dtype=float,
    )
    Xa, mean, std = parent.standardize(X_all)
    beta, diag = parent.fit_logistic(
        Xa,
        y_all,
        l2=pre["probability_model"]["l2_lambda"],
        max_iter=pre["probability_model"]["maximum_iterations"],
        tol=pre["probability_model"]["coefficient_delta_tolerance"],
    )
    freeze = {
        "schema": "r39d-final-model-policy-freeze",
        "identity_set_sha256": test_sha,
        "excluded_r39c_identity_set_sha256": old_sha,
        "training_rows": len(training),
        "training_draws": int(y_all.sum()),
        "holdout_labels_accessed_before_freeze": 0,
        "selected_threshold": threshold,
        "policy_receipt_sha256": policy_receipt["policy_receipt_sha256"],
        "feature_names": pre["features"],
        "training_mean": mean.tolist(),
        "training_std": std.tolist(),
        "trajectory_beta": beta.tolist(),
        "trajectory_diagnostics": diag,
        "numpy_version": np.__version__,
    }
    freeze["model_policy_parameter_sha256"] = canonical_sha(freeze)
    freeze_path = args.out_dir / "model_policy_freeze_r39d.json"
    freeze_path.write_text(json.dumps(freeze, ensure_ascii=False, indent=2), encoding="utf-8")
    assert freeze_path.exists() and freeze_path.stat().st_size > 0

    test_ids = {x["identity"] for x in test100}
    test_labels, test_access = parent.read_test_labels_after_freeze(args.original_dir, test_ids)
    assert test_access == len(test_labels) == 100

    ts = sorted(test100, key=lambda z: (z["dt"], z["identity"]))
    Xt = np.asarray([x["features"] for x in ts], dtype=float)
    pd = parent.predict_logistic((Xt - mean) / std, beta)
    baseline_probs = [np.asarray(x["q1"], dtype=float) for x in ts]
    traj_probs = [parent.threeway(x["q1"], p) for x, p in zip(ts, pd)]
    actual = [outcome(*test_labels[x["identity"]]) for x in ts]
    market_dec = [int(np.argmax(p)) for p in baseline_probs]
    default_traj_dec = [int(np.argmax(p)) for p in traj_probs]
    policy_dec = [policy_decision(x["q1"], p, threshold) for x, p in zip(ts, pd)]

    mb_prob = parent.metrics(baseline_probs, actual)
    mt_prob = parent.metrics(traj_probs, actual)
    mb_dec = decision_metrics(market_dec, actual)
    md_dec = decision_metrics(default_traj_dec, actual)
    mp_dec = decision_metrics(policy_dec, actual)
    auc_b = parent.auc_draw(baseline_probs, actual)
    auc_t = parent.auc_draw(traj_probs, actual)
    dates = [x["dt"].date().isoformat() for x in ts]
    boot = cluster_bootstrap_accuracy(
        dates,
        market_dec,
        policy_dec,
        actual,
        pre["evaluation"]["cluster_bootstrap"]["samples"],
        pre["evaluation"]["cluster_bootstrap"]["seed"],
    )
    discordant = {
        "policy_correct_market_wrong": sum(
            int(c == y and b != y) for b, c, y in zip(market_dec, policy_dec, actual)
        ),
        "market_correct_policy_wrong": sum(
            int(b == y and c != y) for b, c, y in zip(market_dec, policy_dec, actual)
        ),
    }

    gate = {
        "policy_predicts_at_least_one_draw": mp_dec["predicted_draw_count"] >= 1,
        "policy_draw_f1_strictly_better_than_market": mp_dec["draw_f1"] > mb_dec["draw_f1"],
        "policy_accuracy_strictly_better_than_market": mp_dec["accuracy"] > mb_dec["accuracy"],
        "trajectory_log_loss_nonworse_than_market": mt_prob["log_loss"] <= mb_prob["log_loss"] + 1e-12,
        "trajectory_brier_nonworse_than_market": mt_prob["brier"] <= mb_prob["brier"] + 1e-12,
        "trajectory_rps_nonworse_than_market": mt_prob["rps"] <= mb_prob["rps"] + 1e-12,
        "trajectory_draw_auc_strictly_better_than_market": (
            auc_t is not None and auc_b is not None and auc_t > auc_b
        ),
        "cluster_bootstrap_accuracy_delta_p05_positive": boot["p05"] > 0,
    }
    gate["passed"] = all(gate.values())
    status = (
        "PASS_R39D_DECISION_BOUNDARY_CHALLENGER_EXPLORATION_ONLY"
        if gate["passed"]
        else "NO_R39D_DECISION_BOUNDARY_INCREMENT_FIXED100_EXPLORATION_ONLY"
    )

    rows_out = []
    for x, pb, pt, y, db, dd, dp in zip(
        ts, baseline_probs, traj_probs, actual, market_dec, default_traj_dec, policy_dec
    ):
        rows_out.append(
            {
                "identity": x["identity"],
                "match_datetime": x["dt"].isoformat(),
                "actual": ["H", "D", "A"][y],
                "market_probability": [float(v) for v in pb],
                "trajectory_probability": [float(v) for v in pt],
                "market_top1": ["H", "D", "A"][db],
                "trajectory_default_top1": ["H", "D", "A"][dd],
                "r39d_policy_top1": ["H", "D", "A"][dp],
            }
        )
    (args.out_dir / "per_match_r39d.json").write_text(
        json.dumps(rows_out, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    result = {
        "schema_version": pre["schema_version"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "identity_set_sha256": test_sha,
        "excluded_r39c_identity_set_sha256": old_sha,
        "old_new_overlap": 0,
        "policy_threshold": threshold,
        "policy_receipt_sha256": policy_receipt["policy_receipt_sha256"],
        "model_policy_parameter_sha256": freeze["model_policy_parameter_sha256"],
        "access_audit": {
            "training_labels_accessed": train_access,
            "holdout_labels_accessed_before_policy_freeze": 0,
            "holdout_labels_accessed_before_model_policy_freeze": 0,
            "r39c_old100_holdout_score_values_accessed": 0,
            "r39d_new100_holdout_score_values_accessed_after_freeze": test_access,
        },
        "actual_counts": {
            "H": sum(y == 0 for y in actual),
            "D": sum(y == 1 for y in actual),
            "A": sum(y == 2 for y in actual),
        },
        "probability_metrics": {
            "market": {**mb_prob, "draw_auc": auc_b},
            "trajectory": {**mt_prob, "draw_auc": auc_t},
        },
        "decision_metrics": {
            "market_argmax": mb_dec,
            "trajectory_default_argmax": md_dec,
            "r39d_two_stage_policy": mp_dec,
        },
        "paired_discordance": discordant,
        "cluster_bootstrap_accuracy_delta_policy_minus_market": boot,
        "challenge_gate": gate,
        "hard_limits": pre["hard_limits"],
    }
    (args.out_dir / "result_r39d.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
