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

R39W_PATH = Path("football-data/research/existing_data_balance_intensity_r39w/evaluate_r39w.py")
R39W_EXPECTED_BLOB = "4abe6918ec9fd32e65d7b6eb2d74686238e0f1ae"

SEEDS = {
    "r39u": "R39U_FIXED100_20260810",
    "r39v": "R39V_DISJOINT_FIXED100_20260810",
    "r39w": "R39W_BALANCE_INTENSITY_FIXED100_20260810",
    "r39x": "R39X_NONLINEAR_REGIME_FIXED100_20260810",
    "r39y": "R39Y_REGIME_REPLICATION_FIXED100_20260810",
    "r39z": "R39Z_ORDINAL_BAND_FIXED100_20260810",
}
EXPECTED_SHA = {
    "r39u": "dad7317511e2dd080d82e2f7bfc68590c369a01844e8e434625e0d0b135c1ce6",
    "r39v": "600cf1c9e7815f436bfa19739c4a399ae42f32e55c03fecab6f88ad02001d81b",
    "r39w": "df64b2999f447a1b42a69326ca5db3e4132af624d14d147deda06042b6d3f5c8",
    "r39x": "5de03fc4d0e52fda74c10dd8430cbe0a2f3db43fbd97529608222083d70270ba",
    "r39y": "de0c755f6e828a3778d8da9e830bba988ade7ebb347b208f20260872c4fdbc57",
    "r39z": "ffb26e35e1f98196cfb2842a559b0d22380a4b89165889351dfbf6f99aaf61a2",
}
LABELS = ("H", "D", "A")


