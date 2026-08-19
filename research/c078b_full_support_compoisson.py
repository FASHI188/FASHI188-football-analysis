#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import gammaln, logsumexp

import c078a_full_support_directt as c078a

GRID_MAX = 100
K = np.arange(GRID_MAX + 1, dtype=float)
LOGFACT = gammaln(K + 1.0)
NU_MIN = 1.0
NU_MAX = 5.0
NU_START = 1.15
BOOT_EXACT = 5000
BOOT_EXACT_SEED = 78101
BOOT_TAIL = 5000
BOOT_TAIL_SEED = 78102
BOOT_CAL = 5000
BOOT_CAL8_SEED = 78108
BOOT_CAL9_SEED = 78109
NORMALIZATION_TAIL_BOUND_MAX = 1e-12
TAIL_FOLD_MIN = 30


def comp_chunk_stats(eta: np.ndarray, nu: float):
    eta = np.clip(np.asarray(eta, dtype=float), c078a.ETA_MIN, c078a.ETA_MAX)
    logw = eta[:, None] * K[None, :] - nu * LOGFACT[None, :]
    logz = logsumexp(logw, axis=1)
    probs = np.exp(logw - logz[:, None])
    e_k = probs @ K
    e_logfact = probs @ LOGFACT

    lam = np.exp(eta)
    q = lam / (GRID_MAX + 1.0) ** nu
    log_a_last_rel = logw[:, -1] - logz
    tail_upper = np.full(len(eta), np.inf, dtype=float)
    good = q < 1.0
    tail_upper[good] = np.exp(log_a_last_rel[good]) * q[good] / np.maximum(1.0 - q[good], 1e-300)
    return logz, probs, e_k, e_logfact, tail_upper


def comp_objective(theta: np.ndarray, X: np.ndarray, y: np.ndarray, chunk_size: int = 4096):
    beta = np.asarray(theta[:-1], dtype=float)
    z = float(theta[-1])
    nu = math.exp(z)
    n = len(y)
    total_nll = 0.0
    grad_beta = np.zeros_like(beta)
    grad_z = 0.0
    for start in range(0, n, chunk_size):
        stop = min(start + chunk_size, n)
        Xc = X[start:stop]
        yc = y[start:stop].astype(float)
        eta = np.clip(Xc @ beta, c078a.ETA_MIN, c078a.ETA_MAX)
        logz, _, e_k, e_logfact, _ = comp_chunk_stats(eta, nu)
        logfact_y = gammaln(yc + 1.0)
        logp = yc * eta - nu * logfact_y - logz
        total_nll += -float(np.sum(logp))
        grad_beta += Xc.T @ (e_k - yc)
        grad_z += float(np.sum(nu * (logfact_y - e_logfact)))
    return total_nll / n, np.concatenate([grad_beta / n, np.array([grad_z / n])])


def fit_comp(X: np.ndarray, y: np.ndarray, poisson_beta: np.ndarray):
    init = np.concatenate([np.asarray(poisson_beta, dtype=float), np.array([math.log(NU_START)])])
    bounds = [(None, None)] * len(poisson_beta) + [(math.log(NU_MIN), math.log(NU_MAX))]
    return minimize(
        lambda th: comp_objective(th, X, y),
        init,
        method="L-BFGS-B",
        jac=True,
        bounds=bounds,
        options={"maxiter": 5000, "ftol": 1e-10, "gtol": 1e-10, "maxls": 50},
    )


def normalization_bound(theta: np.ndarray, X: np.ndarray, chunk_size: int = 4096):
    beta = theta[:-1]
    nu = math.exp(float(theta[-1]))
    max_bound = 0.0
    for start in range(0, len(X), chunk_size):
        eta = np.clip(X[start:start+chunk_size] @ beta, c078a.ETA_MIN, c078a.ETA_MAX)
        *_, bound = comp_chunk_stats(eta, nu)
        if not np.isfinite(bound).all():
            return float("inf")
        max_bound = max(max_bound, float(np.max(bound)))
    return max_bound


