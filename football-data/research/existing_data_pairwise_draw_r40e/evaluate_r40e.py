#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

R39W_PATH = Path("football-data/research/existing_data_balance_intensity_r39w/evaluate_r39w.py")
R39W_EXPECTED_BLOB = "4abe6918ec9fd32e65d7b6eb2d74686238e0f1ae"

SEEDS = {
    "r39u": "R39U_FIXED100_20260810",
    "r39v": "R39V_DISJOINT_FIXED100_20260810",
    "r39w": "R39W_BALANCE_INTENSITY_FIXED100_20260810",
    "r39x": "R39X_NONLINEAR_REGIME_FIXED100_20260810",
    "r39y": "R39Y_REGIME_REPLICATION_FIXED100_20260810",
    "r39z": "R39Z_ORDINAL_BAND_FIXED100_20260810",
    "r40a": "R40A_VARYING_ORDINAL_FIXED100_20260810",
    "r40b": "R40B_TEAM_DRAW_STATE_FIXED100_20260810",
    "r40d": "R40D_SCORE_COMPRESSION_FIXED100_20260810",
}
EXPECTED_SHA = {
    "r39u": "dad7317511e2dd080d82e2f7bfc68590c369a01844e8e434625e0d0b135c1ce6",
    "r39v": "600cf1c9e7815f436bfa19739c4a399ae42f32e55c03fecab6f88ad02001d81b",
    "r39w": "df64b2999f447a1b42a69326ca5db3e4132af624d14d147deda06042b6d3f5c8",
    "r39x": "5de03fc4d0e52fda74c10dd8430cbe0a2f3db43fbd97529608222083d70270ba",
    "r39y": "de0c755f6e828a3778d8da9e830bba988ade7ebb347b208f20260872c4fdbc57",
    "r39z": "ffb26e35e1f98196cfb2842a559b0d22380a4b89165889351dfbf6f99aaf61a2",
    "r40a": "64d7c671987dac7bdd92c1eb76370e28c62f9157c9071861af33d59a0f23d7b2",
    "r40b": "f6c2d7ae058d5a9955f0f80fecdf18bbbed519499cd433ead20731e9f0e8500e",
    "r40d": "90cf4729ff433fa45e59eb070d6abbb8b8f1402e94c9ac680f21e7873a3a28aa",
}
LABELS = ("H", "D", "A")
TIE_PRIORITY = {"A": 2, "H": 1, "D": 0}


