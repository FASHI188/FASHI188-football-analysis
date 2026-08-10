#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import subprocess
import sys
from collections import Counter
from pathlib import Path

R39W_PATH = Path("football-data/research/existing_data_balance_intensity_r39w/evaluate_r39w.py")
R39W_EXPECTED_BLOB = "4abe6918ec9fd32e65d7b6eb2d74686238e0f1ae"

R39U_SEED = "R39U_FIXED100_20260810"
R39V_SEED = "R39V_DISJOINT_FIXED100_20260810"
R39W_SEED = "R39W_BALANCE_INTENSITY_FIXED100_20260810"
R39X_SEED = "R39X_NONLINEAR_REGIME_FIXED100_20260810"
R39Y_SEED = "R39Y_REGIME_REPLICATION_FIXED100_20260810"
R39U_SHA = "dad7317511e2dd080d82e2f7bfc68590c369a01844e8e434625e0d0b135c1ce6"
R39V_SHA = "600cf1c9e7815f436bfa19739c4a399ae42f32e55c03fecab6f88ad02001d81b"
R39W_SHA = "df64b2999f447a1b42a69326ca5db3e4132af624d14d147deda06042b6d3f5c8"
R39X_SHA = "5de03fc4d0e52fda74c10dd8430cbe0a2f3db43fbd97529608222083d70270ba"
R39Y_SHA = "de0c755f6e828a3778d8da9e830bba988ade7ebb347b208f20260872c4fdbc57"
LABELS = ("H", "D", "A")
ENC = {"A": -1.0, "D": 0.0, "H": 1.0}


