#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import math
from collections import defaultdict
from pathlib import Path
from urllib.request import urlopen

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

SCHEMA = "C072E2_OU25_MOVEMENT_DIRECTT_DEVELOPMENT_V1"
REV = "279978313f9c16a210fa80e8986fa22f0f866fba"
RAW = f"https://raw.githubusercontent.com/nm2890/football-data/{REV}/"
FILES = [
    "data/belgium/jupiler-pro-league.csv",
    "data/egypt/premier-league.csv",
    "data/england/premier-league.csv",
    "data/france/ligue-1.csv",
    "data/germany/bundesliga.csv",
    "data/italy/serie-a.csv",
    "data/netherlands/eredivisie.csv",
    "data/spain/laliga.csv",
]
ID_ODDS = [
    "Date", "Season", "HomeTeam", "AwayTeam",
    "over_2.5_open", "over_2.5_close", "under_2.5_open", "under_2.5_close",
]
BASE = [
    "competition_total_mean", "competition_total_sd",
    "home_goals_for_mean", "home_goals_for_sd", "home_goals_against_mean", "home_goals_against_sd",
    "away_goals_for_mean", "away_goals_for_sd", "away_goals_against_mean", "away_goals_against_sd",
    "log1p_home_result_history_n", "log1p_away_result_history_n",
]
REF = BASE + ["open_logit"]
CAND = REF + ["movement_logit"]
TEST_SEASONS = [2019, 2020, 2021, 2022, 2023]
MIN_HISTORY = 8
MIN_TRAIN = 15000
MIN_TEST = 1500
K = 8
C_FIXED = 0.1
BOOT_REPS = 3000
BOOT_SEED = 72022
EPS = 1e-8


def season_start(x) -> int | None:
    s = str(x)
    for token in s.replace("-", "/").split("/"):
        token = token.strip()
        if len(token) == 4 and token.isdigit():
            return int(token)
    digits = "".join(ch if ch.isdigit() else " " for ch in s).split()
    for d in digits:
        if len(d) >= 4:
            return int(d[:4])
    return None


def load_source() -> tuple[pd.DataFrame, dict]:
    frames = []
    sealed_2425_identity_rows = 0
    sealed_2425_goal_values_parsed = 0
    for path in FILES:
        raw = urlopen(RAW + path, timeout=90).read()
        ident = pd.read_csv(io.BytesIO(raw), usecols=ID_ODDS)
        ident["source_file"] = path
        ident["_row_pos"] = np.arange(len(ident), dtype=int)
        ident["season_start"] = ident["Season"].map(season_start)
        ident["date"] = pd.to_datetime(ident["Date"], errors="coerce", utc=True)
        if ident["season_start"].isna().any() or ident["date"].isna().any():
            raise RuntimeError(f"season/date parse failure {path}")
        ident["season_start"] = ident["season_start"].astype(int)
        sealed_2425_identity_rows += int((ident.season_start == 2024).sum())

        allowed = ident.index[ident.season_start <= 2023].to_numpy(int)
        allowed_set = set(int(x) for x in allowed)
        # Goal columns are parsed only for rows through 2023/24. 2024/25 rows are skipped before label projection.
        lab = pd.read_csv(
            io.BytesIO(raw),
            usecols=["FTHG", "FTAG"],
            skiprows=lambda line_no: line_no > 0 and (line_no - 1) not in allowed_set,
        )
        if len(lab) != len(allowed):
            raise RuntimeError(f"label projection length drift {path}: {len(lab)} != {len(allowed)}")
        dev = ident.loc[allowed].copy().reset_index(drop=True)
        dev["FTHG"] = pd.to_numeric(lab["FTHG"], errors="coerce").to_numpy()
        dev["FTAG"] = pd.to_numeric(lab["FTAG"], errors="coerce").to_numpy()
        dev = dev.dropna(subset=["FTHG", "FTAG"]).copy()
        dev["FTHG"] = dev.FTHG.astype(int)
        dev["FTAG"] = dev.FTAG.astype(int)
        frames.append(dev)

    frame = pd.concat(frames, ignore_index=True)
    if (frame.season_start >= 2024).any():
        raise RuntimeError("sealed 2024/25 label horizon breach")
    frame = frame.sort_values(["date", "source_file", "HomeTeam", "AwayTeam"]).reset_index(drop=True)
    return frame, {
        "sealed_2024_25_identity_rows": sealed_2425_identity_rows,
        "sealed_2024_25_goal_values_parsed": sealed_2425_goal_values_parsed,
        "development_goal_rows": int(len(frame)),
    }


