#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import c074e_scorehistory_movement_directt as c074e

TEST_SEASONS = ["2019-2020", "2020-2021", "2021-2022", "2022-2023", "2023-2024"]
MIN_TRAIN_TAIL = 100
MIN_TEST_TAIL = 20
MIN_POOLED_TEST = 150
C_FIXED = 0.1
BOOT_REPS = 3000
BOOT_SEED = 75002
ENUM_RESIDUAL_TARGET = 1e-10


def pipeline():
    return make_pipeline(
        StandardScaler(),
        LogisticRegression(C=C_FIXED, solver="lbfgs", max_iter=2000, class_weight=None, random_state=0),
    )


def expand_survival(train_tail: pd.DataFrame):
    xs, ys = [], []
    for r in train_tail.itertuples(index=False):
        e = int(r.exact_total) - 7
        x = float(r.movement_logit)
        for j in range(e + 1):
            xs.append([x])
            ys.append(1 if j < e else 0)
    return np.asarray(xs, float), np.asarray(ys, int)


def baseline_r(train_tail: pd.DataFrame) -> float:
    e = train_tail["exact_total"].to_numpy(int) - 7
    continuations = float(e.sum())
    exposures = float((e + 1).sum())
    r = continuations / exposures
    return float(np.clip(r, 1e-8, 1 - 1e-8))


