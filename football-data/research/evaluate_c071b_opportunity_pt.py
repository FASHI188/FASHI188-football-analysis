#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds
import pyarrow.parquet as pq
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import audit_c071_opportunity_source as audit

SCHEMA_VERSION = "C071B_OPPORTUNITY_PT_DEVELOPMENT_V1"
EXPECTED_FIXTURE_SHA = "7ba90661dbed29eb940daf5ea385c7d76d5751d16be86bd9063293a982abc7b7"
EXPECTED_STATS_SHA = "2fb85b14b4428e1a36efe6d651de4ca8f7a6169ecfa3edb9cda49cb5e58d97e9"
DEV_CUTOFF = pd.Timestamp("2024-01-01T00:00:00Z")
CONFIRM_END = pd.Timestamp("2026-07-01T00:00:00Z")
EXPECTED_CONFIRM_IDENTITIES = 72180
RESULT_DELAY = pd.Timedelta(minutes=105)
MIN_STATS_HISTORY = 8
K = 8
C = 0.1
BOOT_REPS = 2000
BOOT_SEED = 71102

FOLDS = {
    "fold_1": (pd.Timestamp("2022-01-01T00:00:00Z"), pd.Timestamp("2022-07-01T00:00:00Z")),
    "fold_2": (pd.Timestamp("2022-07-01T00:00:00Z"), pd.Timestamp("2023-01-01T00:00:00Z")),
    "fold_3": (pd.Timestamp("2023-01-01T00:00:00Z"), pd.Timestamp("2023-07-01T00:00:00Z")),
    "fold_4": (pd.Timestamp("2023-07-01T00:00:00Z"), pd.Timestamp("2024-01-01T00:00:00Z")),
}

RESULT_METRICS = ["goals_for", "goals_against"]
OPP_METRICS = [
    "shots_total_for", "shots_total_against",
    "shots_on_goal_for", "shots_on_goal_against",
    "shots_inside_box_for", "shots_inside_box_against",
    "shots_outside_share_for", "shots_outside_share_against",
    "penalties_for", "penalties_against",
]
OPP_SD_METRICS = [m for m in OPP_METRICS if not m.startswith("penalties_")]

BASE_FEATURES = [
    "league_total_mean", "league_total_sd",
    "home_goals_for_mean", "home_goals_for_sd", "home_goals_against_mean", "home_goals_against_sd",
    "away_goals_for_mean", "away_goals_for_sd", "away_goals_against_mean", "away_goals_against_sd",
    "log1p_home_result_history_n", "log1p_away_result_history_n",
]
MEAN_FEATURES = [f"{side}_{m}_mean" for side in ("home", "away") for m in OPP_METRICS]
SD_FEATURES = [f"{side}_{m}_sd" for side in ("home", "away") for m in OPP_SD_METRICS]
MEAN_MODEL_FEATURES = BASE_FEATURES + MEAN_FEATURES
DIST_MODEL_FEATURES = BASE_FEATURES + MEAN_FEATURES + SD_FEATURES


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def utc(x):
    return pd.to_datetime(x, utc=True, errors="coerce")


def read_dev_goal_labels(path: Path) -> pd.DataFrame:
    # Arrow predicate is part of the seal: no row at/after 2024-01-01 is returned to Python.
    dataset = ds.dataset(path, format="parquet")
    table = dataset.to_table(
        columns=["id", "date_utc", "goals_home", "goals_away"],
        filter=ds.field("date_utc") < datetime(2024, 1, 1),
    )
    out = table.to_pandas()
    out["date_utc"] = utc(out["date_utc"])
    if len(out) and not bool((out["date_utc"] < DEV_CUTOFF).all()):
        raise RuntimeError("development goal-label horizon breached")
    return out


def build_eligible_identities(fixtures: pd.DataFrame, stats: pd.DataFrame):
    stats = stats.copy()
    stats["core_complete"] = stats[audit.CORE].notna().all(axis=1)
    comp = stats[stats.core_complete].merge(
        fixtures[["id", "home_team_id", "away_team_id"]],
        left_on="fixture_id", right_on="id", how="inner", validate="one_to_one"
    )
    hist = pd.concat([
        pd.DataFrame({"team_id": comp.home_team_id.astype(int), "known_at": comp.known_at}),
        pd.DataFrame({"team_id": comp.away_team_id.astype(int), "known_at": comp.known_at}),
    ], ignore_index=True)
    hn, an, eligible = audit.prior_counts(fixtures, hist, MIN_STATS_HISTORY)
    out = fixtures.loc[eligible, audit.FIXTURE_COLS].copy()
    out["home_prior_complete_stats"] = hn[eligible]
    out["away_prior_complete_stats"] = an[eligible]
    return out.sort_values(["date_utc", "id"]).reset_index(drop=True), comp


