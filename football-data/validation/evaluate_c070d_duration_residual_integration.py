from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

import evaluate_c069_matched_pair_draw_state_r1 as c69
import evaluate_c069_r2_maxcard_coverage as r2
import evaluate_c070c_semimarkov_generator as c70c


SCHEMA_VERSION = "C070D_DURATION_RESIDUAL_INTEGRATION_V1"
EPS = 1e-6
BOOT_REPS = 2000
BOOT_SEED = 7004
EXPECTED_PAIRS = c70c.EXPECTED_PAIRS
FOLDS = c70c.FOLDS


def _logit(p: np.ndarray | float) -> np.ndarray:
    x = np.clip(np.asarray(p, float), EPS, 1.0 - EPS)
    return np.log(x / (1.0 - x))


def _sigmoid(x: np.ndarray | float) -> np.ndarray:
    z = np.asarray(x, float)
    out = np.empty_like(z, dtype=float)
    pos = z >= 0
    out[pos] = 1.0 / (1.0 + np.exp(-z[pos]))
    ez = np.exp(z[~pos])
    out[~pos] = ez / (1.0 + ez)
    return np.clip(out, EPS, 1.0 - EPS)


def _integrate(p_inc: np.ndarray, p_markov: np.ndarray, p_semi: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    shift = _logit(p_semi) - _logit(p_markov)
    candidate = _sigmoid(_logit(p_inc) + shift)
    return candidate, shift


def _metric(frame: pd.DataFrame, p: np.ndarray) -> dict:
    return {
        **c69._metric(frame["y"].to_numpy(int), np.asarray(p, float)),
        "pair_accuracy": c69._pair_accuracy(frame, np.asarray(p, float)),
    }


def _delta(candidate: dict, baseline: dict) -> dict:
    return {
        k: float(candidate[k] - baseline[k])
        for k in ("log_loss", "brier", "auc", "accuracy", "pair_accuracy")
    }


def _pair_bootstrap(frame: pd.DataFrame, p0: np.ndarray, p1: np.ndarray) -> dict:
    y = frame["y"].to_numpy(int)
    p0 = np.clip(np.asarray(p0, float), 1e-12, 1.0 - 1e-12)
    p1 = np.clip(np.asarray(p1, float), 1e-12, 1.0 - 1e-12)
    l0 = -(y * np.log(p0) + (1 - y) * np.log(1 - p0))
    l1 = -(y * np.log(p1) + (1 - y) * np.log(1 - p1))
    t = frame[["pair_id"]].copy()
    t["delta"] = l1 - l0
    arr = t.groupby("pair_id")["delta"].mean().to_numpy(float)
    rng = np.random.default_rng(BOOT_SEED)
    sims = np.empty(BOOT_REPS, dtype=float)
    n = len(arr)
    for i in range(BOOT_REPS):
        sims[i] = float(np.mean(arr[rng.integers(0, n, size=n)]))
    return {
        "pair_count": int(n),
        "mean_delta_log_loss": float(arr.mean()),
        "ci90_low": float(np.quantile(sims, 0.05)),
        "ci90_high": float(np.quantile(sims, 0.95)),
        "reps": BOOT_REPS,
        "seed": BOOT_SEED,
    }


def _calibration_diagnostic(y: np.ndarray, p: np.ndarray) -> dict:
    # Descriptive only. It is never used to alter probabilities or gates.
    x = _logit(p).reshape(-1, 1)
    y = np.asarray(y, int)
    try:
        model = LogisticRegression(C=1e6, max_iter=5000, class_weight=None, random_state=0)
        model.fit(x, y)
        return {
            "intercept": float(model.intercept_[0]),
            "slope": float(model.coef_[0, 0]),
            "diagnostic_only": True,
        }
    except Exception as exc:
        return {"intercept": None, "slope": None, "diagnostic_only": True, "error": type(exc).__name__}


def _simulate_fold(markov, semi, test_pairs: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, dict]:
    q_markov = []
    q_semi = []
    cap_markov = []
    cap_semi = []
    for _, row in test_pairs.iterrows():
        qm, cm = c70c._simulate_q(
            markov,
            float(row["lambda_home"]),
            float(row["lambda_away"]),
            c70c.MARKOV_FEATURES,
        )
        qs, cs = c70c._simulate_q(
            semi,
            float(row["lambda_home"]),
            float(row["lambda_away"]),
            c70c.SEMIMARKOV_FEATURES,
        )
        q_markov.append(qm)
        q_semi.append(qs)
        cap_markov.append(cm)
        cap_semi.append(cs)
    return (
        np.asarray(q_markov, float),
        np.asarray(q_semi, float),
        {
            "mean_cap_mass_markov": float(np.mean(cap_markov)),
            "mean_cap_mass_semimarkov": float(np.mean(cap_semi)),
        },
    )


def run(a01: Path, a02: Path, a03: Path, matches_zip: Path, out: Path) -> None:
    comp_file, matches, events, union_sha, a03_sha = c70c.r3._merge_three(c69, a01, a02, a03, matches_zip)
    regular, skipped = c70c._regular_rows(comp_file, matches)
    prematch = c70c._build_prematch(regular)
    minute, minute_diag = c70c._minute_rows(prematch, events)

    eligible = prematch[
        (prematch["hn"] >= c69.MIN_PRIOR_TEAM_MATCHES)
        & (prematch["an"] >= c69.MIN_PRIOR_TEAM_MATCHES)
        & prematch["target"].isin(["D", "OW"])
    ].copy().sort_values(["dt", "match_id"]).reset_index(drop=True)

    source = {
        "packages": ["A01", "A02", "A03"],
        "source_matches": int(len(matches)),
        "union_ids_sha256_sorted": union_sha,
        "a03_ids_sha256_ordered": a03_sha,
        "regular_matches": int(len(regular)),
        "skipped_nonregular_matches": int(skipped),
        "eligible_rows": int(len(eligible)),
        "eligible_draws": int((eligible["target"] == "D").sum()),
        "eligible_onegoal_wins": int((eligible["target"] == "OW").sum()),
        "minute_diagnostics": minute_diag,
    }
    for key, expected in c70c.EXPECTED_SOURCE.items():
        if int(source[key]) != int(expected):
            raise RuntimeError(f"source integrity {key}={source[key]} expected={expected}")
    if minute_diag["score_fallback_matches"] != [2499781]:
        raise RuntimeError(f"fallback identity mismatch {minute_diag['score_fallback_matches']}")

    folds = {}
    pooled_struct_rows = []
    pooled_struct_markov = []
    pooled_struct_semi = []
    pooled_draw_rows = []
    pooled_inc = []
    pooled_markov = []
    pooled_semi = []
    pooled_candidate = []
    pooled_shift = []
    structural_wins = 0
    integration_wins = 0
    calipers = dict(c69.MATCH_CALIPERS)

    for name, (train_end_s, test_start_s, test_end_s) in FOLDS.items():
        train_end = pd.Timestamp(train_end_s).date()
        test_start = pd.Timestamp(test_start_s).date()
        test_end = pd.Timestamp(test_end_s).date() if test_end_s else None

        structural_train = minute[(minute["date"] < train_end) & minute["include_structural"]].copy()
        structural_test = minute[(minute["date"] >= test_start) & minute["include_structural"]].copy()
        if test_end is not None:
            structural_test = structural_test[structural_test["date"] < test_end].copy()
        markov = c70c._fit_multinomial(structural_train, c70c.MARKOV_FEATURES)
        semi = c70c._fit_multinomial(structural_train, c70c.SEMIMARKOV_FEATURES)
        p0 = markov.predict_proba(structural_test[c70c.MARKOV_FEATURES])
        p1 = semi.predict_proba(structural_test[c70c.SEMIMARKOV_FEATURES])
        m0 = c70c._struct_metric(structural_test, p0)
        m1 = c70c._struct_metric(structural_test, p1)
        d_struct = float(m1["log_loss"] - m0["log_loss"])
        structural_wins += int(d_struct < 0)

        target_train = eligible[eligible["date"] < train_end].copy()
        target_test = eligible[eligible["date"] >= test_start].copy()
        if test_end is not None:
            target_test = target_test[target_test["date"] < test_end].copy()
        train_meta, train_cert = r2._optimal_pairs(target_train, f"{name}-c070d-train", calipers)
        test_meta, test_cert = r2._optimal_pairs(target_test, f"{name}-c070d-test", calipers)
        exp_train, exp_test = EXPECTED_PAIRS[name]
        if (len(train_meta), len(test_meta), train_cert, test_cert) != (exp_train, exp_test, exp_train, exp_test):
            raise RuntimeError(
                f"{name} pair integrity got={len(train_meta)}/{len(test_meta)} cert={train_cert}/{test_cert}"
            )
        train_pairs = c70c._pair_rows(target_train, train_meta)
        test_pairs = c70c._pair_rows(target_test, test_meta)
        p_inc = c70c._fit_incumbent(train_pairs, test_pairs)
        q_markov, q_semi, cap = _simulate_fold(markov, semi, test_pairs)
        p_candidate, shift = _integrate(p_inc, q_markov, q_semi)

        mi = _metric(test_pairs, p_inc)
        mm = _metric(test_pairs, q_markov)
        ms = _metric(test_pairs, q_semi)
        mc = _metric(test_pairs, p_candidate)
        dc = _delta(mc, mi)
        integration_wins += int(dc["log_loss"] < 0)

        folds[name] = {
            "structural_parent_reproduction": {
                "train_rows": int(len(structural_train)),
                "test_rows": int(len(structural_test)),
                "markov": m0,
                "semimarkov": m1,
                "delta_log_loss_semimarkov_minus_markov": d_struct,
            },
            "matched_draw": {
                "train_pairs": int(len(train_meta)),
                "test_pairs": int(len(test_meta)),
                "train_maximum_cardinality_certificate": int(train_cert),
                "test_maximum_cardinality_certificate": int(test_cert),
                "incumbent": mi,
                "markov_generator": mm,
                "semimarkov_generator": ms,
                "integrated_candidate": mc,
                "candidate_minus_incumbent": dc,
                "duration_logodds_shift": {
                    "mean": float(np.mean(shift)),
                    "std": float(np.std(shift, ddof=0)),
                    "min": float(np.min(shift)),
                    "max": float(np.max(shift)),
                },
                **cap,
            },
        }
        pooled_struct_rows.append(structural_test)
        pooled_struct_markov.append(p0)
        pooled_struct_semi.append(p1)
        pooled_draw_rows.append(test_pairs)
        pooled_inc.append(p_inc)
        pooled_markov.append(q_markov)
        pooled_semi.append(q_semi)
        pooled_candidate.append(p_candidate)
        pooled_shift.append(shift)

    all_struct = pd.concat(pooled_struct_rows, ignore_index=True)
    ps0 = np.vstack(pooled_struct_markov)
    ps1 = np.vstack(pooled_struct_semi)
    parent_m0 = c70c._struct_metric(all_struct, ps0)
    parent_m1 = c70c._struct_metric(all_struct, ps1)
    parent_delta = float(parent_m1["log_loss"] - parent_m0["log_loss"])
    parent_boot = c70c._struct_bootstrap(all_struct, ps0, ps1)
    parent_guard = bool(
        structural_wins == 3
        and parent_delta < 0
        and parent_boot["ci90_high"] < 0
    )
    if not parent_guard:
        result = {
            "schema_version": SCHEMA_VERSION,
            "status": "STOP_PARENT_REPRODUCTION",
            "verdict": "STOP_PARENT_REPRODUCTION",
            "source": source,
            "parent_reproduction": {
                "markov": parent_m0,
                "semimarkov": parent_m1,
                "delta_log_loss_semimarkov_minus_markov": parent_delta,
                "fold_logloss_wins": int(structural_wins),
                "match_bootstrap": parent_boot,
                "guard_pass": False,
            },
            "boundary": {
                "candidate_scored": False,
                "protected_samples_used": False,
                "new_A_packages_opened": [],
                "formal_weight": 0,
            },
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    all_draw = pd.concat(pooled_draw_rows, ignore_index=True)
    p_inc = np.concatenate(pooled_inc)
    q_markov = np.concatenate(pooled_markov)
    q_semi = np.concatenate(pooled_semi)
    p_candidate = np.concatenate(pooled_candidate)
    shift = np.concatenate(pooled_shift)

    mi = _metric(all_draw, p_inc)
    mm = _metric(all_draw, q_markov)
    ms = _metric(all_draw, q_semi)
    mc = _metric(all_draw, p_candidate)
    dc = _delta(mc, mi)
    boot = _pair_bootstrap(all_draw, p_inc, p_candidate)
    signal = bool(
        dc["log_loss"] < 0
        and boot["ci90_high"] < 0
        and integration_wins >= 2
        and dc["brier"] <= 0
    )

    # Incumbent identity is a hard integrity check inherited from C069/C070-C.
    c70c._assert_close("incumbent pooled LL", mi["log_loss"], c70c.EXPECTED_INCUMBENT_LL)
    c70c._assert_close("incumbent pooled Brier", mi["brier"], c70c.EXPECTED_INCUMBENT_BRIER)
    c70c._assert_close("incumbent pooled pair_accuracy", mi["pair_accuracy"], c70c.EXPECTED_INCUMBENT_PAIR_ACC)

    result = {
        "schema_version": SCHEMA_VERSION,
        "status": "POSTVIEW_C070D_DEVELOPMENT_COMPLETE",
        "verdict": (
            "C070D_DURATION_RESIDUAL_DEVELOPMENT_SIGNAL"
            if signal else "C070D_DURATION_RESIDUAL_INCREMENT_NOT_ESTABLISHED"
        ),
        "source": source,
        "parent_reproduction": {
            "markov": parent_m0,
            "semimarkov": parent_m1,
            "delta_log_loss_semimarkov_minus_markov": parent_delta,
            "fold_logloss_wins": int(structural_wins),
            "match_bootstrap": parent_boot,
            "guard_pass": True,
        },
        "integration_rule_echo": {
            "epsilon": EPS,
            "formula": "logit(p_candidate)=logit(p_incumbent)+logit(p_semimarkov)-logit(p_markov)",
            "learned_weight": False,
            "learned_intercept": False,
            "posthoc_calibration": False,
            "blend_search": False,
        },
        "folds": folds,
        "pooled": {
            "test_rows": int(len(all_draw)),
            "test_pairs": int(all_draw["pair_id"].nunique()),
            "incumbent": mi,
            "markov_generator": mm,
            "semimarkov_generator": ms,
            "integrated_candidate": mc,
            "candidate_minus_incumbent": dc,
            "fold_logloss_wins": int(integration_wins),
            "pair_bootstrap": boot,
            "duration_logodds_shift": {
                "mean": float(np.mean(shift)),
                "std": float(np.std(shift, ddof=0)),
                "min": float(np.min(shift)),
                "max": float(np.max(shift)),
            },
            "calibration_diagnostic_incumbent": _calibration_diagnostic(all_draw["y"].to_numpy(int), p_inc),
            "calibration_diagnostic_candidate": _calibration_diagnostic(all_draw["y"].to_numpy(int), p_candidate),
        },
        "development_signal": signal,
        "boundary": {
            "post_view_development_only": True,
            "protected_samples_used": False,
            "new_A_packages_opened": [],
            "formal_weight": 0,
            "scientific_pass": False,
            "confirmation_pass": False,
            "formal_promotion_allowed": False,
            "failed_c070a_features_used": False,
            "failed_c070b_features_used": False,
            "learned_integration_weight": False,
            "posthoc_probability_calibration_performed": False,
            "model_scoring_performed": True,
            "claim_scope": "development integration signal only; fixed residual transport, no confirmation claim",
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--a01", required=True)
    p.add_argument("--a02", required=True)
    p.add_argument("--a03", required=True)
    p.add_argument("--matches-zip", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    run(Path(a.a01), Path(a.a02), Path(a.a03), Path(a.matches_zip), Path(a.out))


if __name__ == "__main__":
    main()