def load_r39w_module():
    got = subprocess.check_output(["git", "rev-parse", f"HEAD:{R39W_PATH.as_posix()}"], text=True).strip()
    if got != R39W_EXPECTED_BLOB:
        raise RuntimeError(f"R39W_EVALUATOR_BLOB_DRIFT:{got}:{R39W_EXPECTED_BLOB}")
    spec = importlib.util.spec_from_file_location("r39w_eval_for_r39z", R39W_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("R39W_IMPORT_SPEC_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def ridge_fit(X: list[list[float]], y: list[float], l2: float, solve_linear) -> list[float]:
    if not X or len(X) != len(y):
        raise RuntimeError("INVALID_RIDGE_INPUT")
    d = len(X[0])
    A = [[0.0] * d for _ in range(d)]
    b = [0.0] * d
    for row, yy in zip(X, y):
        for j in range(d):
            b[j] += row[j] * yy
            for k in range(j, d):
                A[j][k] += row[j] * row[k]
    for j in range(d):
        for k in range(j):
            A[j][k] = A[k][j]
        if j > 0:
            A[j][j] += l2
        A[j][j] += 1e-9
    return solve_linear(A, b)


def dot(beta: list[float], row: list[float]) -> float:
    return sum(b * x for b, x in zip(beta, row))


def classification_metrics(records: list[dict]) -> dict:
    n = len(records)
    pred = Counter(r["prediction"] for r in records)
    actual = Counter(r["actual"] for r in records)
    hits = sum(r["prediction"] == r["actual"] for r in records)
    per_class = {}
    f1s = []
    for lab in LABELS:
        tp = sum(r["prediction"] == lab and r["actual"] == lab for r in records)
        fp = sum(r["prediction"] == lab and r["actual"] != lab for r in records)
        fn = sum(r["prediction"] != lab and r["actual"] == lab for r in records)
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        f1 = None
        if precision is not None and recall is not None and precision + recall > 0:
            f1 = 2.0 * precision * recall / (precision + recall)
            f1s.append(f1)
        else:
            f1s.append(0.0)
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
        "macro_f1": sum(f1s) / len(f1s),
    }


def verify_sample(u, rows, expected_sha: str, name: str):
    keys = [t.key for t, _ in rows]
    got = u.sample_sha(keys)
    if got != expected_sha:
        raise RuntimeError(f"{name}_SAMPLE_IDENTITY_DRIFT:{got}:{expected_sha}")
    return keys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prereg", type=Path, required=True)
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    pre = json.loads(args.prereg.read_text(encoding="utf-8"))
    assert pre["schema_version"] == "R39Z-EXISTING-DATA-ORDINAL-BAND-1.0"
    a = pre["algorithm"]
    assert float(a["ridge_l2"]) == 10.0
    assert a["target_sample_threshold_tuning"] is False
    assert a["manual_draw_boost"] is False
    assert a["class_weighting"] is False
    assert a["abstention"] is False
    assert a["probabilistic_claim"] is False
    h = pre["hard_boundaries"]
    assert h["external_network_requests"] == 0 and h["football_api_requests"] == 0
    assert h["new_data_collection"] is False

    w = load_r39w_module()
    u = w.load_r39u_module()
    all_rows = u.load_rows(args.root)
    min_prior = int(pre["sample"]["minimum_strictly_prior_same_competition_rows"])
    by_comp, eligible = w.build_eligible(u, all_rows, min_prior)

    r39u = w.hashed_sample(eligible, R39U_SEED, 100, set())
    ku = verify_sample(u, r39u, R39U_SHA, "R39U")
    r39v = w.hashed_sample(eligible, R39V_SEED, 100, set(ku))
    kv = verify_sample(u, r39v, R39V_SHA, "R39V")
    uv = set(ku) | set(kv)
    r39w = w.hashed_sample(eligible, R39W_SEED, 100, uv)
    kw = verify_sample(u, r39w, R39W_SHA, "R39W")
    uvw = uv | set(kw)
    r39x = w.hashed_sample(eligible, R39X_SEED, 100, uvw)
    kx = verify_sample(u, r39x, R39X_SHA, "R39X")
    uvwx = uvw | set(kx)
    r39y = w.hashed_sample(eligible, R39Y_SEED, 100, uvwx)
    ky = verify_sample(u, r39y, R39Y_SHA, "R39Y")

    excluded = uvwx | set(ky)
    sample = w.hashed_sample(eligible, str(pre["sample"]["seed"]), int(pre["sample"]["size"]), excluded)
    if len(sample) != int(pre["sample"]["size"]):
        raise RuntimeError("INSUFFICIENT_R39Z_SAMPLE")
    sample_keys = [t.key for t, _ in sample]
    overlaps = {
        "r39u": len(set(sample_keys) & set(ku)),
        "r39v": len(set(sample_keys) & set(kv)),
        "r39w": len(set(sample_keys) & set(kw)),
        "r39x": len(set(sample_keys) & set(kx)),
        "r39y": len(set(sample_keys) & set(ky)),
    }
    if any(overlaps.values()):
        raise RuntimeError(f"R39Z_OVERLAP_INVALID:{overlaps}")

    l2 = float(a["ridge_l2"])
    ordinal_records = []
    baseline_records = []
    audit = []

    for t, _ in sample:
        prior = [r for r in by_comp[t.competition] if r.dt < t.dt and u.finite_vec(r.x)]
        if len(prior) < max(min_prior, int(pre["baseline"]["k"])):
            raise RuntimeError(f"PRIOR_POOL_DRIFT:{t.key}:{len(prior)}")

        train_raw = [w.side_features(r.x) for r in prior]
        target_raw = w.side_features(t.x)
        X, xt = w.standardize(train_raw, target_raw, u.quantile)
        y = [ENC[r.label] for r in prior]
        beta = ridge_fit(X, y, l2, w.solve_linear)
        fitted_scores = [dot(beta, row) for row in X]
        target_score = dot(beta, xt)

        count_a = sum(r.label == "A" for r in prior)
        count_d = sum(r.label == "D" for r in prior)
        n = len(prior)
        q_lower = count_a / n
        q_upper = (count_a + count_d) / n
        lower = u.quantile(fitted_scores, q_lower)
        upper = u.quantile(fitted_scores, q_upper)
        if not (math.isfinite(lower) and math.isfinite(upper)) or upper < lower:
            raise RuntimeError(f"INVALID_ORDINAL_THRESHOLDS:{t.key}:{lower}:{upper}")

        if target_score < lower:
            pred = "A"
        elif target_score > upper:
            pred = "H"
        else:
            pred = "D"

        ordinal_records.append({
            "key": t.key, "competition": t.competition, "season": t.season,
            "date": t.dt.isoformat(), "home": t.home, "away": t.away,
            "actual": t.label, "prediction": pred,
            "strictly_prior_pool_n": n,
            "latent_score": target_score,
            "lower_A_D_threshold": lower,
            "upper_D_H_threshold": upper,
            "prior_A_rate": q_lower,
            "prior_D_rate": count_d / n,
            "prior_H_rate": 1.0 - q_upper,
        })
        audit.append({
            "key": t.key, "prior_n": n,
            "prior_counts": {lab: sum(r.label == lab for r in prior) for lab in LABELS},
            "latent_beta": beta,
            "target_latent_score": target_score,
            "lower_A_D_threshold": lower,
            "upper_D_H_threshold": upper,
            "threshold_quantiles": {"lower": q_lower, "upper": q_upper},
        })

        pb = u.probs_for(t, prior, int(pre["baseline"]["k"]))
        baseline_records.append({
            "key": t.key, "competition": t.competition, "season": t.season,
            "date": t.dt.isoformat(), "home": t.home, "away": t.away,
            "actual": t.label, "prediction": u.choose(pb),
        })

    om = classification_metrics(ordinal_records)
    bm = classification_metrics(baseline_records)
    of1 = om["draw_f1"] if om["draw_f1"] is not None else -1.0
    bf1 = bm["draw_f1"] if bm["draw_f1"] is not None else -1.0
    op = om["draw_precision"] if om["draw_precision"] is not None else -1.0
    orc = om["draw_recall"] if om["draw_recall"] is not None else -1.0

    gate = {
        "draw_precision_ge_0_30": op >= 0.30,
        "draw_recall_ge_0_30": orc >= 0.30,
        "draw_f1_ge_0_30": of1 >= 0.30,
        "draw_f1_plus_0_10_vs_baseline": of1 >= bf1 + 0.10,
        "accuracy_noninferior_within_0_03": om["accuracy"] >= bm["accuracy"] - 0.03,
        "macro_f1_plus_0_02_vs_baseline": om["macro_f1"] >= bm["macro_f1"] + 0.02,
    }
    passed = all(gate.values())
    terminal = "PASS_R39Z_ORDINAL_DRAW_BAND_DECISION_INCREMENT" if passed else "FAIL_R39Z_ORDINAL_DRAW_BAND_NO_INCREMENT"

    out = {
        "schema_version": pre["schema_version"],
        "terminal": terminal,
        "passed": passed,
        "source_rows": len(all_rows),
        "eligible_rows": len(eligible),
        "consumed_sample_identities": {
            "r39u": u.sample_sha(ku), "r39v": u.sample_sha(kv), "r39w": u.sample_sha(kw),
            "r39x": u.sample_sha(kx), "r39y": u.sample_sha(ky),
        },
        "r39z_fixed100_identity_sha256": u.sample_sha(sample_keys),
        "overlap": overlaps,
        "sample_keys": sample_keys,
        "ordinal_metrics": om,
        "baseline_metrics": bm,
        "gate": gate,
        "audit": audit,
        "ordinal_predictions": ordinal_records,
        "baseline_predictions": baseline_records,
        "hard_boundaries": h,
        "interpretation_boundary": "Classification-only decision challenger. No probabilistic claim and no formal promotion even if PASS; formal_weight remains 0."
    }
    args.out.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (args.out / "r39z_result.json").write_text(raw, encoding="utf-8")
    import hashlib
    (args.out / "r39z_result.sha256").write_text(hashlib.sha256(raw.encode()).hexdigest() + "\n", encoding="ascii")
    print(json.dumps({
        "terminal": terminal, "passed": passed,
        "sample_sha": out["r39z_fixed100_identity_sha256"], "overlap": overlaps,
        "ordinal_metrics": om, "baseline_metrics": bm, "gate": gate
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