def load_r39w_module():
    got = subprocess.check_output(["git", "rev-parse", f"HEAD:{R39W_PATH.as_posix()}"], text=True).strip()
    if got != R39W_EXPECTED_BLOB:
        raise RuntimeError(f"R39W_EVALUATOR_BLOB_DRIFT:{got}:{R39W_EXPECTED_BLOB}")
    spec = importlib.util.spec_from_file_location("r39w_eval_for_r40e", R39W_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("R39W_IMPORT_SPEC_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def verify_sample(u, rows, name: str) -> list[str]:
    keys = [t.key for t, _ in rows]
    got = u.sample_sha(keys)
    want = EXPECTED_SHA[name]
    if got != want:
        raise RuntimeError(f"{name.upper()}_SAMPLE_IDENTITY_DRIFT:{got}:{want}")
    return keys


def classification_metrics(records: list[dict]) -> dict:
    n = len(records)
    pred = Counter(r["prediction"] for r in records)
    actual = Counter(r["actual"] for r in records)
    hits = sum(r["prediction"] == r["actual"] for r in records)
    per_class = {}
    f1_values = []
    for lab in LABELS:
        tp = sum(r["prediction"] == lab and r["actual"] == lab for r in records)
        fp = sum(r["prediction"] == lab and r["actual"] != lab for r in records)
        fn = sum(r["prediction"] != lab and r["actual"] == lab for r in records)
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        f1 = None
        if precision is not None and recall is not None and precision + recall > 0:
            f1 = 2.0 * precision * recall / (precision + recall)
        f1_values.append(f1 if f1 is not None else 0.0)
        per_class[lab] = {
            "tp": tp, "fp": fp, "fn": fn,
            "precision": precision, "recall": recall, "f1": f1,
        }
    return {
        "n": n,
        "hits": hits,
        "accuracy": hits / n,
        "predicted_counts": {lab: pred.get(lab, 0) for lab in LABELS},
        "actual_counts": {lab: actual.get(lab, 0) for lab in LABELS},
        "per_class": per_class,
        "draw_precision": per_class["D"]["precision"],
        "draw_recall": per_class["D"]["recall"],
        "draw_f1": per_class["D"]["f1"],
        "macro_f1": sum(f1_values) / len(f1_values),
    }


def pair_prob(w, u, prior, target, allowed: tuple[str, str], positive: str, l2: float):
    train = [r for r in prior if r.label in allowed]
    if len(train) < 50:
        raise RuntimeError(f"INSUFFICIENT_PAIRWISE_TRAIN:{target.key}:{allowed}:{len(train)}")
    labels_present = {r.label for r in train}
    if labels_present != set(allowed):
        raise RuntimeError(f"PAIRWISE_CLASS_MISSING:{target.key}:{allowed}:{labels_present}")
    X, xt = w.standardize([r.x for r in train], target.x, u.quantile)
    y = [1 if r.label == positive else 0 for r in train]
    beta, iters = w.fit_logistic(X, y, l2)
    return w.predict_prob(beta, xt), len(train), iters


def combine_baseline(w, u, prior, target):
    Xd, xd = w.standardize([w.draw_features(r.x) for r in prior], w.draw_features(target.x), u.quantile)
    yd = [1 if r.label == "D" else 0 for r in prior]
    bd, draw_iters = w.fit_logistic(Xd, yd, 10.0)
    pD = w.predict_prob(bd, xd)
    nd = [r for r in prior if r.label != "D"]
    Xs, xs = w.standardize([w.side_features(r.x) for r in nd], w.side_features(target.x), u.quantile)
    ys = [1 if r.label == "H" else 0 for r in nd]
    bs, side_iters = w.fit_logistic(Xs, ys, 10.0)
    pHnd = w.predict_prob(bs, xs)
    p = {"D": pD, "H": (1.0-pD)*pHnd, "A": (1.0-pD)*(1.0-pHnd)}
    return p, draw_iters, side_iters


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prereg", type=Path, required=True)
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    pre = json.loads(args.prereg.read_text(encoding="utf-8"))
    assert pre["schema_version"] == "R40E-EXISTING-DATA-PAIRWISE-DRAW-1.0"
    alg = pre["algorithm"]
    assert float(alg["l2"]) == 10.0
    assert alg["probabilistic_claim"] is False
    assert alg["manual_draw_boost"] is False
    assert alg["class_weighting"] is False
    assert alg["candidate_grid"] is False
    assert alg["threshold_tuning"] is False
    assert alg["post_result_tuning"] is False
    assert alg["abstention"] is False
    hard = pre["hard_boundaries"]
    assert hard["external_network_requests"] == 0 and hard["football_api_requests"] == 0
    assert hard["new_data_collection"] is False

    w = load_r39w_module()
    u = w.load_r39u_module()
    rows = u.load_rows(args.root)
    min_prior = int(pre["sample"]["minimum_strictly_prior_same_competition_rows"])
    by_comp, eligible = w.build_eligible(u, rows, min_prior)

    excluded: set[str] = set()
    consumed: dict[str, list[str]] = {}
    for name in ("r39u", "r39v", "r39w", "r39x", "r39y", "r39z", "r40a", "r40b", "r40d"):
        old = w.hashed_sample(eligible, SEEDS[name], 100, excluded)
        keys = verify_sample(u, old, name)
        consumed[name] = keys
        excluded.update(keys)

    sample = w.hashed_sample(eligible, str(pre["sample"]["seed"]), int(pre["sample"]["size"]), excluded)
    if len(sample) != int(pre["sample"]["size"]):
        raise RuntimeError("INSUFFICIENT_R40E_SAMPLE")
    sample_keys = [t.key for t, _ in sample]
    overlaps = {name: len(set(sample_keys) & set(keys)) for name, keys in consumed.items()}
    if any(overlaps.values()):
        raise RuntimeError(f"R40E_OVERLAP_INVALID:{overlaps}")

    pairwise_records = []
    baseline_records = []
    audit = []
    l2 = float(alg["l2"])
    for t, _ in sample:
        prior = [r for r in by_comp[t.competition] if r.dt < t.dt and u.finite_vec(r.x)]
        if len(prior) < min_prior:
            raise RuntimeError(f"PRIOR_POOL_DRIFT:{t.key}:{len(prior)}")

        pH_HD, nHD, itHD = pair_prob(w, u, prior, t, ("H", "D"), "H", l2)
        pD_DA, nDA, itDA = pair_prob(w, u, prior, t, ("D", "A"), "D", l2)
        pH_HA, nHA, itHA = pair_prob(w, u, prior, t, ("H", "A"), "H", l2)

        scores = {
            "H": pH_HD + pH_HA,
            "D": (1.0 - pH_HD) + pD_DA,
            "A": (1.0 - pD_DA) + (1.0 - pH_HA),
        }
        if abs(sum(scores.values()) - 3.0) > 1e-10:
            raise RuntimeError(f"PAIRWISE_SCORE_SUM_FAIL:{t.key}:{sum(scores.values())}")
        pred = max(LABELS, key=lambda lab: (scores[lab], TIE_PRIORITY[lab]))
        pairwise_records.append({
            "key": t.key, "competition": t.competition, "season": t.season,
            "date": t.dt.isoformat(), "home": t.home, "away": t.away,
            "actual": t.label, "prediction": pred,
            "pairwise_scores": scores,
            "p_H_given_HD": pH_HD,
            "p_D_given_DA": pD_DA,
            "p_H_given_HA": pH_HA,
            "strictly_prior_pool_n": len(prior),
        })

        pb, bitd, bits = combine_baseline(w, u, prior, t)
        baseline_records.append({
            "key": t.key, "competition": t.competition, "season": t.season,
            "date": t.dt.isoformat(), "home": t.home, "away": t.away,
            "actual": t.label, "prediction": u.choose(pb),
            "probabilities": pb,
        })
        audit.append({
            "key": t.key,
            "pair_train_n": {"HD": nHD, "DA": nDA, "HA": nHA},
            "pair_iterations": {"HD": itHD, "DA": itDA, "HA": itHA},
            "pair_probabilities": {"p_H_given_HD": pH_HD, "p_D_given_DA": pD_DA, "p_H_given_HA": pH_HA},
            "pairwise_scores": scores,
            "pairwise_score_sum": sum(scores.values()),
            "baseline_iterations": {"draw": bitd, "side": bits},
        })

    pm = classification_metrics(pairwise_records)
    bm = classification_metrics(baseline_records)
    pf1 = pm["draw_f1"] if pm["draw_f1"] is not None else -1.0
    bf1 = bm["draw_f1"] if bm["draw_f1"] is not None else -1.0
    pp = pm["draw_precision"] if pm["draw_precision"] is not None else -1.0
    pr = pm["draw_recall"] if pm["draw_recall"] is not None else -1.0
    gate = {
        "draw_precision_ge_0_30": pp >= 0.30,
        "draw_recall_ge_0_30": pr >= 0.30,
        "draw_f1_ge_0_30": pf1 >= 0.30,
        "draw_f1_plus_0_10_vs_baseline": pf1 >= bf1 + 0.10,
        "accuracy_noninferior_within_0_03": pm["accuracy"] >= bm["accuracy"] - 0.03,
        "macro_f1_plus_0_02_vs_baseline": pm["macro_f1"] >= bm["macro_f1"] + 0.02,
    }
    passed = all(gate.values())
    terminal = "PASS_R40E_PAIRWISE_DRAW_GEOMETRY_INCREMENT" if passed else "FAIL_R40E_PAIRWISE_DRAW_GEOMETRY_NO_INCREMENT"

    out = {
        "schema_version": pre["schema_version"], "terminal": terminal, "passed": passed,
        "source_rows": len(rows), "eligible_rows": len(eligible),
        "consumed_sample_identities": {name: u.sample_sha(keys) for name, keys in consumed.items()},
        "r40e_fixed100_identity_sha256": u.sample_sha(sample_keys), "overlap": overlaps,
        "sample_keys": sample_keys,
        "pairwise_metrics": pm, "baseline_metrics": bm,
        "gate": gate, "audit": audit,
        "pairwise_predictions": pairwise_records, "baseline_predictions": baseline_records,
        "hard_boundaries": hard,
        "interpretation_boundary": "Classification-only pairwise decision challenger; pairwise scores are not claimed as calibrated H/D/A probabilities. Even PASS authorizes only a separately preregistered coupling/rolling study; formal_weight remains 0."
    }
    args.out.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (args.out / "r40e_result.json").write_text(raw, encoding="utf-8")
    (args.out / "r40e_result.sha256").write_text(hashlib.sha256(raw.encode()).hexdigest() + "\n", encoding="ascii")
    print(json.dumps({
        "terminal": terminal, "passed": passed, "sample_sha": out["r40e_fixed100_identity_sha256"],
        "overlap": overlaps, "pairwise_metrics": pm, "baseline_metrics": bm, "gate": gate,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