def mean_sd(vals):
    if not vals:
        return np.nan, np.nan
    a = np.asarray(vals, float)
    return float(a.mean()), float(a.std(ddof=0))


def profile(h):
    gf_mu, gf_sd = mean_sd(h["gf"])
    ga_mu, ga_sd = mean_sd(h["ga"])
    return {
        "n": len(h["gf"]),
        "goals_for_mean": gf_mu,
        "goals_for_sd": gf_sd,
        "goals_against_mean": ga_mu,
        "goals_against_sd": ga_sd,
    }


def hist_factory():
    return {"gf": [], "ga": []}


def safe_logit(p):
    p = np.clip(np.asarray(p, float), EPS, 1.0 - EPS)
    return np.log(p / (1.0 - p))


def build_rows(frame: pd.DataFrame) -> pd.DataFrame:
    team_hist = defaultdict(hist_factory)
    comp_tot = defaultdict(list)
    rows = []

    for date, g in frame.groupby("date", sort=True):
        # Predict every match on the same UTC date before any same-date result update.
        for _, r in g.iterrows():
            comp = str(r.source_file)
            hkey = (comp, str(r.HomeTeam))
            akey = (comp, str(r.AwayTeam))
            hp = profile(team_hist[hkey])
            ap = profile(team_hist[akey])
            cm, cs = mean_sd(comp_tot[comp])

            oo = float(r["over_2.5_open"]) if pd.notna(r["over_2.5_open"]) else np.nan
            uo = float(r["under_2.5_open"]) if pd.notna(r["under_2.5_open"]) else np.nan
            oc = float(r["over_2.5_close"]) if pd.notna(r["over_2.5_close"]) else np.nan
            uc = float(r["under_2.5_close"]) if pd.notna(r["under_2.5_close"]) else np.nan
            if all(np.isfinite([oo, uo])) and oo > 1 and uo > 1:
                po = (1.0 / oo) / ((1.0 / oo) + (1.0 / uo))
                open_logit = float(safe_logit([po])[0])
            else:
                open_logit = np.nan
            if all(np.isfinite([oc, uc])) and oc > 1 and uc > 1:
                pc = (1.0 / oc) / ((1.0 / oc) + (1.0 / uc))
                close_logit = float(safe_logit([pc])[0])
            else:
                close_logit = np.nan

            row = {
                "date": r.date,
                "season_start": int(r.season_start),
                "source_file": comp,
                "home_team": str(r.HomeTeam),
                "away_team": str(r.AwayTeam),
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
                "log1p_home_result_history_n": math.log1p(hp["n"]),
                "log1p_away_result_history_n": math.log1p(ap["n"]),
                "open_logit": open_logit,
                "movement_logit": close_logit - open_logit if np.isfinite(close_logit) and np.isfinite(open_logit) else np.nan,
                "eligible": hp["n"] >= MIN_HISTORY and ap["n"] >= MIN_HISTORY,
                "target": min(int(r.FTHG) + int(r.FTAG), 7),
            }
            rows.append(row)

        for _, r in g.iterrows():
            comp = str(r.source_file)
            hkey = (comp, str(r.HomeTeam))
            akey = (comp, str(r.AwayTeam))
            hg, ag = int(r.FTHG), int(r.FTAG)
            team_hist[hkey]["gf"].append(float(hg)); team_hist[hkey]["ga"].append(float(ag))
            team_hist[akey]["gf"].append(float(ag)); team_hist[akey]["ga"].append(float(hg))
            comp_tot[comp].append(float(hg + ag))

    return pd.DataFrame(rows).sort_values(["date", "source_file", "home_team", "away_team"]).reset_index(drop=True)