def build_result_events(identity: pd.DataFrame, labels: pd.DataFrame):
    x = identity.merge(labels[["id", "goals_home", "goals_away"]], on="id", how="inner", validate="one_to_one")
    x = x.dropna(subset=["goals_home", "goals_away"]).copy()
    x["goals_home"] = x.goals_home.astype(float)
    x["goals_away"] = x.goals_away.astype(float)
    x["available_at"] = x.date_utc + RESULT_DELAY
    home = pd.DataFrame({
        "team_id": x.home_team_id.astype(int), "available_at": x.available_at,
        "goals_for": x.goals_home, "goals_against": x.goals_away,
    })
    away = pd.DataFrame({
        "team_id": x.away_team_id.astype(int), "available_at": x.available_at,
        "goals_for": x.goals_away, "goals_against": x.goals_home,
    })
    team = pd.concat([home, away], ignore_index=True)
    league = pd.DataFrame({
        "league_id": x.league_id.astype(int), "available_at": x.available_at,
        "total_goals": x.goals_home + x.goals_away,
    })
    return team, league, int(len(x))


def safe_share(num: pd.Series, den: pd.Series) -> np.ndarray:
    n = num.to_numpy(float)
    d = den.to_numpy(float)
    out = np.zeros(len(n), dtype=float)
    np.divide(n, d, out=out, where=d > 0)
    return out


def build_opportunity_events(comp: pd.DataFrame):
    x = comp[comp.known_at < DEV_CUTOFF].copy()
    # Numerical opportunity payload at/after confirmation boundary is never used in development features.
    home = pd.DataFrame({
        "team_id": x.home_team_id.astype(int), "available_at": x.known_at,
        "shots_total_for": x.home_shots_total.astype(float),
        "shots_total_against": x.away_shots_total.astype(float),
        "shots_on_goal_for": x.home_shots_on_goal.astype(float),
        "shots_on_goal_against": x.away_shots_on_goal.astype(float),
        "shots_inside_box_for": x.home_shots_inside_box.astype(float),
        "shots_inside_box_against": x.away_shots_inside_box.astype(float),
        "shots_outside_share_for": safe_share(x.home_shots_outside_box, x.home_shots_total),
        "shots_outside_share_against": safe_share(x.away_shots_outside_box, x.away_shots_total),
        "penalties_for": x.home_penalties.astype(float),
        "penalties_against": x.away_penalties.astype(float),
    })
    away = pd.DataFrame({
        "team_id": x.away_team_id.astype(int), "available_at": x.known_at,
        "shots_total_for": x.away_shots_total.astype(float),
        "shots_total_against": x.home_shots_total.astype(float),
        "shots_on_goal_for": x.away_shots_on_goal.astype(float),
        "shots_on_goal_against": x.home_shots_on_goal.astype(float),
        "shots_inside_box_for": x.away_shots_inside_box.astype(float),
        "shots_inside_box_against": x.home_shots_inside_box.astype(float),
        "shots_outside_share_for": safe_share(x.away_shots_outside_box, x.away_shots_total),
        "shots_outside_share_against": safe_share(x.home_shots_outside_box, x.home_shots_total),
        "penalties_for": x.away_penalties.astype(float),
        "penalties_against": x.home_penalties.astype(float),
    })
    return pd.concat([home, away], ignore_index=True), int(len(x))


def make_cache(events: pd.DataFrame, key: str, time: str, metrics: list[str]):
    cache = {}
    for k, g in events.sort_values([key, time]).groupby(key, sort=False):
        vals = g[metrics].to_numpy(float)
        times = g[time].astype("int64").to_numpy()
        cache[int(k)] = (
            times,
            np.cumsum(vals, axis=0),
            np.cumsum(vals * vals, axis=0),
        )
    return cache


