#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

SCHEMA = "C072D3_OU25_MOVEMENT_PT_DEVELOPMENT_V1"
REV = "279978313f9c16a210fa80e8986fa22f0f866fba"
REPO = "nm2890/football-data"
FILES = [
    "data/england/premier-league.csv",
    "data/spain/laliga.csv",
    "data/italy/serie-a.csv",
    "data/germany/bundesliga.csv",
    "data/france/ligue-1.csv",
    "data/belgium/jupiler-pro-league.csv",
    "data/netherlands/eredivisie.csv",
    "data/egypt/premier-league.csv",
]
DEV_SEASONS = [
    "2009-2010", "2010-2011", "2011-2012", "2012-2013", "2013-2014",
    "2014-2015", "2015-2016", "2016-2017", "2017-2018", "2018-2019",
    "2019-2020", "2020-2021", "2021-2022", "2022-2023", "2023-2024",
]
TEST_SEASONS = ["2019-2020", "2020-2021", "2021-2022", "2022-2023", "2023-2024"]
SEALED_FORWARD = "2024-2025"
MIN_HISTORY = 8
MIN_TRAIN = 10000
MIN_TEST = 1500
MIN_MARKET_FRAC = 0.95
K = 8
C = 0.1
BOOT_REPS = 3000
BOOT_SEED = 72003
BASE = [
    "competition_total_mean", "competition_total_sd",
    "home_goals_for_mean", "home_goals_for_sd", "home_goals_against_mean", "home_goals_against_sd",
    "away_goals_for_mean", "away_goals_for_sd", "away_goals_against_mean", "away_goals_against_sd",
    "log1p_home_result_history_n", "log1p_away_result_history_n",
]
CANDIDATE = BASE + ["movement_logit"]
NONLABEL = [
    "Date", "country", "league", "Season", "HomeTeam", "AwayTeam",
    "over_2.5_open", "under_2.5_open", "over_2.5_close", "under_2.5_close",
]


def raw_url(path: str) -> str:
    return f"https://raw.githubusercontent.com/{REPO}/{REV}/{path}"


def fetch(path: str) -> bytes:
    req = urllib.request.Request(raw_url(path), headers={"User-Agent": "football3-c072d3"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def parse_price(x: str) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 1.0 else None


def movement_logit(oo: float, uo: float, oc: float, uc: float) -> float:
    p0 = (1.0 / oo) / ((1.0 / oo) + (1.0 / uo))
    p1 = (1.0 / oc) / ((1.0 / oc) + (1.0 / uc))
    return math.log(p1 / (1.0 - p1)) - math.log(p0 / (1.0 - p0))


def load_source() -> tuple[pd.DataFrame, dict]:
    rows: list[dict] = []
    file_stats: list[dict] = []
    sealed_forward_identities = 0
    sealed_forward_target_values_read = 0
    required_header = set(NONLABEL + ["FTHG", "FTAG"])

    for path in FILES:
        raw = fetch(path)
        sha = hashlib.sha256(raw).hexdigest()
        reader = csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""))
        header = next(reader)
        idx = {name: i for i, name in enumerate(header)}
        missing = sorted(required_header - set(idx))
        if missing:
            raise RuntimeError(f"{path}: missing required headers {missing}")
        parsed = 0
        dev_labels = 0
        sealed_ids = 0
        max_nonlabel = max(idx[x] for x in NONLABEL)
        for raw_row in reader:
            if not raw_row or len(raw_row) <= max_nonlabel:
                continue
            # Read non-label identity/market fields first. This is permitted for every season.
            selected = {name: raw_row[idx[name]] for name in NONLABEL}
            season = selected["Season"].strip()
            if season == SEALED_FORWARD:
                # Binding seal: do not index/access FTHG or FTAG for 2024-2025.
                sealed_forward_identities += 1
                sealed_ids += 1
                continue
            if season not in DEV_SEASONS:
                continue
            # Only historical development seasons may materialize goal labels.
            try:
                hg_raw = raw_row[idx["FTHG"]]
                ag_raw = raw_row[idx["FTAG"]]
                hg = int(float(hg_raw))
                ag = int(float(ag_raw))
            except (IndexError, TypeError, ValueError):
                continue
            if hg < 0 or ag < 0:
                continue
            date = pd.to_datetime(selected["Date"], errors="coerce")
            if pd.isna(date):
                continue
            prices = [
                parse_price(selected["over_2.5_open"]), parse_price(selected["under_2.5_open"]),
                parse_price(selected["over_2.5_close"]), parse_price(selected["under_2.5_close"]),
            ]
            market_complete = all(x is not None for x in prices)
            move = None
            if market_complete:
                move = movement_logit(float(prices[0]), float(prices[1]), float(prices[2]), float(prices[3]))
            rows.append({
                "date": pd.Timestamp(date),
                "date_key": pd.Timestamp(date).normalize(),
                "country": selected["country"].strip(),
                "league": selected["league"].strip(),
                "season": season,
                "home_team": selected["HomeTeam"].strip(),
                "away_team": selected["AwayTeam"].strip(),
                "home_goals": hg,
                "away_goals": ag,
                "target": min(hg + ag, 7),
                "market_complete": bool(market_complete),
                "movement_logit": float(move) if move is not None else np.nan,
                "source_file": path,
            })
            parsed += 1
            dev_labels += 1
        file_stats.append({
            "path": path, "sha256": sha, "bytes": len(raw),
            "development_label_rows_materialized": dev_labels,
            "sealed_forward_identity_rows_seen": sealed_ids,
            "sealed_forward_target_values_read": 0,
        })

    frame = pd.DataFrame(rows)
    if frame.empty:
        raise RuntimeError("no development rows parsed")
    frame = frame.sort_values(["date_key", "date", "country", "league", "home_team", "away_team"]).reset_index(drop=True)
    meta = {
        "file_stats": file_stats,
        "development_rows": int(len(frame)),
        "sealed_forward_identity_rows_seen": int(sealed_forward_identities),
        "sealed_forward_target_values_read": int(sealed_forward_target_values_read),
    }
    return frame, meta