def fingerprint(model) -> str:
    lr = model.named_steps["logisticregression"]
    sc = model.named_steps["standardscaler"]
    payload = {
        "coef": np.asarray(lr.coef_, float).round(14).tolist(),
        "intercept": np.asarray(lr.intercept_, float).round(14).tolist(),
        "classes": np.asarray(lr.classes_, int).tolist(),
        "scale_mean": np.asarray(sc.mean_, float).round(14).tolist(),
        "scale_scale": np.asarray(sc.scale_, float).round(14).tolist(),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def exact_ll(e: np.ndarray, r: np.ndarray) -> np.ndarray:
    r = np.clip(np.asarray(r, float), 1e-12, 1 - 1e-12)
    return -(np.log1p(-r) + np.asarray(e, float) * np.log(r))


def choose_k(r0: np.ndarray, r1: np.ndarray, observed_max_e: int) -> int:
    max_r = float(max(np.max(r0), np.max(r1)))
    if max_r <= 0:
        return observed_max_e
    k = max(observed_max_e, 3)
    while max_r ** (k + 1) > ENUM_RESIDUAL_TARGET and k < 400:
        k += 1
    if max_r ** (k + 1) > 1e-8:
        raise RuntimeError("unable to enumerate geometric tail to residual <=1e-8")
    return k


def dist_matrix(r: np.ndarray, k: int) -> np.ndarray:
    r = np.clip(np.asarray(r, float), 1e-12, 1 - 1e-12)
    e = np.arange(k + 1, dtype=float)
    p = (1.0 - r[:, None]) * np.power(r[:, None], e[None, :])
    residual = np.power(r, k + 1)[:, None]
    out = np.concatenate([p, residual], axis=1)
    out /= out.sum(axis=1, keepdims=True)
    return out


def proper_rows(e: np.ndarray, r: np.ndarray, k: int):
    p = dist_matrix(r, k)
    y = np.minimum(np.asarray(e, int), k + 1)
    one = np.zeros_like(p)
    one[np.arange(len(y)), y] = 1.0
    ll = exact_ll(e, r)
    brier = np.square(p - one).sum(axis=1)
    cp = np.cumsum(p, axis=1)[:, :-1]
    cy = np.cumsum(one, axis=1)[:, :-1]
    rps = np.square(cp - cy).sum(axis=1) / max(1, p.shape[1] - 1)
    top1 = (np.argmax(p, axis=1) == y).astype(float)
    return {"logloss": ll, "brier": brier, "rps": rps, "top1": top1}, p


def summarize(rows):
    return {k: float(np.mean(v)) for k, v in rows.items()}


def threshold_brier(e: np.ndarray, r: np.ndarray):
    e = np.asarray(e, int)
    out = {}
    for n in (1, 2, 3):
        y = (e >= n).astype(float)
        p = np.power(r, n)
        out[f"T_ge_{7+n}"] = float(np.mean(np.square(p - y)))
    return out


def bootstrap(d: np.ndarray):
    d = np.asarray(d, float)
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--external-root", required=True)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    out = Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)

    raw = c074e.load_source(Path(a.external_root))
    raw["exact_total"] = (raw["FTHG"].astype(int) + raw["FTAG"].astype(int)).astype(int)
    feat = c074e.build_history_features(raw)
    exact_by_row = raw["exact_total"].to_dict()
    feat["exact_total"] = feat["row_id"].map(exact_by_row).astype(int)
    elig = feat.loc[feat["eligible"]].copy().reset_index(drop=True)

    coverage = {}
    coverage_ok = True
    for season in TEST_SEASONS:
        start = c074e.season_start(season)
        tr = elig[(elig["season_start"] < start) & (elig["exact_total"] >= 7)]
        te = elig[(elig["Season"] == season) & (elig["exact_total"] >= 7)]
        coverage[season] = {"train_tail_rows": int(len(tr)), "test_tail_rows": int(len(te))}
        coverage_ok &= len(tr) >= MIN_TRAIN_TAIL and len(te) >= MIN_TEST_TAIL
    pooled_n = int(sum(v["test_tail_rows"] for v in coverage.values()))
    coverage_ok &= pooled_n >= MIN_POOLED_TEST

    if not coverage_ok:
        summary = {
            "schema_version": "C075B_MOVEMENT_EXACT_TAIL_DEVELOPMENT_V1",
            "status": "STOP_COVERAGE",
            "scientific_effect_evaluated": False,
            "formal_weight": 0,
            "coverage": coverage,
            "pooled_test_tail": pooled_n,
            "boundaries": {
                "C071_reserve_52180_opened": False,
                "C070F_confirmation1597_opened": False,
                "A05_opened": False,
                "protected_opened": False,
                "C074G_2025_26_used_as_tail_confirmation": False,
                "high_tail_D_given_T_tested": False,
                "unified_matrix_generated": False
            }
        }
        (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    folds = {}
    pooled_parts = []
    max_tail_residual = 0.0
    max_prob_residual = 0.0
    fingerprints = {}

    for season in TEST_SEASONS:
        start = c074e.season_start(season)
        train = elig[(elig["season_start"] < start) & (elig["exact_total"] >= 7)].copy()
        test = elig[(elig["Season"] == season) & (elig["exact_total"] >= 7)].copy()
        e_train = train["exact_total"].to_numpy(int) - 7
        e_test = test["exact_total"].to_numpy(int) - 7

        rb = baseline_r(train)
        Xs, ys = expand_survival(train)
        if len(np.unique(ys)) != 2:
            raise RuntimeError(f"survival support incomplete in {season}")
        cand = pipeline(); cand.fit(Xs, ys)
        rc = cand.predict_proba(test[["movement_logit"]].to_numpy(float))[:, list(cand.named_steps["logisticregression"].classes_).index(1)]
        rc = np.clip(rc, 1e-8, 1 - 1e-8)
        r0 = np.full(len(test), rb, dtype=float)
        k = choose_k(r0, rc, int(e_test.max()))
        mb, pb = proper_rows(e_test, r0, k)
        mc, pc = proper_rows(e_test, rc, k)
        sb, sc = summarize(mb), summarize(mc)
        dll = mc["logloss"] - mb["logloss"]
        tail_res = float(max(np.max(np.power(r0, k + 1)), np.max(np.power(rc, k + 1))))
        prob_res = float(max(np.max(np.abs(pb.sum(axis=1)-1.0)), np.max(np.abs(pc.sum(axis=1)-1.0))))
        max_tail_residual = max(max_tail_residual, tail_res)
        max_prob_residual = max(max_prob_residual, prob_res)
        fingerprints[season] = fingerprint(cand)
        folds[season] = {
            "train_tail_rows": int(len(train)), "test_tail_rows": int(len(test)),
            "train_survival_rows": int(len(ys)), "baseline_r": rb,
            "candidate_r_mean": float(np.mean(rc)), "observed_mean_excess": float(np.mean(e_test)),
            "baseline_mean_excess": float(rb/(1-rb)),
            "candidate_mean_excess": float(np.mean(rc/(1-rc))),
            "enumeration_K": int(k), "baseline": sb, "candidate": sc,
            "delta_candidate_minus_baseline": {m: float(sc[m]-sb[m]) for m in sb},
            "baseline_threshold_brier": threshold_brier(e_test, r0),
            "candidate_threshold_brier": threshold_brier(e_test, rc),
            "max_unenumerated_tail_residual": tail_res,
            "max_probability_sum_abs_residual": prob_res,
            "model_fingerprint": fingerprints[season]
        }
        tmp = pd.DataFrame({
            "season": season, "league_key": test["league_key"].to_numpy(),
            "exact_total": test["exact_total"].to_numpy(int), "excess": e_test,
            "movement_logit": test["movement_logit"].to_numpy(float),
            "baseline_ll": mb["logloss"], "candidate_ll": mc["logloss"], "delta_ll": dll,
            "baseline_brier": mb["brier"], "candidate_brier": mc["brier"],
            "baseline_rps": mb["rps"], "candidate_rps": mc["rps"],
            "baseline_r": r0, "candidate_r": rc
        })
        pooled_parts.append(tmp)

    rows = pd.concat(pooled_parts, ignore_index=True)
    rows.to_csv(out / "row_metrics.csv", index=False)
    pooled = {
        "baseline": {
            "logloss": float(rows.baseline_ll.mean()),
            "brier": float(rows.baseline_brier.mean()),
            "rps": float(rows.baseline_rps.mean())
        },
        "candidate": {
            "logloss": float(rows.candidate_ll.mean()),
            "brier": float(rows.candidate_brier.mean()),
            "rps": float(rows.candidate_rps.mean())
        }
    }
    pooled["delta_candidate_minus_baseline"] = {m: pooled["candidate"][m]-pooled["baseline"][m] for m in ("logloss","brier","rps")}
    boot = bootstrap(rows.delta_ll.to_numpy(float))
    fold_wins = int(sum(folds[s]["delta_candidate_minus_baseline"]["logloss"] < 0 for s in TEST_SEASONS))
    cluster = rows.groupby("league_key").agg(n=("delta_ll","size"), delta_logloss=("delta_ll","mean")).reset_index()
    eligible_cluster = cluster[cluster.n >= 15].copy()
    cluster_wins = int((eligible_cluster.delta_logloss < 0).sum())
    cluster_fraction = float(cluster_wins / len(eligible_cluster)) if len(eligible_cluster) else 0.0
    league_clusters = {str(r.league_key): {"n": int(r.n), "delta_logloss": float(r.delta_logloss)} for r in eligible_cluster.itertuples(index=False)}

    gate = {
        "pooled_logloss_improves": pooled["delta_candidate_minus_baseline"]["logloss"] < 0,
        "bootstrap_90pct_upper_below_zero": boot["ci90_high"] < 0,
        "pooled_brier_nonworse": pooled["delta_candidate_minus_baseline"]["brier"] <= 0,
        "pooled_rps_nonworse": pooled["delta_candidate_minus_baseline"]["rps"] <= 0,
        "fold_logloss_wins_at_least_4_of_5": fold_wins >= 4,
        "league_cluster_win_fraction_at_least_half": len(eligible_cluster) > 0 and cluster_fraction >= 0.5,
        "tail_residual_within_1e_8": max_tail_residual <= 1e-8,
        "probability_sum_residual_within_1e_10": max_prob_residual <= 1e-10
    }
    passed = all(gate.values())
    status = "SCIENTIFIC_COMPONENT_PASS_DEVELOPMENT_ONLY" if passed else "FAIL_PARK"
    summary = {
        "schema_version": "C075B_MOVEMENT_EXACT_TAIL_DEVELOPMENT_V1",
        "status": status,
        "scientific_effect_evaluated": True,
        "formal_weight": 0,
        "claim_boundary": "development-only on previously opened C074-E market domain; not independent confirmation; unified score matrix remains closed; T>=7 D|T remains missing",
        "source_revision": c074e.SOURCE_REVISION,
        "population": {"eligible_rows": int(len(elig)), "pooled_test_tail": int(len(rows)), "coverage": coverage},
        "candidate": {"feature": "movement_logit", "C": C_FIXED, "feature_search": False, "hyperparameter_search": False, "distribution_family_search": False, "fold_fingerprints": fingerprints},
        "pooled": {**pooled, "bootstrap_logloss_delta": boot, "fold_logloss_wins": fold_wins, "league_cluster_eligible": int(len(eligible_cluster)), "league_cluster_wins": cluster_wins, "league_cluster_win_fraction": cluster_fraction, "max_unenumerated_tail_residual": max_tail_residual, "max_probability_sum_abs_residual": max_prob_residual},
        "folds": folds,
        "league_clusters": league_clusters,
        "gate": gate,
        "boundaries": {
            "C071_reserve_52180_opened": False,
            "C070F_confirmation1597_opened": False,
            "A05_opened": False,
            "protected_opened": False,
            "C074G_2025_26_used_as_tail_confirmation": False,
            "high_tail_D_given_T_tested": False,
            "unified_matrix_generated": False,
            "formal_weight": 0
        }
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
