#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

SOURCE_REVISION = "279978313f9c16a210fa80e8986fa22f0f866fba"
TEST_SEASONS = ["2019-2020", "2020-2021", "2021-2022", "2022-2023", "2023-2024"]
C_FIXED = 0.1
MIN_HISTORY = 8
K = 8
CLASSES = np.arange(K, dtype=int)
BOOTSTRAPS = 3000
BOOTSTRAP_SEED = 20260819
USECOLS = [
    "Date", "country", "league", "Season", "HomeTeam", "AwayTeam", "FTHG", "FTAG",
    "over_2.5_open", "under_2.5_open", "over_2.5_close", "under_2.5_close",
]
ODD_COLS = ["over_2.5_open", "under_2.5_open", "over_2.5_close", "under_2.5_close"]
BASE = [
    "competition_total_mean", "competition_total_sd",
    "home_goals_for_mean", "home_goals_for_sd", "home_goals_against_mean", "home_goals_against_sd",
    "away_goals_for_mean", "away_goals_for_sd", "away_goals_against_mean", "away_goals_against_sd",
    "log1p_home_result_history_n", "log1p_away_result_history_n",
]
CANDIDATE = BASE + ["movement_logit"]


def devig_over(over_odds: pd.Series, under_odds: pd.Series) -> pd.Series:
    io = 1.0 / over_odds
    iu = 1.0 / under_odds
    return io / (io + iu)