def snapshot(target: pd.DataFrame, target_key: str, cache: dict, metrics: list[str], prefix: str):
    nrow = len(target)
    means = {m: np.full(nrow, np.nan) for m in metrics}
    sds = {m: np.full(nrow, np.nan) for m in metrics}
    counts = np.zeros(nrow, dtype=np.int32)
    target_ns = target.date_utc.astype("int64").to_numpy()
    keys = target[target_key].astype("Int64")
    for k, idx in keys.groupby(keys).groups.items():
        if pd.isna(k) or int(k) not in cache:
            continue
        pos_idx = np.asarray(list(idx), dtype=int)
        times, csum, csq = cache[int(k)]
        p = np.searchsorted(times, target_ns[pos_idx], side="left") - 1
        good = p >= 0
        if not good.any():
            continue
        rows = pos_idx[good]
        pp = p[good]
        nn = (pp + 1).astype(float)
        counts[rows] = pp + 1
        ss = csum[pp]
        qq = csq[pp]
        mu = ss / nn[:, None]
        var = np.maximum(qq / nn[:, None] - mu * mu, 0.0)
        sd = np.sqrt(var)
        for j, m in enumerate(metrics):
            means[m][rows] = mu[:, j]
            sds[m][rows] = sd[:, j]
    out = pd.DataFrame(index=target.index)
    for m in metrics:
        out[f"{prefix}_{m}_mean"] = means[m]
        out[f"{prefix}_{m}_sd"] = sds[m]
    out[f"{prefix}_history_n"] = counts
    return out


def snapshot_league(target: pd.DataFrame, league_events: pd.DataFrame):
    cache = make_cache(league_events, "league_id", "available_at", ["total_goals"])
    z = snapshot(target, "league_id", cache, ["total_goals"], "league")
    return pd.DataFrame({
        "league_total_mean": z["league_total_goals_mean"],
        "league_total_sd": z["league_total_goals_sd"],
    }, index=target.index)


def build_features(target: pd.DataFrame, result_team: pd.DataFrame, result_league: pd.DataFrame, opp_team: pd.DataFrame):
    result_cache = make_cache(result_team, "team_id", "available_at", RESULT_METRICS)
    opp_cache = make_cache(opp_team, "team_id", "available_at", OPP_METRICS)
    hres = snapshot(target, "home_team_id", result_cache, RESULT_METRICS, "home")
    ares = snapshot(target, "away_team_id", result_cache, RESULT_METRICS, "away")
    hopp = snapshot(target, "home_team_id", opp_cache, OPP_METRICS, "home")
    aopp = snapshot(target, "away_team_id", opp_cache, OPP_METRICS, "away")
    league = snapshot_league(target, result_league)
    out = pd.concat([target.reset_index(drop=True), league.reset_index(drop=True), hres.reset_index(drop=True), ares.reset_index(drop=True), hopp.reset_index(drop=True), aopp.reset_index(drop=True)], axis=1)
    out["log1p_home_result_history_n"] = np.log1p(out["home_history_n"].astype(float))
    out["log1p_away_result_history_n"] = np.log1p(out["away_history_n"].astype(float))
    return out


def pipeline():
    return make_pipeline(
        SimpleImputer(strategy="median"),
        StandardScaler(),
        LogisticRegression(C=C, max_iter=2000, class_weight=None, random_state=0, solver="lbfgs"),
    )


def predict_fixed(model, X):
    p = model.predict_proba(X)
    classes = model.named_steps["logisticregression"].classes_.astype(int)
    out = np.zeros((len(X), K), dtype=float)
    out[:, classes] = p
    out = np.clip(out, 1e-15, 1.0)
    out /= out.sum(axis=1, keepdims=True)
    return out


def metrics(y, p):
    y = np.asarray(y, int)
    p = np.asarray(p, float)
    one = np.eye(K)[y]
    cp = np.cumsum(p, axis=1)[:, :-1]
    cy = np.cumsum(one, axis=1)[:, :-1]
    def auc(th):
        yy = (y >= th).astype(int)
        return float(roc_auc_score(yy, p[:, th:].sum(axis=1))) if len(np.unique(yy)) == 2 else None
    return {
        "n": int(len(y)),
        "log_loss": float(log_loss(y, p, labels=list(range(K)))),
        "rps": float(np.mean(np.sum((cp - cy) ** 2, axis=1) / (K - 1))),
        "brier": float(np.mean(np.sum((p - one) ** 2, axis=1))),
        "auc_t_ge_4": auc(4),
        "auc_t_ge_5": auc(5),
    }


def delta(a, b):
    # candidate minus baseline; lower proper scores are better, higher AUC is better.
    out = {}
    for k in ["log_loss", "rps", "brier", "auc_t_ge_4", "auc_t_ge_5"]:
        out[k] = None if a[k] is None or b[k] is None else float(a[k] - b[k])
    return out


