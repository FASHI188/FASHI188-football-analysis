#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

import c074e_scorehistory_movement_directt as c074e

SCHEMA = "C075F_HURDLE_TAIL_DEVELOPMENT_V1"
TEST_SEASONS = ["2019-2020", "2020-2021", "2021-2022", "2022-2023", "2023-2024"]
MIN_TRAIN_TAIL = 100
MIN_TEST_TAIL = 20
MIN_POOLED_TEST = 250
MIN_POSITIVE_TRAIN = 25
BOOT_REPS = 3000
BOOT_SEED = 75007
TAIL_RESIDUAL_LIMIT = 1e-8
PROB_RESIDUAL_LIMIT = 1e-10
FINAL_MAX_SEASON_START = 2023


def fit_simple(e: np.ndarray) -> float:
    e = np.asarray(e, dtype=float)
    if len(e) == 0:
        raise RuntimeError("empty simple-tail fit")
    return float(e.sum() / (e.sum() + len(e)))


def fit_hurdle(e: np.ndarray) -> tuple[float, float, int]:
    e = np.asarray(e, dtype=int)
    pos = e[e >= 1]
    if len(pos) == 0:
        raise RuntimeError("no positive tail rows for hurdle fit")
    pi = float(len(pos) / len(e))
    y = pos - 1
    rho = float(y.sum() / (y.sum() + len(y)))
    return pi, rho, int(len(pos))


def simple_exact_prob(e: np.ndarray, r: float) -> np.ndarray:
    e = np.asarray(e, dtype=int)
    return (1.0 - r) * np.power(r, e)


def hurdle_exact_prob(e: np.ndarray, pi: float, rho: float) -> np.ndarray:
    e = np.asarray(e, dtype=int)
    out = np.empty(len(e), dtype=float)
    zero = e == 0
    out[zero] = 1.0 - pi
    if (~zero).any():
        ep = e[~zero]
        out[~zero] = pi * (1.0 - rho) * np.power(rho, ep - 1)
    return out


def choose_k(r: float, pi: float, rho: float, observed_max: int) -> int:
    k = max(1, int(observed_max))
    while True:
        simple_res = float(r ** (k + 1))
        hurdle_res = float(pi * (rho ** k))
        if max(simple_res, hurdle_res) <= TAIL_RESIDUAL_LIMIT:
            return k
        k += 1
        if k > 500:
            raise RuntimeError("unable to close hurdle/simple tail residual")


def simple_vector(r: float, k: int) -> np.ndarray:
    e = np.arange(k + 1, dtype=int)
    p = simple_exact_prob(e, r)
    residual = float(r ** (k + 1))
    out = np.concatenate([p, np.asarray([residual])])
    return out / out.sum()


def hurdle_vector(pi: float, rho: float, k: int) -> np.ndarray:
    e = np.arange(k + 1, dtype=int)
    p = hurdle_exact_prob(e, pi, rho)
    residual = float(pi * (rho ** k))
    out = np.concatenate([p, np.asarray([residual])])
    return out / out.sum()


def row_metrics(e: np.ndarray, p_exact: np.ndarray, vec: np.ndarray, k: int) -> dict[str, np.ndarray]:
    e = np.asarray(e, dtype=int)
    ll = -np.log(np.clip(p_exact, 1e-300, 1.0))
    y = np.minimum(e, k + 1)
    pmat = np.repeat(vec[None, :], len(e), axis=0)
    one = np.zeros_like(pmat)
    one[np.arange(len(e)), y] = 1.0
    brier = np.square(pmat - one).sum(axis=1)
    cp = np.cumsum(pmat, axis=1)[:, :-1]
    cy = np.cumsum(one, axis=1)[:, :-1]
    rps = np.square(cp - cy).sum(axis=1) / max(1, pmat.shape[1] - 1)
    top1 = (np.argmax(pmat, axis=1) == y).astype(float)
    return {"logloss": ll, "brier": brier, "rps": rps, "top1": top1}


def summarize(rows: dict[str, np.ndarray]) -> dict[str, float]:
    return {k: float(np.mean(v)) for k, v in rows.items()}


def threshold_probs_simple(r: float) -> dict[int, float]:
    return {1: float(r), 2: float(r**2), 3: float(r**3)}


def threshold_probs_hurdle(pi: float, rho: float) -> dict[int, float]:
    return {1: float(pi), 2: float(pi * rho), 3: float(pi * rho**2)}


def threshold_brier(e: np.ndarray, probs: dict[int, float]) -> dict[str, float]:
    e = np.asarray(e, dtype=int)
    out = {}
    for n, p in probs.items():
        y = (e >= n).astype(float)
        out[f"E_ge_{n}"] = float(np.mean(np.square(p - y)))
    return out


def paired_bootstrap(delta: np.ndarray) -> dict[str, float | int]:
    d = np.asarray(delta, dtype=float)
    rng = np.random.default_rng(BOOT_SEED)
    sims = np.empty(BOOT_REPS, dtype=float)
    for i in range(BOOT_REPS):
        idx = rng.integers(0, len(d), size=len(d))
        sims[i] = float(d[idx].mean())
    return {
        "n": int(len(d)), "reps": BOOT_REPS, "seed": BOOT_SEED,
        "mean_delta": float(d.mean()),
        "ci90_low": float(np.quantile(sims, 0.05)),
        "ci90_high": float(np.quantile(sims, 0.95)),
        "probability_delta_lt_zero": float(np.mean(sims < 0)),
    }