def mean_sd(values: list[float]) -> tuple[float, float]:
    if not values:
        return (np.nan, np.nan)
    a = np.asarray(values, dtype=float)
    return (float(a.mean()), float(a.std(ddof=0)))


def fresh_team() -> dict:
    return {"gf": [], "ga": []}


def build_pit_features(frame: pd.DataFrame) -> pd.DataFrame:
    team_hist: dict[tuple[str, str, str], dict] = defaultdict(fresh_team)
    comp_totals: dict[tuple[str, str], list[float]] = defaultdict(list)
    out: list[dict] = []

    for date_key, group in frame.groupby("date_key", sort=True):
        # Predict all matches on the same calendar date before any result update.
        for _, r in group.iterrows():
            comp = (str(r.country), str(r.league))
            hk = (str(r.country), str(r.league), str(r.home_team))
            ak = (str(r.country), str(r.league), str(r.away_team))
            hh = team_hist[hk]
            ah = team_hist[ak]
            cm, cs = mean_sd(comp_totals[comp])
            hgf_m, hgf_s = mean_sd(hh["gf"])
            hga_m, hga_s = mean_sd(hh["ga"])
            agf_m, agf_s = mean_sd(ah["gf"])
            aga_m, aga_s = mean_sd(ah["ga"])
            out.append({
                "date": r.date, "date_key": date_key, "country": r.country, "league": r.league,
                "season": r.season, "home_team": r.home_team, "away_team": r.away_team,
                "target": int(r.target), "market_complete": bool(r.market_complete),
                "movement_logit": float(r.movement_logit) if pd.notna(r.movement_logit) else np.nan,
                "competition_total_mean": cm, "competition_total_sd": cs,
                "home_goals_for_mean": hgf_m, "home_goals_for_sd": hgf_s,
                "home_goals_against_mean": hga_m, "home_goals_against_sd": hga_s,
                "away_goals_for_mean": agf_m, "away_goals_for_sd": agf_s,
                "away_goals_against_mean": aga_m, "away_goals_against_sd": aga_s,
                "log1p_home_result_history_n": math.log1p(len(hh["gf"])),
                "log1p_away_result_history_n": math.log1p(len(ah["gf"])),
                "eligible": len(hh["gf"]) >= MIN_HISTORY and len(ah["gf"]) >= MIN_HISTORY,
            })
        for _, r in group.iterrows():
            comp = (str(r.country), str(r.league))
            hk = (str(r.country), str(r.league), str(r.home_team))
            ak = (str(r.country), str(r.league), str(r.away_team))
            hg, ag = float(r.home_goals), float(r.away_goals)
            team_hist[hk]["gf"].append(hg); team_hist[hk]["ga"].append(ag)
            team_hist[ak]["gf"].append(ag); team_hist[ak]["ga"].append(hg)
            comp_totals[comp].append(hg + ag)

    return pd.DataFrame(out).sort_values(["date_key", "date", "country", "league", "home_team", "away_team"]).reset_index(drop=True)


