#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import audit_c071_opportunity_source as audit
import evaluate_c071b_opportunity_pt_v2 as c071

SCHEMA_VERSION = "C075A_EXACT_TAIL_DEVELOPMENT_V1"
FIX_SHA = "7ba90661dbed29eb940daf5ea385c7d76d5751d16be86bd9063293a982abc7b7"
STAT_SHA = "2fb85b14b4428e1a36efe6d651de4ca8f7a6169ecfa3edb9cda49cb5e58d97e9"
DEV_CUTOFF = pd.Timestamp("2024-01-01T00:00:00Z")
CONFIRM_END = pd.Timestamp("2026-07-01T00:00:00Z")
EXPECTED_CONFIRM_IDENTITIES = 72180
TAIL_START = 7
C = 0.1
BOOT_REPS = 3000
BOOT_SEED = 75001
RESIDUAL_TOL = 1e-8
MIN_TEST_TAIL_PER_FOLD = 100
MIN_POOLED_TEST_TAIL = 600
MIN_TRAIN_TAIL_PER_FOLD = 500
MIN_LEAGUE_CLUSTER = 50
BASE = list(c071.BASE)
FOLDS = {
    "fold_2020": (pd.Timestamp("2020-01-01T00:00:00Z"), pd.Timestamp("2021-01-01T00:00:00Z")),
    "fold_2021": (pd.Timestamp("2021-01-01T00:00:00Z"), pd.Timestamp("2022-01-01T00:00:00Z")),
    "fold_2022": (pd.Timestamp("2022-01-01T00:00:00Z"), pd.Timestamp("2023-01-01T00:00:00Z")),
    "fold_2023": (pd.Timestamp("2023-01-01T00:00:00Z"), pd.Timestamp("2024-01-01T00:00:00Z")),
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def utc_ns(x):
    z = pd.to_datetime(x, utc=True, errors="coerce")
    if isinstance(z, pd.Series):
        return z.dt.as_unit("ns")
    if isinstance(z, pd.DatetimeIndex):
        return z.as_unit("ns")
    return z


def empty_opportunity_events() -> pd.DataFrame:
    return pd.DataFrame(columns=["team_id", "available_at", *c071.OPP_METRICS])


def day_safe_result_events(identity: pd.DataFrame, labels: pd.DataFrame):
    """Strict same-UTC-date predict-before-update result history."""
    x = identity.merge(
        labels[["id", "goals_home", "goals_away"]], on="id", how="inner", validate="one_to_one"
    ).dropna(subset=["goals_home", "goals_away"]).copy()
    x["goals_home"] = x.goals_home.astype(float)
    x["goals_away"] = x.goals_away.astype(float)
    next_day = x.date_utc.dt.floor("D") + pd.Timedelta(days=1)
    physical = x.date_utc + pd.Timedelta(minutes=105)
    x["available_at"] = pd.concat([next_day, physical], axis=1).max(axis=1)
    h = pd.DataFrame({
        "team_id": x.home_team_id.astype(int),
        "available_at": x.available_at,
        "goals_for": x.goals_home,
        "goals_against": x.goals_away,
    })
    a = pd.DataFrame({
        "team_id": x.away_team_id.astype(int),
        "available_at": x.available_at,
        "goals_for": x.goals_away,
        "goals_against": x.goals_home,
    })
    l = pd.DataFrame({
        "league_id": x.league_id.astype(int),
        "available_at": x.available_at,
        "total_goals": x.goals_home + x.goals_away,
    })
    return pd.concat([h, a], ignore_index=True), l, len(x)


def survival_model():
    return make_pipeline(
        SimpleImputer(strategy="median"),
        StandardScaler(),
        LogisticRegression(
            C=C,
            solver="lbfgs",
            max_iter=2000,
            class_weight=None,
            random_state=0,
        ),
    )


def expand_geometric_survival(train: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    blocks = []
    ys = []
    for _, row in train.iterrows():
        e = int(row["excess"])
        reps = e + 1
        block = pd.DataFrame([row[BASE].to_dict()] * reps)
        blocks.append(block)
        ys.extend([1] * e + [0])
    if not blocks:
        raise RuntimeError("empty tail training expansion")
    X = pd.concat(blocks, ignore_index=True)
    y = np.asarray(ys, dtype=int)
    if sorted(np.unique(y).tolist()) != [0, 1]:
        raise RuntimeError("tail survival training lacks both stop/continue classes")
    return X, y


def baseline_r(excess: np.ndarray) -> float:
    e = np.asarray(excess, dtype=float)
    if len(e) == 0:
        raise RuntimeError("empty baseline tail training")
    r = float(e.sum() / (len(e) + e.sum()))
    return min(1.0 - 1e-12, max(1e-12, r))


def adaptive_k(r: float, tol: float = RESIDUAL_TOL) -> int:
    r = float(min(1.0 - 1e-12, max(1e-12, r)))
    emax = max(0, int(math.ceil(math.log(tol) / math.log(r)) - 1))
    return TAIL_START + emax


def geometric_vector(r: float, kmax: int) -> tuple[np.ndarray, float]:
    r = float(min(1.0 - 1e-12, max(1e-12, r)))
    emax = int(kmax - TAIL_START)
    e = np.arange(emax + 1, dtype=float)
    exact = (1.0 - r) * np.power(r, e)
    residual = float(r ** (emax + 1))
    vec = np.concatenate([exact, np.asarray([residual])])
    if abs(float(vec.sum()) - 1.0) > 1e-12:
        raise RuntimeError("geometric vector probability conservation failure")
    return vec, residual


def score_rows(excess: np.ndarray, r_values: np.ndarray, common_k: int):
    excess = np.asarray(excess, dtype=int)
    r_values = np.asarray(r_values, dtype=float)
    n_classes = (common_k - TAIL_START + 1) + 1  # exact 7..K plus residual overflow
    ll = np.empty(len(excess), dtype=float)
    brier = np.empty(len(excess), dtype=float)
    rps = np.empty(len(excess), dtype=float)
    top1 = np.empty(len(excess), dtype=float)
    max_residual = 0.0
    max_sum_residual = 0.0
    for i, (e, r) in enumerate(zip(excess, r_values)):
        vec, residual = geometric_vector(float(r), common_k)
        max_residual = max(max_residual, residual)
        max_sum_residual = max(max_sum_residual, abs(float(vec.sum()) - 1.0))
        if len(vec) != n_classes:
            raise RuntimeError("score vector shape drift")
        truth = int(e) if int(e) <= common_k - TAIL_START else n_classes - 1
        one = np.zeros(n_classes, dtype=float)
        one[truth] = 1.0
        ptrue = max(1e-300, float(vec[truth]))
        ll[i] = -math.log(ptrue)
        brier[i] = float(np.sum((vec - one) ** 2))
        rps[i] = float(np.sum((np.cumsum(vec)[:-1] - np.cumsum(one)[:-1]) ** 2) / (n_classes - 1))
        top1[i] = float(int(np.argmax(vec)) == truth)
    return {
        "logloss": ll,
        "brier": brier,
        "rps": rps,
        "top1": top1,
        "max_unenumerated_residual": float(max_residual),
        "max_probability_sum_abs_residual": float(max_sum_residual),
    }


def binary_brier(excess: np.ndarray, r: np.ndarray, threshold_excess: int) -> float:
    y = (np.asarray(excess, int) >= int(threshold_excess)).astype(float)
    p = np.power(np.asarray(r, float), int(threshold_excess))
    return float(np.mean((p - y) ** 2))


def model_fingerprint(fitted) -> str:
    lr = fitted.named_steps["logisticregression"]
    imp = fitted.named_steps["simpleimputer"]
    scaler = fitted.named_steps["standardscaler"]
    payload = {
        "classes": [int(x) for x in lr.classes_],
        "coef": np.asarray(lr.coef_, float).round(14).tolist(),
        "intercept": np.asarray(lr.intercept_, float).round(14).tolist(),
        "imputer_statistics": np.asarray(imp.statistics_, float).round(14).tolist(),
        "scaler_mean": np.asarray(scaler.mean_, float).round(14).tolist(),
        "scaler_scale": np.asarray(scaler.scale_, float).round(14).tolist(),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def bootstrap(delta: np.ndarray) -> dict:
    d = np.asarray(delta, dtype=float)
    rng = np.random.default_rng(BOOT_SEED)
    sims = np.empty(BOOT_REPS, dtype=float)
    chunk = 50
    cursor = 0
    while cursor < BOOT_REPS:
        nrep = min(chunk, BOOT_REPS - cursor)
        idx = rng.integers(0, len(d), size=(nrep, len(d)))
        sims[cursor:cursor + nrep] = d[idx].mean(axis=1)
        cursor += nrep
    return {
        "n": int(len(d)),
        "reps": BOOT_REPS,
        "seed": BOOT_SEED,
        "mean_delta": float(d.mean()),
        "ci90_low": float(np.quantile(sims, 0.05)),
        "ci90_high": float(np.quantile(sims, 0.95)),
        "probability_delta_lt_zero": float(np.mean(sims < 0.0)),
    }


def mean_dict(frame: pd.DataFrame, prefix: str) -> dict:
    return {
        "logloss": float(frame[f"{prefix}_logloss"].mean()),
        "brier": float(frame[f"{prefix}_brier"].mean()),
        "rps": float(frame[f"{prefix}_rps"].mean()),
        "top1": float(frame[f"{prefix}_top1"].mean()),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", required=True)
    ap.add_argument("--stats", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    fp, sp, out_dir = Path(args.fixtures), Path(args.stats), Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if sha256(fp) != FIX_SHA or sha256(sp) != STAT_SHA:
        raise RuntimeError("pinned source SHA mismatch")

    c071.utc = utc_ns

    # Identity-only reconstruction first. No target goal/result columns are selected here.
    fixtures = pd.read_parquet(fp, columns=audit.FIXTURE_COLS)
    fixtures["date_utc"] = utc_ns(fixtures["date_utc"])
    fixtures = fixtures.dropna(subset=audit.FIXTURE_COLS).sort_values(["date_utc", "id"]).reset_index(drop=True)
    fixtures["id"] = fixtures["id"].astype("int64")
    stats = pd.read_parquet(sp, columns=audit.STAT_COLS)
    stats["known_at"] = utc_ns(stats["known_at"])
    stats = stats.dropna(subset=["fixture_id", "known_at"])
    eligible, _ = c071.eligible_identities(fixtures, stats)
    sealed = eligible[(eligible.date_utc >= DEV_CUTOFF) & (eligible.date_utc < CONFIRM_END)].copy()
    if len(sealed) != EXPECTED_CONFIRM_IDENTITIES:
        raise RuntimeError(f"sealed C071 identity drift {len(sealed)} != {EXPECTED_CONFIRM_IDENTITIES}")
    sealed_identity_payload = "\n".join(str(int(x)) for x in sealed.sort_values(["date_utc", "id"]).id.tolist()) + "\n"
    sealed_identity_sha = hashlib.sha256(sealed_identity_payload.encode()).hexdigest()

    # The helper applies a pyarrow predicate date_utc < 2024-01-01 at read time.
    labels = c071.read_dev_labels(fp)
    if len(labels) and not (labels.date_utc < DEV_CUTOFF).all():
        raise RuntimeError("development label horizon breach")
    dev_id = eligible[eligible.date_utc < DEV_CUTOFF].copy().sort_values(["date_utc", "id"]).reset_index(drop=True)
    dev = dev_id.merge(
        labels[["id", "goals_home", "goals_away"]], on="id", how="left", validate="one_to_one"
    ).dropna(subset=["goals_home", "goals_away"]).reset_index(drop=True)
    dev["goals_home"] = dev.goals_home.astype(int)
    dev["goals_away"] = dev.goals_away.astype(int)
    dev["total"] = dev.goals_home + dev.goals_away
    dev["excess"] = dev.total - TAIL_START

    # Score-history features only, with an even stricter same-UTC-date update policy.
    rteam, rleague, result_rows = day_safe_result_events(dev_id, labels)
    feat = c071.build_features(dev, rteam, rleague, empty_opportunity_events())
    feat["total"] = dev.total.to_numpy(int)
    feat["excess"] = dev.excess.to_numpy(int)
    tail = feat[feat.total >= TAIL_START].copy().sort_values(["date_utc", "id"]).reset_index(drop=True)

    coverage = {}
    pooled_test_tail = 0
    coverage_ok = True
    for name, (start, end) in FOLDS.items():
        ntrain = int((tail.date_utc < start).sum())
        ntest = int(((tail.date_utc >= start) & (tail.date_utc < end)).sum())
        coverage[name] = {"train_tail_rows": ntrain, "test_tail_rows": ntest}
        pooled_test_tail += ntest
        if ntrain < MIN_TRAIN_TAIL_PER_FOLD or ntest < MIN_TEST_TAIL_PER_FOLD:
            coverage_ok = False
    if pooled_test_tail < MIN_POOLED_TEST_TAIL:
        coverage_ok = False

    boundary = {
        "dev_goal_rows_read_max_date_exclusive": str(DEV_CUTOFF),
        "dev_goal_rows_read": int(len(labels)),
        "sealed_C071_identity_count": int(len(sealed)),
        "sealed_C071_identity_sha256": sealed_identity_sha,
        "sealed_C071_52180_goal_rows_returned": 0,
        "C070F_confirmation1597_opened": False,
        "A05_opened": False,
        "protected_opened": False,
        "C074G_2025_26_used_for_tail_confirmation": False,
        "unified_matrix_generated": False,
        "high_tail_D_given_T_tested": False,
        "formal_weight": 0,
    }

    if not coverage_ok:
        summary = {
            "schema_version": SCHEMA_VERSION,
            "status": "STOP_COVERAGE",
            "scientific_effect_evaluated": False,
            "identity": {
                "eligible_pre2024_completed": int(len(dev)),
                "tail_pre2024": int(len(tail)),
                "pooled_test_tail": int(pooled_test_tail),
                "coverage": coverage,
            },
            "boundaries": boundary,
        }
        (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    fold_reports = {}
    scored_rows = []
    fingerprints = {}
    for name, (start, end) in FOLDS.items():
        tr = tail[tail.date_utc < start].copy().reset_index(drop=True)
        te = tail[(tail.date_utc >= start) & (tail.date_utc < end)].copy().reset_index(drop=True)
        r0 = baseline_r(tr.excess.to_numpy(int))
        Xexp, yexp = expand_geometric_survival(tr)
        fitted = survival_model()
        fitted.fit(Xexp[BASE], yexp)
        fingerprints[name] = model_fingerprint(fitted)
        classes = [int(x) for x in fitted.named_steps["logisticregression"].classes_]
        one_index = classes.index(1)
        rc = fitted.predict_proba(te[BASE])[:, one_index]
        rc = np.clip(rc.astype(float), 1e-12, 1.0 - 1e-12)
        rb = np.full(len(te), r0, dtype=float)

        k_req_b = max(adaptive_k(float(r)) for r in rb)
        k_req_c = max(adaptive_k(float(r)) for r in rc)
        common_k = max(int(te.total.max()), int(k_req_b), int(k_req_c))
        sb = score_rows(te.excess.to_numpy(int), rb, common_k)
        sc = score_rows(te.excess.to_numpy(int), rc, common_k)

        row = te[["id", "date_utc", "league_id", "total", "excess"]].copy().reset_index(drop=True)
        for metric in ("logloss", "brier", "rps", "top1"):
            row[f"baseline_{metric}"] = sb[metric]
            row[f"candidate_{metric}"] = sc[metric]
            row[f"delta_{metric}"] = sc[metric] - sb[metric]
        row["baseline_r"] = rb
        row["candidate_r"] = rc
        row["fold"] = name
        scored_rows.append(row)

        base_summary = mean_dict(row, "baseline")
        cand_summary = mean_dict(row, "candidate")
        fold_reports[name] = {
            "train_tail_rows": int(len(tr)),
            "test_tail_rows": int(len(te)),
            "train_survival_rows": int(len(yexp)),
            "test_start": str(start),
            "test_end_exclusive": str(end),
            "baseline_r": float(r0),
            "candidate_r_mean": float(np.mean(rc)),
            "observed_mean_excess": float(te.excess.mean()),
            "baseline_mean_excess": float(r0 / (1.0 - r0)),
            "candidate_mean_excess": float(np.mean(rc / (1.0 - rc))),
            "common_enumeration_K": int(common_k),
            "baseline": base_summary,
            "candidate": cand_summary,
            "delta_candidate_minus_baseline": {m: float(cand_summary[m] - base_summary[m]) for m in base_summary},
            "baseline_binary_brier": {
                "T_ge_8": binary_brier(te.excess.to_numpy(int), rb, 1),
                "T_ge_9": binary_brier(te.excess.to_numpy(int), rb, 2),
                "T_ge_10": binary_brier(te.excess.to_numpy(int), rb, 3),
            },
            "candidate_binary_brier": {
                "T_ge_8": binary_brier(te.excess.to_numpy(int), rc, 1),
                "T_ge_9": binary_brier(te.excess.to_numpy(int), rc, 2),
                "T_ge_10": binary_brier(te.excess.to_numpy(int), rc, 3),
            },
            "baseline_max_unenumerated_residual": float(sb["max_unenumerated_residual"]),
            "candidate_max_unenumerated_residual": float(sc["max_unenumerated_residual"]),
            "baseline_probability_sum_abs_residual": float(sb["max_probability_sum_abs_residual"]),
            "candidate_probability_sum_abs_residual": float(sc["max_probability_sum_abs_residual"]),
            "model_fingerprint": fingerprints[name],
        }

    scored = pd.concat(scored_rows, ignore_index=True).sort_values(["date_utc", "id"]).reset_index(drop=True)
    scored.to_csv(out_dir / "row_metrics.csv", index=False)
    base_pooled = mean_dict(scored, "baseline")
    cand_pooled = mean_dict(scored, "candidate")
    pooled_delta = {m: float(cand_pooled[m] - base_pooled[m]) for m in base_pooled}
    boot = bootstrap(scored.delta_logloss.to_numpy(float))

    league_reports = {}
    league_wins = 0
    eligible_leagues = 0
    for league_id, group in scored.groupby("league_id"):
        if len(group) < MIN_LEAGUE_CLUSTER:
            continue
        eligible_leagues += 1
        delta_ll = float(group.delta_logloss.mean())
        if delta_ll < 0.0:
            league_wins += 1
        league_reports[str(int(league_id))] = {"n": int(len(group)), "delta_logloss": delta_ll}
    league_win_fraction = float(league_wins / eligible_leagues) if eligible_leagues else 0.0

    fold_wins = int(sum(1 for r in fold_reports.values() if r["delta_candidate_minus_baseline"]["logloss"] < 0.0))
    max_residual = max(
        max(r["baseline_max_unenumerated_residual"], r["candidate_max_unenumerated_residual"])
        for r in fold_reports.values()
    )
    max_prob_residual = max(
        max(r["baseline_probability_sum_abs_residual"], r["candidate_probability_sum_abs_residual"])
        for r in fold_reports.values()
    )

    gates = {
        "pooled_logloss_improves": bool(pooled_delta["logloss"] < 0.0),
        "bootstrap_90pct_upper_below_zero": bool(boot["ci90_high"] < 0.0),
        "pooled_brier_nonworse": bool(pooled_delta["brier"] <= 0.0),
        "pooled_rps_nonworse": bool(pooled_delta["rps"] <= 0.0),
        "fold_logloss_wins_at_least_3_of_4": bool(fold_wins >= 3),
        "league_cluster_win_fraction_at_least_half": bool(eligible_leagues > 0 and league_win_fraction >= 0.5),
        "tail_residual_within_1e_8": bool(max_residual <= RESIDUAL_TOL),
        "probability_sum_residual_within_1e_8": bool(max_prob_residual <= RESIDUAL_TOL),
    }
    passed = all(gates.values())
    status = "SCIENTIFIC_COMPONENT_PASS_DEVELOPMENT_ONLY" if passed else "FAIL_PARK"

    summary = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "scientific_effect_evaluated": True,
        "formal_weight": 0,
        "claim_boundary": "development-only; not independent confirmation; unified score matrix remains closed; T>=7 D|T remains missing",
        "identity": {
            "eligible_pre2024_completed": int(len(dev)),
            "tail_pre2024": int(len(tail)),
            "pooled_test_tail": int(len(scored)),
            "coverage": coverage,
            "result_history_rows": int(result_rows),
        },
        "candidate": {
            "family": "covariate geometric survival regression",
            "C": C,
            "features": BASE,
            "feature_search": False,
            "hyperparameter_search": False,
            "distribution_family_search": False,
            "same_day_predict_before_update": True,
            "fold_fingerprints": fingerprints,
        },
        "pooled": {
            "baseline": base_pooled,
            "candidate": cand_pooled,
            "delta_candidate_minus_baseline": pooled_delta,
            "bootstrap_logloss_delta": boot,
            "fold_logloss_wins": fold_wins,
            "league_cluster_eligible": eligible_leagues,
            "league_cluster_wins": league_wins,
            "league_cluster_win_fraction": league_win_fraction,
            "max_unenumerated_tail_residual": float(max_residual),
            "max_probability_sum_abs_residual": float(max_prob_residual),
        },
        "folds": fold_reports,
        "league_clusters": league_reports,
        "gate": gates,
        "boundaries": boundary,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