def comp_distribution_arrays(theta: np.ndarray, X: np.ndarray, chunk_size: int = 4096):
    beta = theta[:-1]
    nu = math.exp(float(theta[-1]))
    n = len(X)
    pmf = np.empty((n, c078a.MAX_T + 1), dtype=float)
    sf60 = np.empty(n, dtype=float)
    sf6 = np.empty(n, dtype=float)
    sf7 = np.empty(n, dtype=float)
    sf8 = np.empty(n, dtype=float)
    bounds = np.empty(n, dtype=float)
    conservation = np.empty(n, dtype=float)
    for start in range(0, n, chunk_size):
        stop = min(start + chunk_size, n)
        eta = np.clip(X[start:stop] @ beta, c078a.ETA_MIN, c078a.ETA_MAX)
        _, probs, _, _, tail_bound = comp_chunk_stats(eta, nu)
        # Final fitted tail beyond 100 must be <=1e-12. We use the explicitly normalized
        # 0..100 grid for scoring; the audited omitted full-support mass is negligible and bounded.
        p60 = probs[:, :c078a.MAX_T + 1]
        pmf[start:stop] = p60
        sf60[start:stop] = probs[:, c078a.MAX_T + 1:].sum(axis=1)
        sf6[start:stop] = probs[:, 7:].sum(axis=1)
        sf7[start:stop] = probs[:, 8:].sum(axis=1)
        sf8[start:stop] = probs[:, 9:].sum(axis=1)
        bounds[start:stop] = tail_bound
        conservation[start:stop] = np.abs(probs.sum(axis=1) - 1.0)
    if not np.isfinite(pmf).all() or not np.isfinite(sf60).all() or np.any(pmf < 0) or np.any(sf60 < 0):
        raise RuntimeError("invalid COM-Poisson probabilities")
    return {
        "pmf": pmf, "sf60": sf60, "sf6": sf6, "sf7": sf7, "sf8": sf8,
        "conservation": conservation, "normalization_tail_upper": bounds, "nu": nu,
    }


def paired_bootstrap(delta: np.ndarray, reps: int, seed: int):
    d = np.asarray(delta, dtype=float)
    rng = np.random.default_rng(seed)
    sims = np.empty(reps, dtype=float)
    for i in range(reps):
        idx = rng.integers(0, len(d), size=len(d))
        sims[i] = float(d[idx].mean())
    return {
        "n": int(len(d)), "reps": reps, "seed": seed, "mean_delta": float(d.mean()),
        "ci90_low": float(np.quantile(sims, 0.05)), "ci90_high": float(np.quantile(sims, 0.95)),
        "p_delta_lt_zero": float(np.mean(sims < 0)),
    }