def pipeline():
    return make_pipeline(
        SimpleImputer(strategy="median"),
        StandardScaler(),
        LogisticRegression(C=C, max_iter=3000, class_weight=None, random_state=0, solver="lbfgs"),
    )


def aligned_predict(model, X: pd.DataFrame) -> np.ndarray:
    p = model.predict_proba(X)
    classes = model.named_steps["logisticregression"].classes_.astype(int)
    out = np.zeros((len(X), K), dtype=float)
    out[:, classes] = p
    out = np.clip(out, 1e-15, 1.0)
    out /= out.sum(axis=1, keepdims=True)
    return out


def multiclass_metrics(y, p) -> dict:
    y = np.asarray(y, dtype=int)
    one = np.eye(K)[y]
    cp = np.cumsum(p, axis=1)[:, :-1]
    cy = np.cumsum(one, axis=1)[:, :-1]
    top1 = np.argmax(p, axis=1)
    top3 = np.argsort(p, axis=1)[:, -3:]

    def auc(threshold: int):
        yy = (y >= threshold).astype(int)
        if len(np.unique(yy)) < 2:
            return None
        return float(roc_auc_score(yy, p[:, threshold:].sum(axis=1)))

    return {
        "n": int(len(y)),
        "log_loss": float(log_loss(y, p, labels=list(range(K)))),
        "brier": float(np.mean(np.sum((p - one) ** 2, axis=1))),
        "rps": float(np.mean(np.sum((cp - cy) ** 2, axis=1) / (K - 1))),
        "top1_accuracy": float(np.mean(top1 == y)),
        "top3_accuracy": float(np.mean([y[i] in top3[i] for i in range(len(y))])),
        "auc_t_ge_3": auc(3),
        "auc_t_ge_4": auc(4),
    }


def metric_delta(candidate: dict, baseline: dict) -> dict:
    out = {}
    for k in ["log_loss", "brier", "rps", "top1_accuracy", "top3_accuracy", "auc_t_ge_3", "auc_t_ge_4"]:
        if candidate[k] is None or baseline[k] is None:
            out[k] = None
        else:
            out[k] = float(candidate[k] - baseline[k])
    return out