def pipeline():
    return make_pipeline(
        SimpleImputer(strategy="median"),
        StandardScaler(),
        LogisticRegression(C=C_FIXED, solver="lbfgs", max_iter=3000, class_weight=None, random_state=0),
    )


def predict8(model, X):
    p = model.predict_proba(X)
    cls = model.named_steps["logisticregression"].classes_.astype(int)
    out = np.zeros((len(X), K), dtype=float)
    out[:, cls] = p
    out = np.clip(out, 1e-15, 1.0)
    return out / out.sum(axis=1, keepdims=True)


def top_ece(y, p, bins=10):
    conf = p.max(axis=1)
    pred = p.argmax(axis=1)
    ok = (pred == y).astype(float)
    ece = 0.0
    edges = np.linspace(0, 1, bins + 1)
    for i in range(bins):
        m = (conf >= edges[i]) & (conf < edges[i+1] if i < bins-1 else conf <= edges[i+1])
        if m.any():
            ece += float(m.mean()) * abs(float(ok[m].mean()) - float(conf[m].mean()))
    return float(ece)


def metrics(y, p):
    y = np.asarray(y, int)
    one = np.eye(K)[y]
    cp = np.cumsum(p, axis=1)[:, :-1]
    cy = np.cumsum(one, axis=1)[:, :-1]
    rank = np.argsort(-p, axis=1)
    top3 = np.mean([int(y[i] in rank[i, :3]) for i in range(len(y))])
    def auc(th):
        yy = (y >= th).astype(int)
        return float(roc_auc_score(yy, p[:, th:].sum(axis=1))) if len(np.unique(yy)) == 2 else None
    return {
        "n": int(len(y)),
        "log_loss": float(log_loss(y, p, labels=list(range(K)))),
        "brier": float(np.mean(np.sum((p-one)**2, axis=1))),
        "rps": float(np.mean(np.sum((cp-cy)**2, axis=1)/(K-1))),
        "top1": float(np.mean(p.argmax(axis=1) == y)),
        "top3": float(top3),
        "auc_t_ge_3": auc(3),
        "auc_t_ge_4": auc(4),
        "top_ece": top_ece(y, p),
    }


def delta(a, b):
    out = {}
    for k in ["log_loss", "brier", "rps", "top1", "top3", "auc_t_ge_3", "auc_t_ge_4", "top_ece"]:
        out[k] = None if a[k] is None or b[k] is None else float(a[k] - b[k])
    return out


def bootstrap(y, pref, pcand):
    y = np.asarray(y, int)
    idx = np.arange(len(y))
    d = -np.log(np.clip(pcand[idx, y], 1e-15, 1.0)) + np.log(np.clip(pref[idx, y], 1e-15, 1.0))
    rng = np.random.default_rng(BOOT_SEED)
    sims = np.empty(BOOT_REPS, dtype=float)
    n = len(d)
    for i in range(BOOT_REPS):
        take = rng.integers(0, n, size=n)
        sims[i] = float(d[take].mean())
    return {
        "n": int(n), "reps": BOOT_REPS, "seed": BOOT_SEED,
        "mean_delta": float(d.mean()),
        "ci90_low": float(np.quantile(sims, .05)),
        "ci90_high": float(np.quantile(sims, .95)),
        "p_delta_lt_zero": float(np.mean(sims < 0)),
    }