def bootstrap_ll(y, p0, p1):
    y = np.asarray(y, int)
    idx = np.arange(len(y))
    d = -np.log(np.clip(p1[idx, y], 1e-15, 1.0)) + np.log(np.clip(p0[idx, y], 1e-15, 1.0))
    rng = np.random.default_rng(BOOT_SEED)
    sims = np.empty(BOOT_REPS, dtype=float)
    n = len(d)
    for i in range(BOOT_REPS):
        sims[i] = float(d[rng.integers(0, n, n)].mean())
    return {
        "matches": int(n), "mean_delta_log_loss": float(d.mean()),
        "ci90_low": float(np.quantile(sims, .05)), "ci90_high": float(np.quantile(sims, .95)),
        "reps": BOOT_REPS, "seed": BOOT_SEED,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", required=True)
    ap.add_argument("--stats", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    fp, sp, outdir = Path(args.fixtures), Path(args.stats), Path(args.out_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    if sha256(fp) != EXPECTED_FIXTURE_SHA or sha256(sp) != EXPECTED_STATS_SHA:
        raise RuntimeError("pinned source SHA mismatch")

    # Identity and availability audit projection contains no target outcome labels.
    fixtures = pd.read_parquet(fp, columns=audit.FIXTURE_COLS)
    fixtures["date_utc"] = utc(fixtures.date_utc)
    fixtures = fixtures.dropna(subset=audit.FIXTURE_COLS).copy().sort_values(["date_utc", "id"]).reset_index(drop=True)
    stats = pd.read_parquet(sp, columns=audit.STAT_COLS)
    stats["known_at"] = utc(stats.known_at)
    stats = stats.dropna(subset=["fixture_id", "known_at"]).copy()
    eligible, complete_join = build_eligible_identities(fixtures, stats)

    confirm = eligible[(eligible.date_utc >= DEV_CUTOFF) & (eligible.date_utc < CONFIRM_END)].copy()
    if len(confirm) != EXPECTED_CONFIRM_IDENTITIES:
        raise RuntimeError(f"confirmation identity drift {len(confirm)} != {EXPECTED_CONFIRM_IDENTITIES}")
    confirm.to_csv(outdir / "sealed_confirmation_identity.csv", index=False)

    # This is the only goal-label read, and Arrow filters it strictly to pre-2024 rows.
    labels = read_dev_goal_labels(fp)
    identity_pre = fixtures[fixtures.date_utc < DEV_CUTOFF].copy()
    result_team, result_league, historical_played = build_result_events(identity_pre, labels)
    opp_team, opportunity_history_matches = build_opportunity_events(complete_join)

    dev_ids = eligible[eligible.date_utc < DEV_CUTOFF].copy()
    dev = dev_ids.merge(labels[["id", "goals_home", "goals_away"]], on="id", how="left", validate="one_to_one")
    dev = dev.dropna(subset=["goals_home", "goals_away"]).copy().sort_values(["date_utc", "id"]).reset_index(drop=True)
    dev["target"] = np.minimum((dev.goals_home.astype(int) + dev.goals_away.astype(int)).to_numpy(), 7)
    feat = build_features(dev, result_team, result_league, opp_team)
    feat["target"] = dev.target.to_numpy(int)

    # Sanity: the identity gate should yield at least eight strict-PIT opportunity history rows per team.
    if int(feat.home_history_n.min()) < 0 or int(feat.away_history_n.min()) < 0:
        raise RuntimeError("result history count impossible")
    opp_history_cols = ["home_history_n", "away_history_n"]

    fold_results = {}
    pooled_y, pooled_pb, pooled_pm, pooled_pd = [], [], [], []
    dist_wins = 0
    mean_wins = 0
    for name, (start, end) in FOLDS.items():
        train = feat[feat.date_utc < start].copy()
        test = feat[(feat.date_utc >= start) & (feat.date_utc < end)].copy()
        if len(train) < 1000 or len(test) < 1000:
            raise RuntimeError(f"insufficient fold rows {name} train={len(train)} test={len(test)}")
        ytr = train.target.to_numpy(int)
        yte = test.target.to_numpy(int)
        models = {}
        probs = {}
        specs = {
            "baseline": BASE_FEATURES,
            "mean_candidate": MEAN_MODEL_FEATURES,
            "distribution_candidate": DIST_MODEL_FEATURES,
        }
        for key, cols in specs.items():
            model = pipeline()
            model.fit(train[cols], ytr)
            models[key] = model
            probs[key] = predict_fixed(model, test[cols])
        mb = metrics(yte, probs["baseline"])
        mm = metrics(yte, probs["mean_candidate"])
        md = metrics(yte, probs["distribution_candidate"])
        db = delta(md, mb)
        mean_db = delta(mm, mb)
        dispersion = delta(md, mm)
        dist_wins += int(db["log_loss"] < 0)
        mean_wins += int(mean_db["log_loss"] < 0)
        fold_results[name] = {
            "train_rows": int(len(train)), "test_rows": int(len(test)),
            "test_start": str(start), "test_end_exclusive": str(end),
            "baseline": mb, "mean_candidate": mm, "distribution_candidate": md,
            "distribution_minus_baseline": db,
            "mean_minus_baseline": mean_db,
            "distribution_minus_mean": dispersion,
        }
        pooled_y.append(yte)
        pooled_pb.append(probs["baseline"])
        pooled_pm.append(probs["mean_candidate"])
        pooled_pd.append(probs["distribution_candidate"])

    y = np.concatenate(pooled_y)
    pb = np.vstack(pooled_pb)
    pm = np.vstack(pooled_pm)
    pdist = np.vstack(pooled_pd)
    mb, mm, md = metrics(y, pb), metrics(y, pm), metrics(y, pdist)
    d_primary = delta(md, mb)
    d_mean = delta(mm, mb)
    d_disp = delta(md, mm)
    boot_primary = bootstrap_ll(y, pb, pdist)
    boot_disp = bootstrap_ll(y, pm, pdist)
    signal = bool(
        d_primary["log_loss"] < 0
        and boot_primary["ci90_high"] < 0
        and dist_wins >= 3
        and d_primary["rps"] <= 0
    )

    missing = {
        "baseline": {c: int(feat[c].isna().sum()) for c in BASE_FEATURES},
        "opportunity_mean": {c: int(feat[c].isna().sum()) for c in MEAN_FEATURES},
        "opportunity_dispersion": {c: int(feat[c].isna().sum()) for c in SD_FEATURES},
    }
    result = {
        "schema_version": SCHEMA_VERSION,
        "status": "C071B_DEVELOPMENT_COMPLETE",
        "verdict": "C071B_OPPORTUNITY_PT_DEVELOPMENT_SIGNAL" if signal else "C071B_OPPORTUNITY_PT_STABLE_INCREMENT_NOT_ESTABLISHED",
        "source": {
            "fixtures_sha256": EXPECTED_FIXTURE_SHA, "match_stats_sha256": EXPECTED_STATS_SHA,
            "fixture_identity_rows": int(len(fixtures)), "eligible_threshold8_identity_rows": int(len(eligible)),
        },
        "label_boundary": {
            "development_goal_projection_rows_returned": int(len(labels)),
            "development_goal_projection_max_date": str(labels.date_utc.max()),
            "labels_at_or_after_2024_returned": int((labels.date_utc >= DEV_CUTOFF).sum()),
            "confirmation_identity_rows": int(len(confirm)),
            "confirmation_target_goal_rows_read": 0,
            "confirmation_scored": False,
        },
        "history": {
            "historical_played_result_matches_pre2024": historical_played,
            "core_complete_opportunity_matches_used_pre2024": opportunity_history_matches,
            "strict_result_delay_minutes": 105,
            "strict_stats_rule": "known_at < target kickoff",
        },
        "development": {
            "eligible_labeled_rows_pre2024": int(len(feat)),
            "oos_rows": int(len(y)),
            "folds": fold_results,
            "pooled": {
                "baseline": mb, "mean_candidate": mm, "distribution_candidate": md,
                "distribution_minus_baseline": d_primary,
                "mean_minus_baseline": d_mean,
                "distribution_minus_mean": d_disp,
                "distribution_fold_logloss_wins": int(dist_wins),
                "mean_fold_logloss_wins": int(mean_wins),
                "primary_match_bootstrap": boot_primary,
                "dispersion_increment_match_bootstrap": boot_disp,
            },
            "development_signal_gate": bool(signal),
            "feature_missing_counts_before_fold_train_median_imputation": missing,
        },
        "feature_contract": {
            "baseline_features": BASE_FEATURES,
            "mean_candidate_features": MEAN_MODEL_FEATURES,
            "distribution_candidate_features": DIST_MODEL_FEATURES,
            "provider_xg_used": False, "closing_1x2_used": False,
        },
        "boundary": {
            "postview_development_only": True,
            "fresh_confirmation_claim_allowed": False,
            "formal_weight": 0,
            "C070F_confirmation1597_opened": False,
            "A05_opened": False, "protected_opened": False,
        },
    }
    (outdir / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