def logit_arr(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def season_start(s: str) -> int:
    return int(str(s).split("-")[0])


def mean_sd(vals):
    if len(vals) == 0:
        return np.nan, np.nan
    a = np.asarray(vals, dtype=float)
    return float(a.mean()), float(a.std(ddof=0))


def team_profile(h):
    gf_mu, gf_sd = mean_sd(h["gf"])
    ga_mu, ga_sd = mean_sd(h["ga"])
    return {
        "result_history_n": len(h["gf"]),
        "goals_for_mean": gf_mu,
        "goals_for_sd": gf_sd,
        "goals_against_mean": ga_mu,
        "goals_against_sd": ga_sd,
    }


def fresh_history():
    return {"gf": [], "ga": []}


def load_source(root: Path) -> pd.DataFrame:
    frames = []
    for path in sorted(root.glob("data/**/*.csv")):
        try:
            d = pd.read_csv(path, usecols=USECOLS, low_memory=False)
        except ValueError:
            continue
        d["source_file"] = str(path.relative_to(root))
        frames.append(d)
    if not frames:
        raise RuntimeError("no source csv files with frozen columns")
    df = pd.concat(frames, ignore_index=True)
    for c in ODD_COLS + ["FTHG", "FTAG"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["Date"], errors="coerce")
    date_ok = df["date"].notna()
    odds_ok = df[ODD_COLS].notna().all(axis=1) & (df[ODD_COLS] > 1.0).all(axis=1)
    goals_ok = df[["FTHG", "FTAG"]].notna().all(axis=1) & (df[["FTHG", "FTAG"]] >= 0).all(axis=1)
    season_ok = df["Season"].astype(str).str.match(r"^\d{4}-\d{4}$")
    team_ok = df[["HomeTeam", "AwayTeam", "country", "league"]].notna().all(axis=1)
    df = df.loc[date_ok & odds_ok & goals_ok & season_ok & team_ok].copy()
    df["league_key"] = df["country"].astype(str) + "|" + df["league"].astype(str)
    df["home_key"] = df["league_key"] + "|" + df["HomeTeam"].astype(str)
    df["away_key"] = df["league_key"] + "|" + df["AwayTeam"].astype(str)
    df["T"] = np.minimum((df["FTHG"] + df["FTAG"]).astype(int), 7)
    p_open = devig_over(df["over_2.5_open"], df["under_2.5_open"])
    p_close = devig_over(df["over_2.5_close"], df["under_2.5_close"])
    df["movement_logit"] = logit_arr(p_close) - logit_arr(p_open)
    df["season_start"] = df["Season"].map(season_start)
    return df.sort_values(["date", "league_key", "HomeTeam", "AwayTeam"]).reset_index(drop=True)


def build_history_features(df: pd.DataFrame) -> pd.DataFrame:
    hist = defaultdict(fresh_history)
    comp_totals = defaultdict(list)
    rows = []
    # All same-date fixtures are featurized before any same-date result update.
    for date, g in df.groupby("date", sort=True):
        pending = []
        for idx, r in g.iterrows():
            hp = team_profile(hist[r["home_key"]])
            ap = team_profile(hist[r["away_key"]])
            cm, cs = mean_sd(comp_totals[r["league_key"]])
            row = {
                "row_id": int(idx),
                "date": r["date"],
                "Season": r["Season"],
                "season_start": int(r["season_start"]),
                "league_key": r["league_key"],
                "HomeTeam": r["HomeTeam"],
                "AwayTeam": r["AwayTeam"],
                "competition_total_mean": cm,
                "competition_total_sd": cs,
                "home_goals_for_mean": hp["goals_for_mean"],
                "home_goals_for_sd": hp["goals_for_sd"],
                "home_goals_against_mean": hp["goals_against_mean"],
                "home_goals_against_sd": hp["goals_against_sd"],
                "away_goals_for_mean": ap["goals_for_mean"],
                "away_goals_for_sd": ap["goals_for_sd"],
                "away_goals_against_mean": ap["goals_against_mean"],
                "away_goals_against_sd": ap["goals_against_sd"],
                "log1p_home_result_history_n": math.log1p(hp["result_history_n"]),
                "log1p_away_result_history_n": math.log1p(ap["result_history_n"]),
                "movement_logit": float(r["movement_logit"]),
                "target": int(r["T"]),
                "eligible": hp["result_history_n"] >= MIN_HISTORY and ap["result_history_n"] >= MIN_HISTORY,
            }
            rows.append(row)
            pending.append(r)
        for r in pending:
            hg = int(r["FTHG"])
            ag = int(r["FTAG"])
            hh = hist[r["home_key"]]
            ah = hist[r["away_key"]]
            hh["gf"].append(float(hg)); hh["ga"].append(float(ag))
            ah["gf"].append(float(ag)); ah["ga"].append(float(hg))
            comp_totals[r["league_key"]].append(float(hg + ag))
    out = pd.DataFrame(rows)
    return out.sort_values(["date", "league_key", "HomeTeam", "AwayTeam"]).reset_index(drop=True)


def pipeline():
    return make_pipeline(
        SimpleImputer(strategy="median"),
        StandardScaler(),
        LogisticRegression(
            C=C_FIXED,
            max_iter=3000,
            class_weight=None,
            random_state=0,
            solver="lbfgs",
            multi_class="multinomial",
        ),
    )


def aligned_proba(model, X: pd.DataFrame) -> np.ndarray:
    raw = model.predict_proba(X)
    cls = model.named_steps["logisticregression"].classes_.astype(int)
    out = np.zeros((len(X), K), dtype=float)
    out[:, cls] = raw
    out = np.clip(out, 1e-15, 1.0)
    out = out / out.sum(axis=1, keepdims=True)
    if not np.allclose(out.sum(axis=1), 1.0, atol=1e-10):
        raise RuntimeError("probability conservation failed")
    return out


def row_metrics(y: np.ndarray, p: np.ndarray):
    n = len(y)
    one = np.eye(K)[y]
    ll = -np.log(np.clip(p[np.arange(n), y], 1e-15, 1.0))
    brier = np.square(p - one).sum(axis=1)
    cp = np.cumsum(p, axis=1)[:, :-1]
    cy = np.cumsum(one, axis=1)[:, :-1]
    rps = np.square(cp - cy).sum(axis=1).mean(axis=1)
    top1 = (np.argmax(p, axis=1) == y).astype(float)
    top3_idx = np.argpartition(p, -3, axis=1)[:, -3:]
    top3 = np.array([float(y[i] in top3_idx[i]) for i in range(n)])
    return {"ll": ll, "brier": brier, "rps": rps, "top1": top1, "top3": top3}


def ece_binary(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = len(y)
    ece = 0.0
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (p >= lo) & ((p < hi) if i < bins - 1 else (p <= hi))
        if mask.any():
            ece += mask.mean() * abs(float(y[mask].mean()) - float(p[mask].mean()))
    return float(ece)


def ece_toplabel(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    pred = np.argmax(p, axis=1)
    conf = np.max(p, axis=1)
    correct = (pred == y).astype(float)
    return ece_binary(correct, conf, bins=bins)


def logistic_calibration(y_binary: np.ndarray, p: np.ndarray):
    yb = np.asarray(y_binary, dtype=float)
    if len(np.unique(yb)) < 2:
        return {"intercept": None, "slope": None}
    x = logit_arr(p)
    X = np.column_stack([np.ones(len(x)), x])
    beta = np.array([0.0, 1.0], dtype=float)
    for _ in range(100):
        eta = np.clip(X @ beta, -30, 30)
        mu = 1.0 / (1.0 + np.exp(-eta))
        w = np.maximum(mu * (1 - mu), 1e-8)
        z = eta + (yb - mu) / w
        xtwx = X.T @ (w[:, None] * X) + np.eye(2) * 1e-8
        xtwz = X.T @ (w * z)
        try:
            new = np.linalg.solve(xtwx, xtwz)
        except np.linalg.LinAlgError:
            return {"intercept": None, "slope": None}
        if np.max(np.abs(new - beta)) < 1e-8:
            beta = new
            break
        beta = new
    return {"intercept": float(beta[0]), "slope": float(beta[1])}


def calibration_report(y: np.ndarray, p: np.ndarray):
    per_class = {}
    for k in range(K):
        per_class[str(k)] = logistic_calibration((y == k).astype(int), p[:, k])
    ge3_p = p[:, 3:].sum(axis=1)
    return {
        "classwise_ovr": per_class,
        "toplabel_ece_10": ece_toplabel(y, p, 10),
        "t_ge_3_ece_10": ece_binary((y >= 3).astype(int), ge3_p, 10),
        "t_ge_3_calibration": logistic_calibration((y >= 3).astype(int), ge3_p),
    }


def summarize(y: np.ndarray, p: np.ndarray, rows):
    auc3 = roc_auc_score((y >= 3).astype(int), p[:, 3:].sum(axis=1))
    return {
        "n": int(len(y)),
        "logloss": float(rows["ll"].mean()),
        "brier": float(rows["brier"].mean()),
        "rps": float(rows["rps"].mean()),
        "top1": float(rows["top1"].mean()),
        "top3": float(rows["top3"].mean()),
        "auc_t_ge_3": float(auc3),
        "probability_sum_max_abs_error": float(np.max(np.abs(p.sum(axis=1) - 1.0))),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--external-root", required=True)
    ap.add_argument("--out", default="artifacts/c074e_directt")
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)

    raw = load_source(Path(args.external_root))
    feat = build_history_features(raw)
    elig = feat.loc[feat["eligible"]].copy()
    if elig.empty:
        raise RuntimeError("no eligible score-history rows")

    fold_records = []
    all_y, all_pb, all_pc = [], [], []
    all_mb = {k: [] for k in ["ll", "brier", "rps", "top1", "top3"]}
    all_mc = {k: [] for k in ["ll", "brier", "rps", "top1", "top3"]}

    for test_season in TEST_SEASONS:
        test_start = season_start(test_season)
        train = elig.loc[elig["season_start"] < test_start].copy()
        test = elig.loc[elig["Season"] == test_season].copy()
        if train.empty or test.empty:
            raise RuntimeError(f"empty frozen fold {test_season}")
        y_train = train["target"].to_numpy(dtype=int)
        y_test = test["target"].to_numpy(dtype=int)
        missing_classes = set(CLASSES) - set(np.unique(y_train))
        if missing_classes:
            raise RuntimeError(f"training fold {test_season} missing target classes {sorted(missing_classes)}")

        baseline = pipeline(); candidate = pipeline()
        baseline.fit(train[BASE], y_train)
        candidate.fit(train[CANDIDATE], y_train)
        pb = aligned_proba(baseline, test[BASE])
        pc = aligned_proba(candidate, test[CANDIDATE])
        mb = row_metrics(y_test, pb); mc = row_metrics(y_test, pc)
        sb = summarize(y_test, pb, mb); sc = summarize(y_test, pc, mc)
        fold_records.append({
            "test_season": test_season,
            "train_n": int(len(train)),
            "test_n": int(len(test)),
            "baseline": sb,
            "candidate": sc,
            "delta_candidate_minus_baseline": {
                "logloss": sc["logloss"] - sb["logloss"],
                "brier": sc["brier"] - sb["brier"],
                "rps": sc["rps"] - sb["rps"],
                "top1_pp": 100.0 * (sc["top1"] - sb["top1"]),
                "top3_pp": 100.0 * (sc["top3"] - sb["top3"]),
                "auc_t_ge_3": sc["auc_t_ge_3"] - sb["auc_t_ge_3"],
            },
        })
        all_y.append(y_test); all_pb.append(pb); all_pc.append(pc)
        for k in all_mb:
            all_mb[k].append(mb[k]); all_mc[k].append(mc[k])

    y = np.concatenate(all_y)
    pb = np.vstack(all_pb); pc = np.vstack(all_pc)
    mb = {k: np.concatenate(v) for k, v in all_mb.items()}
    mc = {k: np.concatenate(v) for k, v in all_mc.items()}
    sb = summarize(y, pb, mb); sc = summarize(y, pc, mc)
    cal_b = calibration_report(y, pb); cal_c = calibration_report(y, pc)

    dll = mc["ll"] - mb["ll"]
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    boot = np.empty(BOOTSTRAPS, dtype=float)
    n = len(dll)
    for b in range(BOOTSTRAPS):
        idx = rng.integers(0, n, size=n)
        boot[b] = float(dll[idx].mean())
    boot90 = [float(np.quantile(boot, 0.05)), float(np.quantile(boot, 0.95))]

    fold_ll_wins = int(sum(r["delta_candidate_minus_baseline"]["logloss"] < 0 for r in fold_records))
    deltas = {
        "logloss": sc["logloss"] - sb["logloss"],
        "brier": sc["brier"] - sb["brier"],
        "rps": sc["rps"] - sb["rps"],
        "top1_pp": 100.0 * (sc["top1"] - sb["top1"]),
        "top3_pp": 100.0 * (sc["top3"] - sb["top3"]),
        "auc_t_ge_3": sc["auc_t_ge_3"] - sb["auc_t_ge_3"],
        "toplabel_ece": cal_c["toplabel_ece_10"] - cal_b["toplabel_ece_10"],
        "t_ge_3_ece": cal_c["t_ge_3_ece_10"] - cal_b["t_ge_3_ece_10"],
    }
    gate = {
        "pooled_dlogloss_lt_0": deltas["logloss"] < 0,
        "fold_ll_wins_ge_4_of_5": fold_ll_wins >= 4,
        "pooled_dbrier_le_0": deltas["brier"] <= 0,
        "pooled_drps_le_0": deltas["rps"] <= 0,
        "bootstrap90_upper_dlogloss_lt_0": boot90[1] < 0,
    }
    passed = all(gate.values())

    summary = {
        "experiment": "C074-E",
        "contract": "research/C074E_CONTRACT.md",
        "research_only": True,
        "formal_weight": 0,
        "development_bridge_not_confirmation": True,
        "source_repo": "nm2890/football-data",
        "source_revision": SOURCE_REVISION,
        "source_pit_boundary": "opening/closing semantics only; no immutable per-row quote timestamp; single O/U2.5 line",
        "raw_valid_rows": int(len(raw)),
        "eligible_score_history_rows": int(len(elig)),
        "oos_rows": int(len(y)),
        "test_seasons": TEST_SEASONS,
        "model": {
            "type": "C072-B score-history multinomial logistic family",
            "C": C_FIXED,
            "min_prior_team_results": MIN_HISTORY,
            "same_date_update": "predict all same-date fixtures before any same-date result update",
            "baseline_features": BASE,
            "candidate_added_feature": "movement_logit=logit(devig_over_close)-logit(devig_over_open)",
            "feature_search": 0,
            "C_search": 0,
            "alternate_folds": 0,
            "alternate_movement_transforms": 0,
        },
        "baseline": sb,
        "candidate": sc,
        "delta_candidate_minus_baseline": deltas,
        "fold_ll_wins": fold_ll_wins,
        "paired_bootstrap": {"n": BOOTSTRAPS, "seed": BOOTSTRAP_SEED, "dlogloss_90_interval": boot90},
        "calibration": {"baseline": cal_b, "candidate": cal_c},
        "gate_checks": gate,
        "terminal": "SCORE_HISTORY_CONDITIONAL_MOVEMENT_PASS" if passed else "SCORE_HISTORY_CONDITIONAL_MOVEMENT_NOT_ESTABLISHED",
        "folds": fold_records,
        "sealed_assets_touched": {
            "C071_reserve_52180": 0,
            "C070F_confirmation_1597": 0,
            "A05": 0,
            "protected": 0,
        },
        "post_result_authorization": (
            "If PASS: freeze the exact method and seek an untouched later public period or genuinely unseen external/forward domain. "
            "If FAIL: do not tune or repair on these labels."
        ),
    }

    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    pd.DataFrame([
        {
            "test_season": r["test_season"], "train_n": r["train_n"], "test_n": r["test_n"],
            "baseline_ll": r["baseline"]["logloss"], "candidate_ll": r["candidate"]["logloss"],
            "d_ll": r["delta_candidate_minus_baseline"]["logloss"],
            "d_brier": r["delta_candidate_minus_baseline"]["brier"],
            "d_rps": r["delta_candidate_minus_baseline"]["rps"],
            "d_top1_pp": r["delta_candidate_minus_baseline"]["top1_pp"],
            "d_top3_pp": r["delta_candidate_minus_baseline"]["top3_pp"],
            "d_auc_t_ge_3": r["delta_candidate_minus_baseline"]["auc_t_ge_3"],
        }
        for r in fold_records
    ]).to_csv(out / "fold_metrics.csv", index=False)
    (out / "calibration.json").write_text(json.dumps(summary["calibration"], indent=2), encoding="utf-8")
    print(json.dumps({
        "terminal": summary["terminal"],
        "oos_rows": summary["oos_rows"],
        "baseline_ll": sb["logloss"],
        "candidate_ll": sc["logloss"],
        "dlogloss": deltas["logloss"],
        "dbrier": deltas["brier"],
        "drps": deltas["rps"],
        "fold_ll_wins": fold_ll_wins,
        "bootstrap90": boot90,
        "d_top1_pp": deltas["top1_pp"],
        "d_top3_pp": deltas["top3_pp"],
    }, indent=2))


if __name__ == "__main__":
    main()