def load_r39w_module():
    got = subprocess.check_output(["git", "rev-parse", f"HEAD:{R39W_PATH.as_posix()}"], text=True).strip()
    if got != R39W_EXPECTED_BLOB:
        raise RuntimeError(f"R39W_EVALUATOR_BLOB_DRIFT:{got}:{R39W_EXPECTED_BLOB}")
    spec = importlib.util.spec_from_file_location("r39w_eval_for_r40a", R39W_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("R39W_IMPORT_SPEC_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def sigmoid(x: float) -> float:
    if x >= 0:
        e = math.exp(-min(x, 700.0))
        return 1.0 / (1.0 + e)
    e = math.exp(max(x, -700.0))
    return e / (1.0 + e)


def softplus(x: float) -> float:
    if x > 30.0:
        return x
    if x < -30.0:
        return math.exp(x)
    return math.log1p(math.exp(x))


def inverse_softplus(y: float) -> float:
    if y <= 0:
        raise RuntimeError(f"INVALID_SOFTPLUS_INVERSE:{y}")
    if y > 30.0:
        return y
    return math.log(math.expm1(y))


def logit(p: float) -> float:
    p = min(max(p, 1e-9), 1.0 - 1e-9)
    return math.log(p / (1.0 - p))


def width_features(x: tuple[float, ...]) -> tuple[float, ...]:
    return (abs(x[0]), abs(x[1]), x[4], x[5])


def standardize_no_intercept(train_raw, target_raw, quantile_fn):
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
    X = [[(r[j] - med[j]) / scale[j] for j in range(d)] for r in train_raw]
    xt = [(target_raw[j] - med[j]) / scale[j] for j in range(d)]
    return X, xt


def unpack(params: list[float], dloc: int, dwidth: int):
    center = params[0]
    gamma0 = params[1]
    beta = params[2:2 + dloc]
    gamma = params[2 + dloc:2 + dloc + dwidth]
    return center, gamma0, beta, gamma


def ordinal_probs(params: list[float], x: list[float], z: list[float], min_width: float):
    center, gamma0, beta, gamma = unpack(params, len(x), len(z))
    eta = sum(b * v for b, v in zip(beta, x))
    graw = gamma0 + sum(g * v for g, v in zip(gamma, z))
    width = min_width + softplus(graw)
    theta1 = center - 0.5 * width
    theta2 = center + 0.5 * width
    a = sigmoid(theta1 - eta)
    b = sigmoid(theta2 - eta)
    pA = max(a, 1e-12)
    pD = max(b - a, 1e-12)
    pH = max(1.0 - b, 1e-12)
    total = pA + pD + pH
    return {"H": pH / total, "D": pD / total, "A": pA / total}, {
        "eta": eta, "graw": graw, "width": width, "theta1": theta1, "theta2": theta2
    }


def loss_and_grad(params, X, Z, labels, cfg):
    dloc = len(X[0])
    dwidth = len(Z[0])
    center, gamma0, beta, gamma = unpack(params, dloc, dwidth)
    min_width = float(cfg["minimum_width"])
    n = len(labels)
    grad = [0.0] * len(params)
    nll = 0.0

    for x, z, lab in zip(X, Z, labels):
        eta = sum(b * v for b, v in zip(beta, x))
        graw = gamma0 + sum(g * v for g, v in zip(gamma, z))
        width = min_width + softplus(graw)
        theta1 = center - 0.5 * width
        theta2 = center + 0.5 * width
        a = sigmoid(theta1 - eta)
        b = sigmoid(theta2 - eta)
        da = a * (1.0 - a)
        db = b * (1.0 - b)

        if lab == "A":
            p = max(a, 1e-12)
            dp_eta = -da
            dp_t1 = da
            dp_t2 = 0.0
        elif lab == "D":
            p = max(b - a, 1e-12)
            dp_eta = da - db
            dp_t1 = -da
            dp_t2 = db
        elif lab == "H":
            p = max(1.0 - b, 1e-12)
            dp_eta = db
            dp_t1 = 0.0
            dp_t2 = -db
        else:
            raise RuntimeError(f"UNKNOWN_LABEL:{lab}")

        nll -= math.log(p)
        invp = 1.0 / p
        g_eta = -dp_eta * invp
        g_t1 = -dp_t1 * invp
        g_t2 = -dp_t2 * invp
        g_center = g_t1 + g_t2
        g_width = 0.5 * (g_t2 - g_t1)
        g_graw = g_width * sigmoid(graw)

        grad[0] += g_center
        grad[1] += g_graw
        for j, xv in enumerate(x):
            grad[2 + j] += g_eta * xv
        base = 2 + dloc
        for j, zv in enumerate(z):
            grad[base + j] += g_graw * zv

    l2loc = float(cfg["location_l2"])
    l2width = float(cfg["width_l2"])
    reg = 0.5 * l2loc * sum(v * v for v in beta) + 0.5 * l2width * sum(v * v for v in gamma)
    for j, v in enumerate(beta):
        grad[2 + j] += l2loc * v
    base = 2 + dloc
    for j, v in enumerate(gamma):
        grad[base + j] += l2width * v

    scale = 1.0 / n
    return (nll + reg) * scale, [g * scale for g in grad]


def fit_ordinal(X, Z, labels, cfg):
    n = len(labels)
    counts = {lab: sum(y == lab for y in labels) for lab in LABELS}
    if min(counts.values()) <= 0:
        raise RuntimeError(f"ORDINAL_CLASS_MISSING:{counts}")
    pA = counts["A"] / n
    pAD = (counts["A"] + counts["D"]) / n
    theta1 = logit(pA)
    theta2 = logit(pAD)
    center0 = 0.5 * (theta1 + theta2)
    min_width = float(cfg["minimum_width"])
    desired_width = max(theta2 - theta1, min_width + 1e-3)
    gamma0 = inverse_softplus(max(desired_width - min_width, 1e-3))
    dloc = len(X[0])
    dwidth = len(Z[0])
    params = [center0, gamma0] + [0.0] * dloc + [0.0] * dwidth

    lr = float(cfg["learning_rate"])
    b1 = float(cfg["adam_beta1"])
    b2 = float(cfg["adam_beta2"])
    eps = float(cfg["adam_epsilon"])
    max_iter = int(cfg["max_iterations"])
    grad_tol = float(cfg["convergence_gradient_norm"])
    obj_delta = float(cfg["convergence_objective_delta"])
    patience = int(cfg["convergence_patience"])
    m = [0.0] * len(params)
    v = [0.0] * len(params)
    prev_obj = None
    stable = 0
    final_obj = None
    final_grad_norm = None

    for it in range(1, max_iter + 1):
        obj, grad = loss_and_grad(params, X, Z, labels, cfg)
        if not math.isfinite(obj) or any(not math.isfinite(g) for g in grad):
            return params, {"converged": False, "reason": "NONFINITE", "iterations": it, "objective": obj, "gradient_norm": None}
        gn = math.sqrt(sum(g * g for g in grad))
        final_obj, final_grad_norm = obj, gn
        if gn <= grad_tol:
            return params, {"converged": True, "reason": "GRADIENT_NORM", "iterations": it, "objective": obj, "gradient_norm": gn}
        if prev_obj is not None and abs(prev_obj - obj) <= obj_delta:
            stable += 1
        else:
            stable = 0
        if stable >= patience:
            return params, {"converged": True, "reason": "OBJECTIVE_PATIENCE", "iterations": it, "objective": obj, "gradient_norm": gn}

        for j, g in enumerate(grad):
            m[j] = b1 * m[j] + (1.0 - b1) * g
            v[j] = b2 * v[j] + (1.0 - b2) * g * g
            mhat = m[j] / (1.0 - b1 ** it)
            vhat = v[j] / (1.0 - b2 ** it)
            params[j] -= lr * mhat / (math.sqrt(vhat) + eps)
        prev_obj = obj

    return params, {"converged": False, "reason": "MAX_ITERATIONS", "iterations": max_iter, "objective": final_obj, "gradient_norm": final_grad_norm}


def verify_sample(u, rows, name):
    keys = [t.key for t, _ in rows]
    got = u.sample_sha(keys)
    want = EXPECTED_SHA[name]
    if got != want:
        raise RuntimeError(f"{name.upper()}_SAMPLE_IDENTITY_DRIFT:{got}:{want}")
    return keys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prereg", type=Path, required=True)
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    pre = json.loads(args.prereg.read_text(encoding="utf-8"))
    assert pre["schema_version"] == "R40A-EXISTING-DATA-VARYING-ORDINAL-1.0"
    cfg = pre["model"]
    assert cfg["optimizer"] == "deterministic full-batch Adam"
    assert float(cfg["learning_rate"]) == 0.03
    assert int(cfg["max_iterations"]) == 300
    assert cfg["manual_draw_boost"] is False
    assert cfg["class_weighting"] is False
    assert cfg["candidate_grid"] is False
    assert cfg["post_result_tuning"] is False
    assert cfg["abstention"] is False
    h = pre["hard_boundaries"]
    assert h["external_network_requests"] == 0 and h["football_api_requests"] == 0
    assert h["new_data_collection"] is False

    w = load_r39w_module()
    u = w.load_r39u_module()
    all_rows = u.load_rows(args.root)
    min_prior = int(pre["sample"]["minimum_strictly_prior_same_competition_rows"])
    by_comp, eligible = w.build_eligible(u, all_rows, min_prior)

    excluded = set()
    consumed = {}
    for name in ("r39u", "r39v", "r39w", "r39x", "r39y", "r39z"):
        sample = w.hashed_sample(eligible, SEEDS[name], 100, excluded)
        keys = verify_sample(u, sample, name)
        consumed[name] = keys
        excluded.update(keys)

    sample = w.hashed_sample(eligible, str(pre["sample"]["seed"]), int(pre["sample"]["size"]), excluded)
    if len(sample) != int(pre["sample"]["size"]):
        raise RuntimeError("INSUFFICIENT_R40A_SAMPLE")
    sample_keys = [t.key for t, _ in sample]
    overlaps = {name: len(set(sample_keys) & set(keys)) for name, keys in consumed.items()}
    if any(overlaps.values()):
        raise RuntimeError(f"R40A_OVERLAP_INVALID:{overlaps}")

    ordinal_records = []
    baseline_records = []
    fit_audit = []
    all_converged = True

    for t, _ in sample:
        prior = [r for r in by_comp[t.competition] if r.dt < t.dt and u.finite_vec(r.x)]
        if len(prior) < min_prior:
            raise RuntimeError(f"PRIOR_POOL_DRIFT:{t.key}:{len(prior)}")

        loc_raw = [w.side_features(r.x) for r in prior]
        loc_target = w.side_features(t.x)
        Xloc, xloc = standardize_no_intercept(loc_raw, loc_target, u.quantile)
        width_raw = [width_features(r.x) for r in prior]
        width_target = width_features(t.x)
        Z, zt = standardize_no_intercept(width_raw, width_target, u.quantile)
        labels = [r.label for r in prior]

        params, fit = fit_ordinal(Xloc, Z, labels, cfg)
        if not fit["converged"]:
            all_converged = False
        probs, latent = ordinal_probs(params, xloc, zt, float(cfg["minimum_width"]))
        if abs(sum(probs.values()) - 1.0) > 1e-9:
            raise RuntimeError(f"PROBABILITY_CONSERVATION_FAIL:{t.key}:{sum(probs.values())}")
        ordinal_records.append({
            "key": t.key, "competition": t.competition, "season": t.season,
            "date": t.dt.isoformat(), "home": t.home, "away": t.away,
            "actual": t.label, "prediction": u.choose(probs),
            "probabilities": {lab: round(probs[lab], 12) for lab in LABELS},
            "strictly_prior_pool_n": len(prior),
            "target_latent": latent,
        })
        center, gamma0, beta, gamma = unpack(params, len(xloc), len(zt))
        fit_audit.append({
            "key": t.key,
            "strictly_prior_pool_n": len(prior),
            "fit": fit,
            "center": center,
            "gamma0": gamma0,
            "beta": beta,
            "gamma": gamma,
            "target_latent": latent,
            "probability_sum": sum(probs.values()),
        })

        # Exact R39W two-stage baseline on the same target/prior pool.
        draw_train_raw = [w.draw_features(r.x) for r in prior]
        draw_target_raw = w.draw_features(t.x)
        Xd, xd = w.standardize(draw_train_raw, draw_target_raw, u.quantile)
        yd = [1 if r.label == "D" else 0 for r in prior]
        bd, _ = w.fit_logistic(Xd, yd, float(pre["baseline"]["draw_l2"]))
        pD = w.predict_prob(bd, xd)

        nd = [r for r in prior if r.label != "D"]
        side_train_raw = [w.side_features(r.x) for r in nd]
        side_target_raw = w.side_features(t.x)
        Xs, xs = w.standardize(side_train_raw, side_target_raw, u.quantile)
        ys = [1 if r.label == "H" else 0 for r in nd]
        bs, _ = w.fit_logistic(Xs, ys, float(pre["baseline"]["side_l2"]))
        pHnd = w.predict_prob(bs, xs)
        pb = {"D": pD, "H": (1.0 - pD) * pHnd, "A": (1.0 - pD) * (1.0 - pHnd)}
        total = sum(pb.values())
        pb = {k: v / total for k, v in pb.items()}
        baseline_records.append({
            "key": t.key, "competition": t.competition, "season": t.season,
            "date": t.dt.isoformat(), "home": t.home, "away": t.away,
            "actual": t.label, "prediction": u.choose(pb),
            "probabilities": {lab: round(pb[lab], 12) for lab in LABELS},
            "strictly_prior_pool_n": len(prior),
        })

    om = u.metrics(ordinal_records)
    bm = u.metrics(baseline_records)
    ydraw = [r["actual"] == "D" for r in ordinal_records]
    oauc = w.auc_binary([float(r["probabilities"]["D"]) for r in ordinal_records], ydraw)
    bauc = w.auc_binary([float(r["probabilities"]["D"]) for r in baseline_records], ydraw)
    of1 = om["draw_f1"] if om["draw_f1"] is not None else -1.0
    bf1 = bm["draw_f1"] if bm["draw_f1"] is not None else -1.0
    orec = om["draw_recall"] if om["draw_recall"] is not None else -1.0

    gate = {
        "log_loss_better_and_brier_nonworse": om["log_loss"] < bm["log_loss"] and om["brier"] <= bm["brier"],
        "draw_auc_nonworse": oauc >= bauc,
        "draw_f1_ge_0_25_and_plus_0_08": of1 >= 0.25 and of1 >= bf1 + 0.08,
        "draw_recall_ge_0_20": orec >= 0.20,
        "accuracy_noninferior_within_0_02": om["accuracy"] >= bm["accuracy"] - 0.02,
        "all_target_fits_converged": all_converged,
    }
    passed = all(gate.values())
    terminal = "PASS_R40A_VARYING_ORDINAL_INCREMENT" if passed else "FAIL_R40A_VARYING_ORDINAL_NO_INCREMENT"

    out = {
        "schema_version": pre["schema_version"],
        "terminal": terminal,
        "passed": passed,
        "source_rows": len(all_rows),
        "eligible_rows": len(eligible),
        "consumed_sample_identities": {name: u.sample_sha(keys) for name, keys in consumed.items()},
        "r40a_fixed100_identity_sha256": u.sample_sha(sample_keys),
        "overlap": overlaps,
        "sample_keys": sample_keys,
        "ordinal_metrics": om,
        "baseline_metrics": bm,
        "ordinal_draw_auc": oauc,
        "baseline_draw_auc": bauc,
        "gate": gate,
        "convergence": {
            "all_converged": all_converged,
            "converged_count": sum(bool(a["fit"]["converged"]) for a in fit_audit),
            "max_iterations_used": max(a["fit"]["iterations"] for a in fit_audit),
            "mean_iterations": sum(a["fit"]["iterations"] for a in fit_audit) / len(fit_audit),
            "reasons": {reason: sum(a["fit"]["reason"] == reason for a in fit_audit) for reason in sorted(set(a["fit"]["reason"] for a in fit_audit))},
        },
        "fit_audit": fit_audit,
        "ordinal_predictions": ordinal_records,
        "baseline_predictions": baseline_records,
        "hard_boundaries": h,
        "interpretation_boundary": "Retrospective probabilistic challenger only. Even PASS authorizes only time-ordered rolling evaluation of the exact frozen architecture; formal_weight remains 0."
    }
    args.out.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (args.out / "r40a_result.json").write_text(raw, encoding="utf-8")
    (args.out / "r40a_result.sha256").write_text(hashlib.sha256(raw.encode()).hexdigest() + "\n", encoding="ascii")
    print(json.dumps({
        "terminal": terminal, "passed": passed,
        "sample_sha": out["r40a_fixed100_identity_sha256"], "overlap": overlaps,
        "ordinal_metrics": om, "baseline_metrics": bm,
        "ordinal_draw_auc": oauc, "baseline_draw_auc": bauc,
        "gate": gate, "convergence": out["convergence"]
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