def residual_bootstrap(residual: np.ndarray, seed: int):
    r = np.asarray(residual, dtype=float)
    rng = np.random.default_rng(seed)
    sims = np.empty(BOOT_CAL, dtype=float)
    for i in range(BOOT_CAL):
        idx = rng.integers(0, len(r), size=len(r))
        sims[i] = float(r[idx].mean())
    lo = float(np.quantile(sims, 0.05)); hi = float(np.quantile(sims, 0.95))
    return {
        "n": int(len(r)), "mean_residual": float(r.mean()), "reps": BOOT_CAL, "seed": seed,
        "ci90_low": lo, "ci90_high": hi, "contains_zero": bool(lo <= 0 <= hi),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)

    df, inventory = c078a.load_source(Path(args.source_root))
    feat = c078a.build_history_features(df)
    eligible = feat[feat["eligible"]].copy().reset_index(drop=True)

    folds = []
    pooled = []
    all_optimizer_success = True
    max_norm_tail_bound = 0.0
    max_conservation = 0.0
    max_residual61 = 0.0
    nu_above_lower_count = 0
    all_nu_below_upper = True

    for test_season in c078a.TEST_SEASONS:
        sy = c078a.season_start(test_season)
        train = eligible[eligible["season_start"] < sy].copy().reset_index(drop=True)
        test = eligible[eligible["Season"] == test_season].copy().reset_index(drop=True)
        Xtr, Xte = c078a.preprocess(train, test, c078a.FEATURES)
        ytr = train["T_exact"].to_numpy(int)
        yte = test["T_exact"].to_numpy(int)

        pres = c078a.fit_poisson(Xtr, ytr)
        cres = fit_comp(Xtr, ytr, pres.x)
        p_ok = bool(pres.success); c_ok = bool(cres.success)
        all_optimizer_success &= p_ok and c_ok
        nu = math.exp(float(cres.x[-1]))
        if nu > 1.0001:
            nu_above_lower_count += 1
        if nu >= 4.999:
            all_nu_below_upper = False

        train_bound = normalization_bound(cres.x, Xtr)
        test_bound = normalization_bound(cres.x, Xte)
        max_norm_tail_bound = max(max_norm_tail_bound, train_bound, test_bound)

        pdist = c078a.distribution_arrays("poisson", pres.x, Xte)
        cdist = comp_distribution_arrays(cres.x, Xte)
        max_conservation = max(max_conservation, float(np.max(pdist["conservation"])), float(np.max(cdist["conservation"])))
        max_residual61 = max(max_residual61, float(np.max(pdist["sf60"])), float(np.max(cdist["sf60"])))

        pm = c078a.exact_row_metrics(yte, pdist)
        cm = c078a.exact_row_metrics(yte, cdist)
        pt = c078a.tail_row_metrics(yte, pdist)
        ct = c078a.tail_row_metrics(yte, cdist)

        y8tr = np.minimum(ytr, 7); y8te = np.minimum(yte, 7)
        cat_base = c078a.categorical_pipeline(); cat_move = c078a.categorical_pipeline()
        cat_base.fit(train[c078a.BASE_FEATURES], y8tr)
        cat_move.fit(train[c078a.FEATURES], y8tr)
        cbp = c078a.aligned_cat_proba(cat_base, test[c078a.BASE_FEATURES])
        cmp = c078a.aligned_cat_proba(cat_move, test[c078a.FEATURES])
        cbm = c078a.cat_metrics(y8te, cbp); cmm = c078a.cat_metrics(y8te, cmp)
        p8m = c078a.cat_metrics(y8te, c078a.collapsed8(pdist))
        comp8m = c078a.cat_metrics(y8te, c078a.collapsed8(cdist))

        fold = {
            "test_season": test_season, "train_n": int(len(train)), "test_n": int(len(test)),
            "tail_n": int(np.sum(yte >= 7)),
            "poisson_optimizer": {"success": p_ok, "status": int(pres.status), "message": str(pres.message), "nit": int(pres.nit), "fun": float(pres.fun)},
            "compoisson_optimizer": {"success": c_ok, "status": int(cres.status), "message": str(cres.message), "nit": int(cres.nit), "fun": float(cres.fun), "nu": float(nu), "train_norm_tail_upper": float(train_bound), "test_norm_tail_upper": float(test_bound)},
            "exact_delta": {
                "logloss": float(np.mean(cm["ll"] - pm["ll"])),
                "brier": float(np.mean(cm["brier"] - pm["brier"])),
                "rps": float(np.mean(cm["rps"] - pm["rps"])),
                "top1": float(np.mean(cm["top1"] - pm["top1"])),
            },
            "tail_delta": {
                "logloss": float(np.mean(ct["ll"] - pt["ll"])),
                "brier": float(np.mean(ct["brier"] - pt["brier"])),
                "rps": float(np.mean(ct["rps"] - pt["rps"])),
            },
        }
        folds.append(fold)

        tail_positions = np.flatnonzero(yte >= 7)
        for i in range(len(test)):
            row = {
                "test_season": test_season, "date": str(test.iloc[i]["date"].date()),
                "league_key": str(test.iloc[i]["league_key"]), "HomeTeam": str(test.iloc[i]["HomeTeam"]), "AwayTeam": str(test.iloc[i]["AwayTeam"]),
                "T_exact": int(yte[i]), "is_tail": bool(yte[i] >= 7),
                "poisson_exact_ll": float(pm["ll"][i]), "compoisson_exact_ll": float(cm["ll"][i]),
                "poisson_exact_brier": float(pm["brier"][i]), "compoisson_exact_brier": float(cm["brier"][i]),
                "poisson_exact_rps": float(pm["rps"][i]), "compoisson_exact_rps": float(cm["rps"][i]),
                "poisson_exact_top1": float(pm["top1"][i]), "compoisson_exact_top1": float(cm["top1"][i]),
                "cat_scorehistory_ll": float(cbm["ll"][i]), "cat_movement_ll": float(cmm["ll"][i]),
                "poisson_collapsed_ll": float(p8m["ll"][i]), "compoisson_collapsed_ll": float(comp8m["ll"][i]),
                "cat_scorehistory_brier": float(cbm["brier"][i]), "cat_movement_brier": float(cmm["brier"][i]),
                "poisson_collapsed_brier": float(p8m["brier"][i]), "compoisson_collapsed_brier": float(comp8m["brier"][i]),
                "cat_scorehistory_rps": float(cbm["rps"][i]), "cat_movement_rps": float(cmm["rps"][i]),
                "poisson_collapsed_rps": float(p8m["rps"][i]), "compoisson_collapsed_rps": float(comp8m["rps"][i]),
                "poisson_tail_ll": None, "compoisson_tail_ll": None,
                "poisson_tail_brier": None, "compoisson_tail_brier": None,
                "poisson_tail_rps": None, "compoisson_tail_rps": None,
                "compoisson_p8_cond": None, "compoisson_p9_cond": None,
            }
            if yte[i] >= 7:
                j = int(np.where(tail_positions == i)[0][0])
                row.update({
                    "poisson_tail_ll": float(pt["ll"][j]), "compoisson_tail_ll": float(ct["ll"][j]),
                    "poisson_tail_brier": float(pt["brier"][j]), "compoisson_tail_brier": float(ct["brier"][j]),
                    "poisson_tail_rps": float(pt["rps"][j]), "compoisson_tail_rps": float(ct["rps"][j]),
                    "compoisson_p8_cond": float(ct["p8_cond"][j]), "compoisson_p9_cond": float(ct["p9_cond"][j]),
                })
            pooled.append(row)

    rows = pd.DataFrame(pooled)
    rows.to_csv(out / "row_metrics.csv", index=False)
    tail = rows[rows["is_tail"]].copy()

    exact = {
        "n": int(len(rows)),
        "poisson": {"logloss": float(rows.poisson_exact_ll.mean()), "brier": float(rows.poisson_exact_brier.mean()), "rps": float(rows.poisson_exact_rps.mean()), "top1": float(rows.poisson_exact_top1.mean())},
        "compoisson": {"logloss": float(rows.compoisson_exact_ll.mean()), "brier": float(rows.compoisson_exact_brier.mean()), "rps": float(rows.compoisson_exact_rps.mean()), "top1": float(rows.compoisson_exact_top1.mean())},
    }
    exact["delta"] = {k: float(exact["compoisson"][k] - exact["poisson"][k]) for k in exact["poisson"]}
    exact_boot = paired_bootstrap((rows.compoisson_exact_ll - rows.poisson_exact_ll).to_numpy(float), BOOT_EXACT, BOOT_EXACT_SEED)

    tails = {
        "n": int(len(tail)),
        "poisson": {"logloss": float(tail.poisson_tail_ll.mean()), "brier": float(tail.poisson_tail_brier.mean()), "rps": float(tail.poisson_tail_rps.mean())},
        "compoisson": {"logloss": float(tail.compoisson_tail_ll.mean()), "brier": float(tail.compoisson_tail_brier.mean()), "rps": float(tail.compoisson_tail_rps.mean())},
    }
    tails["delta"] = {k: float(tails["compoisson"][k] - tails["poisson"][k]) for k in tails["poisson"]}
    tail_boot = paired_bootstrap((tail.compoisson_tail_ll - tail.poisson_tail_ll).to_numpy(float), BOOT_TAIL, BOOT_TAIL_SEED)
    cal8 = residual_bootstrap((tail.compoisson_p8_cond - (tail.T_exact >= 8).astype(float)).to_numpy(float), BOOT_CAL8_SEED)
    cal9 = residual_bootstrap((tail.compoisson_p9_cond - (tail.T_exact >= 9).astype(float)).to_numpy(float), BOOT_CAL9_SEED)

    collapsed = {
        "n": int(len(rows)),
        "categorical_scorehistory": {"logloss": float(rows.cat_scorehistory_ll.mean()), "brier": float(rows.cat_scorehistory_brier.mean()), "rps": float(rows.cat_scorehistory_rps.mean())},
        "categorical_movement": {"logloss": float(rows.cat_movement_ll.mean()), "brier": float(rows.cat_movement_brier.mean()), "rps": float(rows.cat_movement_rps.mean())},
        "poisson": {"logloss": float(rows.poisson_collapsed_ll.mean()), "brier": float(rows.poisson_collapsed_brier.mean()), "rps": float(rows.poisson_collapsed_rps.mean())},
        "compoisson": {"logloss": float(rows.compoisson_collapsed_ll.mean()), "brier": float(rows.compoisson_collapsed_brier.mean()), "rps": float(rows.compoisson_collapsed_rps.mean())},
    }

    exact_fold_wins = sum(f["exact_delta"]["logloss"] < 0 for f in folds)
    tail_eligible = [f for f in folds if f["tail_n"] >= TAIL_FOLD_MIN]
    tail_fold_wins = sum(f["tail_delta"]["logloss"] < 0 for f in tail_eligible)

    gates = {
        "exact_pooled_dll_lt0": exact["delta"]["logloss"] < 0,
        "exact_boot90_upper_lt0": exact_boot["ci90_high"] < 0,
        "exact_fold_wins_ge4of5": exact_fold_wins >= 4,
        "exact_brier_nonworse": exact["delta"]["brier"] <= 0,
        "exact_rps_nonworse": exact["delta"]["rps"] <= 0,
        "tail_pooled_dll_lt0": tails["delta"]["logloss"] < 0,
        "tail_boot90_upper_lt0": tail_boot["ci90_high"] < 0,
        "tail_brier_nonworse": tails["delta"]["brier"] <= 0,
        "tail_rps_nonworse": tails["delta"]["rps"] <= 0,
        "tail_eligible_folds_ge3": len(tail_eligible) >= 3,
        "tail_strict_majority_fold_wins": len(tail_eligible) >= 3 and tail_fold_wins > len(tail_eligible) / 2,
        "tail_8plus_calibration_ci_contains0": bool(cal8["contains_zero"]),
        "tail_9plus_calibration_ci_contains0": bool(cal9["contains_zero"]),
        "collapsed_compoisson_nonworse_than_poisson_ll": collapsed["compoisson"]["logloss"] <= collapsed["poisson"]["logloss"],
        "collapsed_compoisson_beats_c074e_movement_ll": collapsed["compoisson"]["logloss"] < collapsed["categorical_movement"]["logloss"],
        "all_optimizers_success": bool(all_optimizer_success),
        "nu_above_lower_in_ge4of5": nu_above_lower_count >= 4,
        "all_nu_below_upper": bool(all_nu_below_upper),
        "normalization_tail_bound_le_1e_12": max_norm_tail_bound <= NORMALIZATION_TAIL_BOUND_MAX,
        "probability_conservation": max_conservation <= 1e-10,
        "residual_beyond60": max_residual61 <= 1e-8,
    }
    status = "FULL_SUPPORT_THIN_TAIL_DEVELOPMENT_PASS_POSTVIEW" if all(gates.values()) else "FAIL_PARK"

    summary = {
        "schema_version": "C078B_FULL_SUPPORT_COMPOISSON_V1",
        "status": status,
        "evidence_class": "POST_C078A_DEVELOPMENT_NOT_INDEPENDENT_CONFIRMATION",
        "source_revision": c078a.SOURCE_REVISION,
        "source_inventory": inventory,
        "valid_source_rows": int(len(df)), "eligible_rows": int(len(eligible)),
        "test_seasons": c078a.TEST_SEASONS,
        "features": c078a.FEATURES, "feature_count": len(c078a.FEATURES),
        "models": {"baseline": "Poisson", "candidate": "standard COM-Poisson, global nu in [1,5]"},
        "folds": folds,
        "pooled_exact": exact, "exact_bootstrap": exact_boot,
        "pooled_tail": tails, "tail_bootstrap": tail_boot,
        "tail_calibration": {"8plus": cal8, "9plus": cal9},
        "collapsed8": collapsed,
        "stability": {"exact_fold_wins": int(exact_fold_wins), "tail_eligible_fold_count": int(len(tail_eligible)), "tail_fold_wins": int(tail_fold_wins), "nu_above_lower_count": int(nu_above_lower_count)},
        "numerical_audit": {"max_relative_normalization_tail_upper": float(max_norm_tail_bound), "max_probability_conservation_abs_residual": float(max_conservation), "max_P_T_ge_61": float(max_residual61)},
        "gates": gates,
        "no_rescue_after_view": True,
        "no_more_thin_tail_family_shopping_on_this_domain_if_fail": True,
        "formal_weight": 0,
        "C077B_labels_read": False,
        "C076D_4567_opened": False,
        "C071_reserve52180_opened": False,
        "C070F1597_opened": False,
        "A05_or_protected_opened": False,
        "exact_tail_formally_promoted": False,
        "unified_matrix_generated": False,
        "CURRENT_change": False,
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