def main():
    frame, boundary = load_source()
    feat = build_rows(frame)
    elig = feat[feat.eligible].copy()

    folds = {}
    pooled_y, pooled_base, pooled_ref, pooled_cand = [], [], [], []
    wins = 0
    coverage_pass = True
    max_prob_resid = 0.0

    for season in TEST_SEASONS:
        tr = elig[elig.season_start < season].copy()
        te = elig[elig.season_start == season].copy()
        classes = sorted(tr.target.unique().astype(int).tolist())
        leagues = int(te.source_file.nunique())
        cov = len(tr) >= MIN_TRAIN and len(te) >= MIN_TEST and classes == list(range(K)) and leagues >= 6
        coverage_pass &= cov
        if not cov:
            folds[str(season)] = {"coverage_pass": False, "train_rows": int(len(tr)), "test_rows": int(len(te)), "train_classes": classes, "test_leagues": leagues}
            continue

        ytr = tr.target.to_numpy(int); yte = te.target.to_numpy(int)
        mb = pipeline(); mr = pipeline(); mc = pipeline()
        mb.fit(tr[BASE], ytr); mr.fit(tr[REF], ytr); mc.fit(tr[CAND], ytr)
        pb = predict8(mb, te[BASE]); pr = predict8(mr, te[REF]); pc = predict8(mc, te[CAND])
        max_prob_resid = max(max_prob_resid, float(np.max(np.abs(pb.sum(1)-1))), float(np.max(np.abs(pr.sum(1)-1))), float(np.max(np.abs(pc.sum(1)-1))))
        xb, xr, xc = metrics(yte, pb), metrics(yte, pr), metrics(yte, pc)
        dcr = delta(xc, xr); drb = delta(xr, xb)
        wins += int(dcr["log_loss"] < 0)
        folds[str(season)] = {
            "coverage_pass": True, "train_rows": int(len(tr)), "test_rows": int(len(te)), "test_leagues": leagues,
            "score_history": xb, "reference_opening": xr, "candidate_opening_plus_movement": xc,
            "candidate_minus_reference": dcr, "reference_minus_score_history": drb,
        }
        pooled_y.append(yte); pooled_base.append(pb); pooled_ref.append(pr); pooled_cand.append(pc)

    pooled_n = int(sum(len(x) for x in pooled_y))
    coverage_pass &= pooled_n >= 8000 and len(pooled_y) == 5
    if not coverage_pass:
        result = {
            "schema": SCHEMA, "terminal": "STOP_COVERAGE", "coverage_pass": False,
            "folds": folds, "pooled_test_rows": pooled_n,
            "boundary": boundary | {"formal_weight": 0, "C073_C077_quarantined": True, "C070F_confirmation1597_opened": False},
        }
    else:
        y = np.concatenate(pooled_y); pb = np.vstack(pooled_base); pr = np.vstack(pooled_ref); pc = np.vstack(pooled_cand)
        xb, xr, xc = metrics(y, pb), metrics(y, pr), metrics(y, pc)
        dcr, drb = delta(xc, xr), delta(xr, xb)
        bt = bootstrap(y, pr, pc)
        gate = bool(
            dcr["log_loss"] < 0 and bt["ci90_high"] < 0 and dcr["brier"] <= 0 and dcr["rps"] <= 0
            and wins >= 4 and max_prob_resid <= 1e-10
        )
        result = {
            "schema": SCHEMA,
            "terminal": "C072E2_MOVEMENT_DEVELOPMENT_PASS" if gate else "C072E2_MOVEMENT_INCREMENT_NOT_ESTABLISHED",
            "coverage_pass": True,
            "source": {"repo": "nm2890/football-data", "revision": REV, "pit_classification": "COARSE_OPEN_CLOSE_SEMANTICS_ONLY_NO_IMMUTABLE_QUOTE_TIMESTAMPS"},
            "features": {"base": BASE, "reference": REF, "candidate": CAND, "C": C_FIXED, "feature_search": False, "transform_search": False},
            "folds": folds,
            "pooled": {
                "test_rows": int(len(y)),
                "score_history": xb,
                "reference_opening": xr,
                "candidate_opening_plus_movement": xc,
                "candidate_minus_reference": dcr,
                "reference_minus_score_history": drb,
                "fold_logloss_wins_movement": int(wins),
                "bootstrap_candidate_minus_reference": bt,
                "max_probability_sum_residual": max_prob_resid,
            },
            "development_gate": gate,
            "stopping_rule": "if FAIL, no C/transform/other-market/fold/league-subset/interaction/manual score boost repair on these labels",
            "boundary": boundary | {
                "formal_weight": 0,
                "C073_C077_quarantined": True,
                "C070F_confirmation1597_opened": False,
                "protected_opened": False,
                "sealed_2024_25_goal_values_parsed": 0,
                "confirmation_claim_allowed": False,
            },
        }

    out = Path("football-data/research/c072e2_summary.json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