def canonical_sha(obj: dict) -> str:
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--external-root", required=True)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    raw = c074e.load_source(Path(a.external_root))
    raw["exact_total"] = (raw["FTHG"].astype(int) + raw["FTAG"].astype(int)).astype(int)
    feat = c074e.build_history_features(raw)
    exact_map = raw["exact_total"].to_dict()
    feat["exact_total"] = feat["row_id"].map(exact_map).astype(int)
    elig = feat.loc[feat["eligible"]].copy().reset_index(drop=True)
    tail = elig.loc[elig["exact_total"] >= 7].copy().reset_index(drop=True)
    tail["excess"] = tail["exact_total"].astype(int) - 7

    coverage = {}
    coverage_pass = True
    for season in TEST_SEASONS:
        start = c074e.season_start(season)
        tr = tail[tail["season_start"] < start]
        te = tail[tail["Season"] == season]
        pos_train = int((tr["excess"] >= 1).sum())
        coverage[season] = {
            "train_tail_rows": int(len(tr)),
            "train_positive_tail_rows": pos_train,
            "test_tail_rows": int(len(te)),
        }
        coverage_pass &= (
            len(tr) >= MIN_TRAIN_TAIL and len(te) >= MIN_TEST_TAIL and pos_train >= MIN_POSITIVE_TRAIN
        )
    pooled_expected = int(sum(v["test_tail_rows"] for v in coverage.values()))
    coverage_pass &= pooled_expected >= MIN_POOLED_TEST

    boundary = {
        "C075C_consumed_tail_labels_used": False,
        "C075E_consumed_tail_labels_used": False,
        "C071_reserve_52180_opened": False,
        "C070F_confirmation1597_opened": False,
        "A05_opened": False,
        "protected_opened": False,
        "T_ge_7_D_given_T_tested": False,
        "unified_matrix_generated": False,
        "formal_weight": 0,
    }

    if not coverage_pass:
        summary = {
            "schema_version": SCHEMA,
            "status": "STOP_COVERAGE",
            "scientific_effect_evaluated": False,
            "post_forward_hypothesis_development_only": True,
            "coverage": coverage,
            "pooled_test_tail_expected": pooled_expected,
            "boundaries": boundary,
        }
        (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    folds = {}
    pooled_parts = []
    max_tail_residual = 0.0
    max_prob_residual = 0.0

    for season in TEST_SEASONS:
        start = c074e.season_start(season)
        tr = tail[tail["season_start"] < start].copy()
        te = tail[tail["Season"] == season].copy()
        etr = tr["excess"].to_numpy(int)
        ete = te["excess"].to_numpy(int)

        r = fit_simple(etr)
        pi, rho, npos = fit_hurdle(etr)
        k = choose_k(r, pi, rho, int(ete.max()))
        vb = simple_vector(r, k)
        vc = hurdle_vector(pi, rho, k)
        pb_exact = simple_exact_prob(ete, r)
        pc_exact = hurdle_exact_prob(ete, pi, rho)
        mb = row_metrics(ete, pb_exact, vb, k)
        mc = row_metrics(ete, pc_exact, vc, k)
        sb = summarize(mb)
        sc = summarize(mc)
        delta = {m: float(sc[m] - sb[m]) for m in sb}
        simple_residual = float(r ** (k + 1))
        hurdle_residual = float(pi * (rho ** k))
        prob_residual = float(max(abs(vb.sum() - 1.0), abs(vc.sum() - 1.0)))
        max_tail_residual = max(max_tail_residual, simple_residual, hurdle_residual)
        max_prob_residual = max(max_prob_residual, prob_residual)

        folds[season] = {
            "train_tail_rows": int(len(tr)),
            "train_positive_tail_rows": int(npos),
            "test_tail_rows": int(len(te)),
            "baseline_r": r,
            "candidate_pi": pi,
            "candidate_rho": rho,
            "observed_test_positive_fraction": float((ete >= 1).mean()),
            "observed_test_mean_excess": float(ete.mean()),
            "enumeration_K": int(k),
            "baseline": sb,
            "candidate": sc,
            "delta_candidate_minus_baseline": delta,
            "baseline_threshold_brier": threshold_brier(ete, threshold_probs_simple(r)),
            "candidate_threshold_brier": threshold_brier(ete, threshold_probs_hurdle(pi, rho)),
            "baseline_unenumerated_tail_residual": simple_residual,
            "candidate_unenumerated_tail_residual": hurdle_residual,
            "max_probability_sum_abs_residual": prob_residual,
        }

        temp = pd.DataFrame({
            "season": season,
            "league_key": te["league_key"].to_numpy(),
            "exact_total": te["exact_total"].to_numpy(int),
            "excess": ete,
            "baseline_ll": mb["logloss"],
            "candidate_ll": mc["logloss"],
            "delta_ll": mc["logloss"] - mb["logloss"],
            "baseline_brier": mb["brier"],
            "candidate_brier": mc["brier"],
            "baseline_rps": mb["rps"],
            "candidate_rps": mc["rps"],
            "baseline_top1": mb["top1"],
            "candidate_top1": mc["top1"],
        })
        pooled_parts.append(temp)

    rows = pd.concat(pooled_parts, ignore_index=True)
    rows.to_csv(out / "row_metrics.csv", index=False)

    pooled = {
        "baseline": {
            "logloss": float(rows["baseline_ll"].mean()),
            "brier": float(rows["baseline_brier"].mean()),
            "rps": float(rows["baseline_rps"].mean()),
            "top1": float(rows["baseline_top1"].mean()),
        },
        "candidate": {
            "logloss": float(rows["candidate_ll"].mean()),
            "brier": float(rows["candidate_brier"].mean()),
            "rps": float(rows["candidate_rps"].mean()),
            "top1": float(rows["candidate_top1"].mean()),
        },
    }
    pooled["delta_candidate_minus_baseline"] = {
        m: float(pooled["candidate"][m] - pooled["baseline"][m]) for m in pooled["baseline"]
    }
    boot = paired_bootstrap(rows["delta_ll"].to_numpy(float))
    fold_wins = int(sum(folds[s]["delta_candidate_minus_baseline"]["logloss"] < 0 for s in TEST_SEASONS))

    # League clusters are diagnostic only; they cannot override the preregistered proper-score gate.
    league = rows.groupby("league_key").agg(n=("delta_ll", "size"), delta_logloss=("delta_ll", "mean")).reset_index()
    league = league[league["n"] >= 10]
    league_diag = {
        str(r.league_key): {"n": int(r.n), "delta_logloss": float(r.delta_logloss)}
        for r in league.itertuples(index=False)
    }

    gate = {
        "pooled_logloss_improves": pooled["delta_candidate_minus_baseline"]["logloss"] < 0,
        "bootstrap_90pct_upper_below_zero": boot["ci90_high"] < 0,
        "pooled_brier_nonworse": pooled["delta_candidate_minus_baseline"]["brier"] <= 0,
        "pooled_rps_nonworse": pooled["delta_candidate_minus_baseline"]["rps"] <= 0,
        "fold_logloss_wins_at_least_4_of_5": fold_wins >= 4,
        "tail_residual_within_1e_8": max_tail_residual <= TAIL_RESIDUAL_LIMIT,
        "probability_sum_residual_within_1e_10": max_prob_residual <= PROB_RESIDUAL_LIMIT,
    }
    passed = all(gate.values())

    frozen_parameter = None
    if passed:
        dev = tail[tail["season_start"] <= FINAL_MAX_SEASON_START].copy()
        edev = dev["excess"].to_numpy(int)
        pi, rho, npos = fit_hurdle(edev)
        param = {
            "family": "two-part hurdle-geometric",
            "pi": pi,
            "rho": rho,
            "pmf": ["P(E=0)=1-pi", "P(E=e)=pi*(1-rho)*rho^(e-1), e>=1"],
            "development_tail_n": int(len(dev)),
            "development_positive_tail_n": int(npos),
            "development_horizon_max_season_start": FINAL_MAX_SEASON_START,
            "source_revision": c074e.SOURCE_REVISION,
        }
        param["parameter_sha256"] = canonical_sha(param)
        frozen_parameter = param
        (out / "confirmation_ready_parameter.json").write_text(
            json.dumps(param, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    summary = {
        "schema_version": SCHEMA,
        "status": "SCIENTIFIC_COMPONENT_PASS_DEVELOPMENT_ONLY" if passed else "FAIL_PARK",
        "scientific_effect_evaluated": True,
        "post_forward_hypothesis_development_only": True,
        "formal_weight": 0,
        "claim_boundary": "C075-E generated the structural hypothesis only; no C075-C/E target label is used here. Even PASS requires a completely fresh external confirmation before exact-tail closure.",
        "population": {
            "eligible_rows": int(len(elig)),
            "eligible_tail_rows_all_source": int(len(tail)),
            "pooled_oos_tail_rows": int(len(rows)),
            "coverage": coverage,
        },
        "baseline": {"family": "one-parameter geometric training-fold MLE"},
        "candidate": {
            "family": "two-part hurdle-geometric training-fold MLE",
            "features": None,
            "feature_search": False,
            "hyperparameter_search": False,
            "distribution_family_search": False,
        },
        "pooled": {
            **pooled,
            "bootstrap_logloss_delta": boot,
            "fold_logloss_wins": fold_wins,
            "max_unenumerated_tail_residual": max_tail_residual,
            "max_probability_sum_abs_residual": max_prob_residual,
        },
        "folds": folds,
        "league_cluster_diagnostic": league_diag,
        "gate": gate,
        "confirmation_ready_parameter": frozen_parameter,
        "boundaries": boundary,
        "stopping_rule": "do not repair or retune this family on the scored internal OOS labels after this result",
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