def paired_bootstrap_delta_ll(y, p0, p1) -> dict:
    y = np.asarray(y, dtype=int)
    ix = np.arange(len(y))
    d = -np.log(np.clip(p1[ix, y], 1e-15, 1.0)) + np.log(np.clip(p0[ix, y], 1e-15, 1.0))
    rng = np.random.default_rng(BOOT_SEED)
    n = len(d)
    sims = np.empty(BOOT_REPS, dtype=float)
    for i in range(BOOT_REPS):
        draw = rng.integers(0, n, n)
        sims[i] = float(d[draw].mean())
    return {
        "n": n,
        "reps": BOOT_REPS,
        "seed": BOOT_SEED,
        "mean_delta_log_loss": float(d.mean()),
        "ci90_low": float(np.quantile(sims, 0.05)),
        "ci90_high": float(np.quantile(sims, 0.95)),
        "p_delta_lt_0": float(np.mean(sims < 0.0)),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="football-data/research/c072d3_ou25_movement_pt_summary.json")
    args = ap.parse_args()

    frame, source_meta = load_source()
    feat = build_pit_features(frame)
    eligible = feat[feat["eligible"]].copy()
    season_order = {s: i for i, s in enumerate(DEV_SEASONS)}

    fold_outputs = []
    pooled_y = []
    pooled_p0 = []
    pooled_p1 = []
    coverage_pass = True
    fold_wins = 0
    max_prob_residual = 0.0

    for test_season in TEST_SEASONS:
        test_idx = season_order[test_season]
        train = eligible[eligible["season"].map(season_order).fillna(999).astype(int) < test_idx].copy()
        test_all = eligible[eligible["season"] == test_season].copy()
        market_frac = float(test_all["market_complete"].mean()) if len(test_all) else 0.0
        # Fair paired comparison: baseline and candidate use identical complete-market rows.
        train = train[train["market_complete"]].copy()
        test = test_all[test_all["market_complete"]].copy()

        fold_cov = len(train) >= MIN_TRAIN and len(test) >= MIN_TEST and market_frac >= MIN_MARKET_FRAC
        coverage_pass = coverage_pass and fold_cov
        if not fold_cov:
            fold_outputs.append({
                "test_season": test_season,
                "train_n": int(len(train)), "test_all_eligible_n": int(len(test_all)), "test_n": int(len(test)),
                "market_complete_fraction": market_frac, "coverage_pass": False,
            })
            continue

        m0 = pipeline(); m1 = pipeline()
        m0.fit(train[BASE], train["target"].astype(int))
        m1.fit(train[CANDIDATE], train["target"].astype(int))
        p0 = aligned_predict(m0, test[BASE])
        p1 = aligned_predict(m1, test[CANDIDATE])
        y = test["target"].astype(int).to_numpy()
        max_prob_residual = max(max_prob_residual, float(np.max(np.abs(p0.sum(axis=1) - 1.0))), float(np.max(np.abs(p1.sum(axis=1) - 1.0))))
        b = multiclass_metrics(y, p0); c = multiclass_metrics(y, p1); d = metric_delta(c, b)
        if d["log_loss"] < 0:
            fold_wins += 1
        fold_outputs.append({
            "test_season": test_season,
            "train_n": int(len(train)), "test_all_eligible_n": int(len(test_all)), "test_n": int(len(test)),
            "market_complete_fraction": market_frac, "coverage_pass": True,
            "baseline": b, "candidate": c, "delta_candidate_minus_baseline": d,
            "movement_logit_abs_mean": float(test["movement_logit"].abs().mean()),
        })
        pooled_y.append(y); pooled_p0.append(p0); pooled_p1.append(p1)

    if not pooled_y:
        summary = {
            "schema_version": SCHEMA, "terminal": "STOP_COVERAGE", "coverage_pass": False,
            "source_meta": source_meta, "folds": fold_outputs,
            "sealed_forward_2024_25_target_values_read": source_meta["sealed_forward_target_values_read"],
        }
        Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 2

    y = np.concatenate(pooled_y)
    p0 = np.vstack(pooled_p0)
    p1 = np.vstack(pooled_p1)
    pooled_b = multiclass_metrics(y, p0)
    pooled_c = multiclass_metrics(y, p1)
    pooled_d = metric_delta(pooled_c, pooled_b)
    boot = paired_bootstrap_delta_ll(y, p0, p1)

    gates = {
        "coverage": bool(coverage_pass and len(fold_outputs) == 5 and all(f.get("coverage_pass", False) for f in fold_outputs)),
        "pooled_dlogloss_lt_0": pooled_d["log_loss"] < 0,
        "bootstrap90_upper_lt_0": boot["ci90_high"] < 0,
        "fold_wins_ge_4_of_5": fold_wins >= 4,
        "brier_nonworse": pooled_d["brier"] <= 0,
        "rps_nonworse": pooled_d["rps"] <= 0,
        "probability_residual_le_1e12": max_prob_residual <= 1e-12,
        "sealed_2024_25_labels_untouched": source_meta["sealed_forward_target_values_read"] == 0,
    }
    passed = all(gates.values())
    summary = {
        "schema_version": SCHEMA,
        "project": "football3",
        "scientific_parent_c072c": "e3e73c998020beef585cc459a69ea5b73b44ddb3",
        "quarantine_c073_c077": True,
        "source_repo": REPO,
        "source_revision": REV,
        "source_market_semantics": "average O/U2.5 opening/closing only; coarse research proxy",
        "source_meta": source_meta,
        "development_oos_n": int(len(y)),
        "folds": fold_outputs,
        "fold_logloss_wins": int(fold_wins),
        "pooled_baseline": pooled_b,
        "pooled_candidate": pooled_c,
        "pooled_delta_candidate_minus_baseline": pooled_d,
        "paired_bootstrap_dlogloss": boot,
        "max_probability_conservation_residual": max_prob_residual,
        "gates": gates,
        "development_pass": bool(passed),
        "terminal": "C072D3_MOVEMENT_PT_DEVELOPMENT_PASS" if passed else "C072D3_MOVEMENT_PT_DEVELOPMENT_FAIL_PARK",
        "sealed_forward_2024_25_target_values_read": source_meta["sealed_forward_target_values_read"],
        "formal_weight": 0,
        "interpretation_boundary": "Research development only; not timestamped multi-line O/U, not formal promotion, not exact-score/draw resolution.",
    }
    rendered = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
    Path(args.out).write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0 if passed else 3


if __name__ == "__main__":
    sys.exit(main())
