#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path

R39U_PATH = Path("football-data/research/existing_data_analog_r39u/evaluate_analog_r39u.py")
R39U_EXPECTED_BLOB = "16bdecdd508b780318628ec58224168dfa849e99"
R39U_SEED = "R39U_FIXED100_20260810"
R39V_SEED = "R39V_DISJOINT_FIXED100_20260810"
R39U_EXPECTED_SAMPLE_SHA = "dad7317511e2dd080d82e2f7bfc68590c369a01844e8e434625e0d0b135c1ce6"
R39V_EXPECTED_SAMPLE_SHA = "600cf1c9e7815f436bfa19739c4a399ae42f32e55c03fecab6f88ad02001d81b"
LABELS = ("H", "D", "A")


def load_r39u_module():
    got = subprocess.check_output(["git", "rev-parse", f"HEAD:{R39U_PATH.as_posix()}"], text=True).strip()
    if got != R39U_EXPECTED_BLOB:
        raise RuntimeError(f"R39U_EVALUATOR_BLOB_DRIFT:{got}:{R39U_EXPECTED_BLOB}")
    spec = importlib.util.spec_from_file_location("r39u_eval_for_r39w", R39U_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("R39U_IMPORT_SPEC_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def sigmoid(z: float) -> float:
    if z >= 0:
        e = math.exp(-min(z, 700.0))
        return 1.0 / (1.0 + e)
    e = math.exp(max(z, -700.0))
    return e / (1.0 + e)


def logit(p: float) -> float:
    p = min(max(p, 1e-9), 1.0 - 1e-9)
    return math.log(p / (1.0 - p))


def draw_features(x: tuple[float, ...]) -> tuple[float, ...]:
    return (
        abs(x[0]), abs(x[1]), abs(x[2]), abs(x[3]),
        x[4], x[5],
        abs(x[6]), abs(x[7]), abs(x[8]),
    )


def side_features(x: tuple[float, ...]) -> tuple[float, ...]:
    return (x[0], x[1], x[2], x[3], x[6], x[7], x[8])


def standardize(train_raw: list[tuple[float, ...]], target_raw: tuple[float, ...], quantile_fn):
    d = len(target_raw)
    med = []
    scale = []
    for j in range(d):
        vals = [r[j] for r in train_raw]
        m = quantile_fn(vals, 0.5)
        s = quantile_fn(vals, 0.75) - quantile_fn(vals, 0.25)
        if not math.isfinite(s) or s < 1e-9:
            s = 1.0
        med.append(m)
        scale.append(s)
    X = [[1.0] + [(r[j] - med[j]) / scale[j] for j in range(d)] for r in train_raw]
    xt = [1.0] + [(target_raw[j] - med[j]) / scale[j] for j in range(d)]
    return X, xt


def objective(beta: list[float], X: list[list[float]], y: list[int], l2: float) -> float:
    out = 0.0
    for row, yy in zip(X, y):
        z = sum(b * v for b, v in zip(beta, row))
        p = min(max(sigmoid(z), 1e-12), 1.0 - 1e-12)
        out += -(yy * math.log(p) + (1 - yy) * math.log(1.0 - p))
    out += 0.5 * l2 * sum(b * b for b in beta[1:])
    return out


def solve_linear(A: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[pivot][col]) < 1e-12:
            M[pivot][col] += 1e-8
        if pivot != col:
            M[col], M[pivot] = M[pivot], M[col]
        div = M[col][col]
        if abs(div) < 1e-15:
            raise RuntimeError("SINGULAR_NEWTON_SYSTEM")
        for j in range(col, n + 1):
            M[col][j] /= div
        for r in range(n):
            if r == col:
                continue
            f = M[r][col]
            if f == 0.0:
                continue
            for j in range(col, n + 1):
                M[r][j] -= f * M[col][j]
    return [M[i][n] for i in range(n)]


def fit_logistic(X: list[list[float]], y: list[int], l2: float, max_iter: int = 35):
    if not X or len(X) != len(y):
        raise RuntimeError("INVALID_LOGISTIC_INPUT")
    if min(y) == max(y):
        raise RuntimeError("LOGISTIC_CLASS_MISSING")
    d = len(X[0])
    mean_y = sum(y) / len(y)
    beta = [logit(mean_y)] + [0.0] * (d - 1)
    for it in range(1, max_iter + 1):
        grad = [0.0] * d
        H = [[0.0] * d for _ in range(d)]
        for row, yy in zip(X, y):
            z = sum(beta[j] * row[j] for j in range(d))
            p = sigmoid(z)
            err = p - yy
            w = max(p * (1.0 - p), 1e-10)
            for j in range(d):
                grad[j] += err * row[j]
            for j in range(d):
                rj = row[j]
                wrj = w * rj
                for k in range(j, d):
                    H[j][k] += wrj * row[k]
        for j in range(1, d):
            grad[j] += l2 * beta[j]
            H[j][j] += l2
        for j in range(d):
            H[j][j] += 1e-9
            for k in range(j):
                H[j][k] = H[k][j]
        delta = solve_linear(H, grad)
        old = objective(beta, X, y, l2)
        step = 1.0
        accepted = False
        while step >= 1e-6:
            cand = [beta[j] - step * delta[j] for j in range(d)]
            new = objective(cand, X, y, l2)
            if new <= old + 1e-10:
                beta = cand
                accepted = True
                break
            step *= 0.5
        if not accepted:
            raise RuntimeError("NEWTON_LINE_SEARCH_FAILED")
        if max(abs(step * v) for v in delta) < 1e-7:
            return beta, it
    raise RuntimeError("LOGISTIC_NOT_CONVERGED")


def predict_prob(beta: list[float], x: list[float]) -> float:
    return min(max(sigmoid(sum(b * v for b, v in zip(beta, x))), 1e-9), 1.0 - 1e-9)


def auc_binary(scores: list[float], labels: list[bool]) -> float:
    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    if not pos or not neg:
        raise RuntimeError("AUC_CLASS_MISSING")
    wins = 0.0
    for p in pos:
        for n in neg:
            wins += 1.0 if p > n else (0.5 if p == n else 0.0)
    return wins / (len(pos) * len(neg))


def build_eligible(u, rows, min_prior: int):
    by_comp = {}
    for r in rows:
        by_comp.setdefault(r.competition, []).append(r)
    for comp in by_comp:
        by_comp[comp].sort(key=lambda r: (r.dt, r.key))
    eligible = []
    for comp, rs in sorted(by_comp.items()):
        valid_prior = []
        i = 0
        while i < len(rs):
            d = rs[i].dt
            j = i
            while j < len(rs) and rs[j].dt == d:
                j += 1
            for t in rs[i:j]:
                if u.finite_vec(t.x) and len(valid_prior) >= min_prior:
                    eligible.append((t, len(valid_prior)))
            valid_prior.extend(r for r in rs[i:j] if u.finite_vec(r.x))
            i = j
    return by_comp, eligible


def hashed_sample(eligible, seed: str, n: int, excluded: set[str]):
    remaining = [(t, prior_n) for t, prior_n in eligible if t.key not in excluded]
    ranked = sorted(
        remaining,
        key=lambda z: (hashlib.sha256(f"{seed}|{z[0].key}".encode()).hexdigest(), z[0].key),
    )
    return ranked[:n]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prereg", type=Path, required=True)
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    pre = json.loads(args.prereg.read_text(encoding="utf-8"))
    assert pre["schema_version"] == "R39W-EXISTING-DATA-BALANCE-INTENSITY-1.0"
    assert pre["hard_boundaries"]["external_network_requests"] == 0
    assert pre["hard_boundaries"]["football_api_requests"] == 0
    assert pre["hard_boundaries"]["new_data_collection"] is False
    assert pre["model"]["candidate_grid"] is False
    assert pre["model"]["manual_draw_boost"] is False
    assert pre["model"]["post_result_tuning"] is False

    u = load_r39u_module()
    rows = u.load_rows(args.root)
    min_prior = int(pre["sample"]["minimum_strictly_prior_same_competition_rows"])
    by_comp, eligible = build_eligible(u, rows, min_prior)

    r39u = hashed_sample(eligible, R39U_SEED, 100, set())
    r39u_keys = [t.key for t, _ in r39u]
    if u.sample_sha(r39u_keys) != R39U_EXPECTED_SAMPLE_SHA:
        raise RuntimeError("R39U_SAMPLE_IDENTITY_DRIFT")
    r39v = hashed_sample(eligible, R39V_SEED, 100, set(r39u_keys))
    r39v_keys = [t.key for t, _ in r39v]
    if u.sample_sha(r39v_keys) != R39V_EXPECTED_SAMPLE_SHA:
        raise RuntimeError("R39V_SAMPLE_IDENTITY_DRIFT")

    excluded = set(r39u_keys) | set(r39v_keys)
    sample = hashed_sample(eligible, str(pre["sample"]["seed"]), int(pre["sample"]["size"]), excluded)
    if len(sample) != int(pre["sample"]["size"]):
        raise RuntimeError("INSUFFICIENT_R39W_SAMPLE")
    sample_keys = [t.key for t, _ in sample]
    overlap_u = len(set(sample_keys) & set(r39u_keys))
    overlap_v = len(set(sample_keys) & set(r39v_keys))
    if overlap_u != 0 or overlap_v != 0:
        raise RuntimeError(f"R39W_OVERLAP_INVALID:{overlap_u}:{overlap_v}")

    l2 = float(pre["model"]["l2"])
    model_records = []
    baseline_records = []
    solver_iters = []
    for t, _ in sample:
        prior = [r for r in by_comp[t.competition] if r.dt < t.dt and u.finite_vec(r.x)]
        if len(prior) < max(50, min_prior):
            raise RuntimeError(f"PRIOR_POOL_DRIFT:{t.key}:{len(prior)}")

        draw_train_raw = [draw_features(r.x) for r in prior]
        draw_target_raw = draw_features(t.x)
        Xd, xd = standardize(draw_train_raw, draw_target_raw, u.quantile)
        yd = [1 if r.label == "D" else 0 for r in prior]
        bd, itd = fit_logistic(Xd, yd, l2)
        pD = predict_prob(bd, xd)

        nd = [r for r in prior if r.label != "D"]
        side_train_raw = [side_features(r.x) for r in nd]
        side_target_raw = side_features(t.x)
        Xs, xs = standardize(side_train_raw, side_target_raw, u.quantile)
        ys = [1 if r.label == "H" else 0 for r in nd]
        bs, its = fit_logistic(Xs, ys, l2)
        pHnd = predict_prob(bs, xs)

        p = {
            "D": pD,
            "H": (1.0 - pD) * pHnd,
            "A": (1.0 - pD) * (1.0 - pHnd),
        }
        total = sum(p.values())
        p = {k: v / total for k, v in p.items()}
        pred = u.choose(p)
        model_records.append({
            "key": t.key, "competition": t.competition, "season": t.season,
            "date": t.dt.isoformat(), "home": t.home, "away": t.away,
            "actual": t.label, "prediction": pred,
            "probabilities": {lab: round(p[lab], 12) for lab in LABELS},
            "strictly_prior_pool_n": len(prior),
            "draw_solver_iterations": itd, "side_solver_iterations": its,
        })
        solver_iters.extend([itd, its])

        pb = u.probs_for(t, prior, int(pre["baseline"]["k"]))
        baseline_records.append({
            "key": t.key, "competition": t.competition, "season": t.season,
            "date": t.dt.isoformat(), "home": t.home, "away": t.away,
            "actual": t.label, "prediction": u.choose(pb),
            "probabilities": {lab: round(pb[lab], 12) for lab in LABELS},
            "strictly_prior_pool_n": len(prior),
        })

    mm = u.metrics(model_records)
    bm = u.metrics(baseline_records)
    labels = [r["actual"] == "D" for r in model_records]
    model_auc = auc_binary([float(r["probabilities"]["D"]) for r in model_records], labels)
    base_auc = auc_binary([float(r["probabilities"]["D"]) for r in baseline_records], labels)

    model_f1 = mm["draw_f1"] if mm["draw_f1"] is not None else -1.0
    base_f1 = bm["draw_f1"] if bm["draw_f1"] is not None else -1.0
    gate = {
        "draw_auc_gt_0_55_and_plus_0_02_vs_baseline": model_auc > 0.55 and model_auc >= base_auc + 0.02,
        "draw_f1_plus_0_05_vs_baseline": model_f1 >= base_f1 + 0.05,
        "log_loss_better_than_baseline": mm["log_loss"] < bm["log_loss"],
        "accuracy_noninferior_within_0_02": mm["accuracy"] >= bm["accuracy"] - 0.02,
    }
    passed = all(gate.values())
    terminal = "PASS_R39W_BALANCE_INTENSITY_INCREMENT" if passed else "FAIL_R39W_BALANCE_INTENSITY_NO_INCREMENT"

    out = {
        "schema_version": pre["schema_version"],
        "terminal": terminal,
        "passed": passed,
        "source_rows": len(rows),
        "eligible_rows": len(eligible),
        "r39u_identity_sha256": u.sample_sha(r39u_keys),
        "r39v_identity_sha256": u.sample_sha(r39v_keys),
        "r39w_fixed100_identity_sha256": u.sample_sha(sample_keys),
        "r39u_overlap": overlap_u,
        "r39v_overlap": overlap_v,
        "sample_keys": sample_keys,
        "model_metrics": mm,
        "baseline_metrics": bm,
        "model_draw_auc": model_auc,
        "baseline_draw_auc": base_auc,
        "gate": gate,
        "solver": {
            "fits": len(solver_iters),
            "min_iterations": min(solver_iters),
            "max_iterations": max(solver_iters),
            "mean_iterations": sum(solver_iters) / len(solver_iters),
        },
        "model_predictions": model_records,
        "baseline_predictions": baseline_records,
        "hard_boundaries": pre["hard_boundaries"],
        "interpretation_boundary": "Retrospective disjoint exploratory research only. No formal promotion; formal_weight remains 0."
    }
    args.out.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (args.out / "r39w_result.json").write_text(raw, encoding="utf-8")
    (args.out / "r39w_result.sha256").write_text(hashlib.sha256(raw.encode()).hexdigest() + "\n", encoding="ascii")
    print(json.dumps({
        "terminal": terminal,
        "passed": passed,
        "r39w_fixed100_identity_sha256": out["r39w_fixed100_identity_sha256"],
        "overlap": {"r39u": overlap_u, "r39v": overlap_v},
        "model_metrics": mm,
        "baseline_metrics": bm,
        "model_draw_auc": model_auc,
        "baseline_draw_auc": base_auc,
        "gate": gate,
        "solver": out["solver"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
