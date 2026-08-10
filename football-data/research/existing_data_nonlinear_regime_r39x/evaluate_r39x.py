#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import subprocess
import sys
from bisect import bisect_right
from pathlib import Path

R39W_PATH = Path("football-data/research/existing_data_balance_intensity_r39w/evaluate_r39w.py")
R39W_EXPECTED_BLOB = "4abe6918ec9fd32e65d7b6eb2d74686238e0f1ae"

R39U_SEED = "R39U_FIXED100_20260810"
R39V_SEED = "R39V_DISJOINT_FIXED100_20260810"
R39W_SEED = "R39W_BALANCE_INTENSITY_FIXED100_20260810"
R39U_EXPECTED_SHA = "dad7317511e2dd080d82e2f7bfc68590c369a01844e8e434625e0d0b135c1ce6"
R39V_EXPECTED_SHA = "600cf1c9e7815f436bfa19739c4a399ae42f32e55c03fecab6f88ad02001d81b"
R39W_EXPECTED_SHA = "df64b2999f447a1b42a69326ca5db3e4132af624d14d147deda06042b6d3f5c8"
LABELS = ("H", "D", "A")
GAP_IDXS = (0, 1, 2, 3, 6, 7, 8)


def load_r39w_module():
    got = subprocess.check_output(["git", "rev-parse", f"HEAD:{R39W_PATH.as_posix()}"], text=True).strip()
    if got != R39W_EXPECTED_BLOB:
        raise RuntimeError(f"R39W_EVALUATOR_BLOB_DRIFT:{got}:{R39W_EXPECTED_BLOB}")
    spec = importlib.util.spec_from_file_location("r39w_eval_for_r39x", R39W_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("R39W_IMPORT_SPEC_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def balance_score(x: tuple[float, ...], sorted_gap_values: list[list[float]]) -> float:
    n = len(sorted_gap_values[0])
    if n <= 0:
        raise RuntimeError("EMPTY_BALANCE_REFERENCE")
    pct = []
    for idx, vals in zip(GAP_IDXS, sorted_gap_values):
        pct.append(bisect_right(vals, abs(x[idx])) / n)
    return sum(pct) / len(pct)


def bin3(value: float, q1: float, q2: float) -> int:
    if value <= q1:
        return 0
    if value <= q2:
        return 1
    return 2


def probs_from_draw_and_side(p_draw: float, p_home_given_nondraw: float) -> dict[str, float]:
    p = {
        "D": p_draw,
        "H": (1.0 - p_draw) * p_home_given_nondraw,
        "A": (1.0 - p_draw) * (1.0 - p_home_given_nondraw),
    }
    total = sum(p.values())
    if not math.isfinite(total) or total <= 0:
        raise RuntimeError("INVALID_PROBABILITY_TOTAL")
    return {k: v / total for k, v in p.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prereg", type=Path, required=True)
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    pre = json.loads(args.prereg.read_text(encoding="utf-8"))
    assert pre["schema_version"] == "R39X-EXISTING-DATA-NONLINEAR-REGIME-1.0"
    assert pre["draw_model"]["cells"] == 9
    assert float(pre["draw_model"]["draw_rate_shrinkage_prior_strength"]) == 60.0
    assert pre["draw_model"]["post_result_tuning"] is False
    assert pre["draw_model"]["manual_draw_boost"] is False
    h = pre["hard_boundaries"]
    assert h["external_network_requests"] == 0
    assert h["football_api_requests"] == 0
    assert h["new_data_collection"] is False
    assert h["abstention"] is False

    w = load_r39w_module()
    u = w.load_r39u_module()
    rows = u.load_rows(args.root)
    min_prior = int(pre["sample"]["minimum_strictly_prior_same_competition_rows"])
    by_comp, eligible = w.build_eligible(u, rows, min_prior)

    r39u = w.hashed_sample(eligible, R39U_SEED, 100, set())
    r39u_keys = [t.key for t, _ in r39u]
    if u.sample_sha(r39u_keys) != R39U_EXPECTED_SHA:
        raise RuntimeError("R39U_SAMPLE_IDENTITY_DRIFT")

    r39v = w.hashed_sample(eligible, R39V_SEED, 100, set(r39u_keys))
    r39v_keys = [t.key for t, _ in r39v]
    if u.sample_sha(r39v_keys) != R39V_EXPECTED_SHA:
        raise RuntimeError("R39V_SAMPLE_IDENTITY_DRIFT")

    uv = set(r39u_keys) | set(r39v_keys)
    r39w = w.hashed_sample(eligible, R39W_SEED, 100, uv)
    r39w_keys = [t.key for t, _ in r39w]
    if u.sample_sha(r39w_keys) != R39W_EXPECTED_SHA:
        raise RuntimeError("R39W_SAMPLE_IDENTITY_DRIFT")

    excluded = uv | set(r39w_keys)
    sample = w.hashed_sample(eligible, str(pre["sample"]["seed"]), int(pre["sample"]["size"]), excluded)
    if len(sample) != int(pre["sample"]["size"]):
        raise RuntimeError("INSUFFICIENT_R39X_SAMPLE")
    sample_keys = [t.key for t, _ in sample]
    overlap_u = len(set(sample_keys) & set(r39u_keys))
    overlap_v = len(set(sample_keys) & set(r39v_keys))
    overlap_w = len(set(sample_keys) & set(r39w_keys))
    if overlap_u or overlap_v or overlap_w:
        raise RuntimeError(f"R39X_OVERLAP_INVALID:{overlap_u}:{overlap_v}:{overlap_w}")

    alpha = float(pre["draw_model"]["draw_rate_shrinkage_prior_strength"])
    l2 = float(pre["side_model"]["l2"])

    regime_records = []
    linear_records = []
    cell_audit = []
    side_solver_iters = []
    linear_draw_solver_iters = []

    for t, _ in sample:
        prior = [r for r in by_comp[t.competition] if r.dt < t.dt and u.finite_vec(r.x)]
        if len(prior) < min_prior:
            raise RuntimeError(f"PRIOR_POOL_DRIFT:{t.key}:{len(prior)}")

        # Freeze all nonlinear regime transforms from this target's strictly-prior pool only.
        sorted_gaps = [sorted(abs(r.x[idx]) for r in prior) for idx in GAP_IDXS]
        prior_balance = [balance_score(r.x, sorted_gaps) for r in prior]
        target_balance = balance_score(t.x, sorted_gaps)
        prior_intensity = [r.x[4] + r.x[5] for r in prior]
        target_intensity = t.x[4] + t.x[5]

        bq1 = u.quantile(prior_balance, 1.0 / 3.0)
        bq2 = u.quantile(prior_balance, 2.0 / 3.0)
        iq1 = u.quantile(prior_intensity, 1.0 / 3.0)
        iq2 = u.quantile(prior_intensity, 2.0 / 3.0)

        tb = bin3(target_balance, bq1, bq2)
        ti = bin3(target_intensity, iq1, iq2)
        cell_rows = []
        for r, bs, ins in zip(prior, prior_balance, prior_intensity):
            if bin3(bs, bq1, bq2) == tb and bin3(ins, iq1, iq2) == ti:
                cell_rows.append(r)

        if not cell_rows:
            raise RuntimeError(f"EMPTY_TARGET_CELL:{t.key}:{tb}:{ti}")
        overall_draws = sum(r.label == "D" for r in prior)
        overall_draw_rate = overall_draws / len(prior)
        cell_draws = sum(r.label == "D" for r in cell_rows)
        pD_regime = (cell_draws + alpha * overall_draw_rate) / (len(cell_rows) + alpha)
        pD_regime = min(max(pD_regime, 1e-9), 1.0 - 1e-9)

        # Keep the conditional H/A lane exactly as R39W.
        nd = [r for r in prior if r.label != "D"]
        side_train_raw = [w.side_features(r.x) for r in nd]
        side_target_raw = w.side_features(t.x)
        Xs, xs = w.standardize(side_train_raw, side_target_raw, u.quantile)
        ys = [1 if r.label == "H" else 0 for r in nd]
        bs_side, its_side = w.fit_logistic(Xs, ys, l2)
        pHnd = w.predict_prob(bs_side, xs)
        side_solver_iters.append(its_side)

        p_regime = probs_from_draw_and_side(pD_regime, pHnd)
        regime_records.append({
            "key": t.key,
            "competition": t.competition,
            "season": t.season,
            "date": t.dt.isoformat(),
            "home": t.home,
            "away": t.away,
            "actual": t.label,
            "prediction": u.choose(p_regime),
            "probabilities": {lab: round(p_regime[lab], 12) for lab in LABELS},
            "strictly_prior_pool_n": len(prior),
            "balance_score": target_balance,
            "intensity_score": target_intensity,
            "balance_bin": tb,
            "intensity_bin": ti,
            "cell_n": len(cell_rows),
            "cell_draws": cell_draws,
            "overall_draw_rate": overall_draw_rate,
            "shrunk_cell_draw_probability": pD_regime,
            "side_solver_iterations": its_side,
        })
        cell_audit.append({
            "key": t.key,
            "balance_q1": bq1,
            "balance_q2": bq2,
            "intensity_q1": iq1,
            "intensity_q2": iq2,
            "target_balance_score": target_balance,
            "target_intensity_score": target_intensity,
            "target_cell": [tb, ti],
            "cell_n": len(cell_rows),
            "cell_draws": cell_draws,
            "overall_prior_n": len(prior),
            "overall_prior_draws": overall_draws,
            "overall_draw_rate": overall_draw_rate,
            "shrunk_cell_draw_probability": pD_regime,
        })

        # Exact R39W linear Draw baseline on the same target and same prior pool.
        draw_train_raw = [w.draw_features(r.x) for r in prior]
        draw_target_raw = w.draw_features(t.x)
        Xd, xd = w.standardize(draw_train_raw, draw_target_raw, u.quantile)
        yd = [1 if r.label == "D" else 0 for r in prior]
        bd, itd = w.fit_logistic(Xd, yd, l2)
        pD_linear = w.predict_prob(bd, xd)
        linear_draw_solver_iters.append(itd)
        p_linear = probs_from_draw_and_side(pD_linear, pHnd)
        linear_records.append({
            "key": t.key,
            "competition": t.competition,
            "season": t.season,
            "date": t.dt.isoformat(),
            "home": t.home,
            "away": t.away,
            "actual": t.label,
            "prediction": u.choose(p_linear),
            "probabilities": {lab: round(p_linear[lab], 12) for lab in LABELS},
            "strictly_prior_pool_n": len(prior),
            "draw_solver_iterations": itd,
            "side_solver_iterations": its_side,
        })

    rm = u.metrics(regime_records)
    lm = u.metrics(linear_records)
    labels = [r["actual"] == "D" for r in regime_records]
    regime_auc = w.auc_binary([float(r["probabilities"]["D"]) for r in regime_records], labels)
    linear_auc = w.auc_binary([float(r["probabilities"]["D"]) for r in linear_records], labels)
    regime_f1 = rm["draw_f1"] if rm["draw_f1"] is not None else -1.0
    linear_f1 = lm["draw_f1"] if lm["draw_f1"] is not None else -1.0

    gate = {
        "draw_auc_gt_0_56_and_plus_0_02_vs_linear": regime_auc > 0.56 and regime_auc >= linear_auc + 0.02,
        "draw_f1_plus_0_05_vs_linear": regime_f1 >= linear_f1 + 0.05,
        "log_loss_noninferior_within_0_01": rm["log_loss"] <= lm["log_loss"] + 0.01,
        "accuracy_noninferior_within_0_02": rm["accuracy"] >= lm["accuracy"] - 0.02,
    }
    passed = all(gate.values())
    terminal = "PASS_R39X_NONLINEAR_REGIME_INCREMENT" if passed else "FAIL_R39X_NONLINEAR_REGIME_NO_INCREMENT"

    out = {
        "schema_version": pre["schema_version"],
        "terminal": terminal,
        "passed": passed,
        "source_rows": len(rows),
        "eligible_rows": len(eligible),
        "r39u_identity_sha256": u.sample_sha(r39u_keys),
        "r39v_identity_sha256": u.sample_sha(r39v_keys),
        "r39w_identity_sha256": u.sample_sha(r39w_keys),
        "r39x_fixed100_identity_sha256": u.sample_sha(sample_keys),
        "overlap": {"r39u": overlap_u, "r39v": overlap_v, "r39w": overlap_w},
        "sample_keys": sample_keys,
        "regime_metrics": rm,
        "linear_metrics": lm,
        "regime_draw_auc": regime_auc,
        "linear_draw_auc": linear_auc,
        "gate": gate,
        "solver": {
            "side_fits": len(side_solver_iters),
            "side_min_iterations": min(side_solver_iters),
            "side_max_iterations": max(side_solver_iters),
            "side_mean_iterations": sum(side_solver_iters) / len(side_solver_iters),
            "linear_draw_fits": len(linear_draw_solver_iters),
            "linear_draw_min_iterations": min(linear_draw_solver_iters),
            "linear_draw_max_iterations": max(linear_draw_solver_iters),
            "linear_draw_mean_iterations": sum(linear_draw_solver_iters) / len(linear_draw_solver_iters),
        },
        "cell_audit": cell_audit,
        "regime_predictions": regime_records,
        "linear_predictions": linear_records,
        "hard_boundaries": h,
        "interpretation_boundary": "Retrospective disjoint exploratory research only. No formal promotion; formal_weight remains 0."
    }
    args.out.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (args.out / "r39x_result.json").write_text(raw, encoding="utf-8")
    (args.out / "r39x_result.sha256").write_text(hashlib.sha256(raw.encode()).hexdigest() + "\n", encoding="ascii")
    print(json.dumps({
        "terminal": terminal,
        "passed": passed,
        "r39x_fixed100_identity_sha256": out["r39x_fixed100_identity_sha256"],
        "overlap": out["overlap"],
        "regime_metrics": rm,
        "linear_metrics": lm,
        "regime_draw_auc": regime_auc,
        "linear_draw_auc": linear_auc,
        "gate": gate,
        "solver": out["solver"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
