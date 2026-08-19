#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import digamma, gammaln
from scipy.stats import nbinom, poisson
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

SOURCE_REVISION = "279978313f9c16a210fa80e8986fa22f0f866fba"
TEST_SEASONS = ["2019-2020", "2020-2021", "2021-2022", "2022-2023", "2023-2024"]
MIN_HISTORY = 8
BOOT_EXACT = 5000
BOOT_EXACT_SEED = 78001
BOOT_TAIL = 5000
BOOT_TAIL_SEED = 78002
BOOT_CAL = 5000
BOOT_CAL8_SEED = 78008
BOOT_CAL9_SEED = 78009
MAX_T = 60
FULL_RPS_THRESHOLDS = np.arange(0, 60, dtype=int)
TAIL_RPS_THRESHOLDS = np.arange(7, 60, dtype=int)
ALPHA_MIN = 1e-6
ALPHA_MAX = 10.0
ETA_MIN = -20.0
ETA_MAX = 20.0
TAIL_FOLD_MIN = 30

USECOLS = [
    "Date", "country", "league", "Season", "HomeTeam", "AwayTeam", "FTHG", "FTAG",
    "over_2.5_open", "under_2.5_open", "over_2.5_close", "under_2.5_close",
]
ODD_COLS = ["over_2.5_open", "under_2.5_open", "over_2.5_close", "under_2.5_close"]
BASE_FEATURES = [
    "competition_total_mean", "competition_total_sd",
    "home_goals_for_mean", "home_goals_for_sd", "home_goals_against_mean", "home_goals_against_sd",
    "away_goals_for_mean", "away_goals_for_sd", "away_goals_against_mean", "away_goals_against_sd",
    "log1p_home_result_history_n", "log1p_away_result_history_n",
]
FEATURES = BASE_FEATURES + ["movement_logit"]


def git(*args: str, cwd: Path) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


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
        "n": len(h["gf"]),
        "gf_mu": gf_mu, "gf_sd": gf_sd,
        "ga_mu": ga_mu, "ga_sd": ga_sd,
    }


def fresh_history():
    return {"gf": [], "ga": []}


def load_source(root: Path):
    actual = git("rev-parse", "HEAD", cwd=root)
    if actual != SOURCE_REVISION:
        raise RuntimeError(f"source revision drift: {actual} != {SOURCE_REVISION}")
    frames = []
    inventory = []
    for path in sorted(root.glob("data/**/*.csv")):
        try:
            d = pd.read_csv(path, usecols=USECOLS, low_memory=False)
        except ValueError:
            continue
        rel = str(path.relative_to(root))
        inventory.append({"path": rel, "sha256": file_sha256(path), "rows_raw": int(len(d))})
        d["source_file"] = rel
        frames.append(d)
    if not frames:
        raise RuntimeError("no source CSV with frozen columns")
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
    df["T_exact"] = (df["FTHG"] + df["FTAG"]).astype(int)
    if int(df["T_exact"].max()) > MAX_T:
        raise RuntimeError(f"realized exact total exceeds frozen evaluation grid: {int(df['T_exact'].max())}")
    p_open = devig_over(df["over_2.5_open"], df["under_2.5_open"])
    p_close = devig_over(df["over_2.5_close"], df["under_2.5_close"])
    df["movement_logit"] = logit_arr(p_close) - logit_arr(p_open)
    df["season_start"] = df["Season"].map(season_start)
    df = df.sort_values(["date", "league_key", "HomeTeam", "AwayTeam", "source_file"]).reset_index(drop=True)
    return df, inventory


def build_history_features(df: pd.DataFrame) -> pd.DataFrame:
    hist = defaultdict(fresh_history)
    comp_totals = defaultdict(list)
    rows = []
    for d, g in df.groupby("date", sort=True):
        pending = []
        for idx, r in g.iterrows():
            hp = team_profile(hist[r["home_key"]])
            ap = team_profile(hist[r["away_key"]])
            cm, cs = mean_sd(comp_totals[r["league_key"]])
            rows.append({
                "row_id": int(idx), "date": r["date"], "Season": str(r["Season"]),
                "season_start": int(r["season_start"]), "league_key": r["league_key"],
                "HomeTeam": r["HomeTeam"], "AwayTeam": r["AwayTeam"], "source_file": r["source_file"],
                "competition_total_mean": cm, "competition_total_sd": cs,
                "home_goals_for_mean": hp["gf_mu"], "home_goals_for_sd": hp["gf_sd"],
                "home_goals_against_mean": hp["ga_mu"], "home_goals_against_sd": hp["ga_sd"],
                "away_goals_for_mean": ap["gf_mu"], "away_goals_for_sd": ap["gf_sd"],
                "away_goals_against_mean": ap["ga_mu"], "away_goals_against_sd": ap["ga_sd"],
                "log1p_home_result_history_n": math.log1p(hp["n"]),
                "log1p_away_result_history_n": math.log1p(ap["n"]),
                "movement_logit": float(r["movement_logit"]),
                "T_exact": int(r["T_exact"]),
                "eligible": bool(hp["n"] >= MIN_HISTORY and ap["n"] >= MIN_HISTORY),
            })
            pending.append(r)
        # strict same-date predict-before-update
        for r in pending:
            hg = int(r["FTHG"]); ag = int(r["FTAG"])
            hh = hist[r["home_key"]]; ah = hist[r["away_key"]]
            hh["gf"].append(float(hg)); hh["ga"].append(float(ag))
            ah["gf"].append(float(ag)); ah["ga"].append(float(hg))
            comp_totals[r["league_key"]].append(float(hg + ag))
    return pd.DataFrame(rows).sort_values(["date", "league_key", "HomeTeam", "AwayTeam", "source_file"]).reset_index(drop=True)


def preprocess(train: pd.DataFrame, test: pd.DataFrame, features: list[str]):
    imp = SimpleImputer(strategy="median")
    scl = StandardScaler()
    xtr = imp.fit_transform(train[features])
    xtr = scl.fit_transform(xtr)
    xte = scl.transform(imp.transform(test[features]))
    xtr1 = np.column_stack([np.ones(len(xtr)), xtr])
    xte1 = np.column_stack([np.ones(len(xte)), xte])
    return xtr1, xte1


def poisson_objective(beta: np.ndarray, X: np.ndarray, y: np.ndarray):
    eta = np.clip(X @ beta, ETA_MIN, ETA_MAX)
    mu = np.exp(eta)
    nll = np.mean(mu - y * eta + gammaln(y + 1.0))
    grad = X.T @ (mu - y) / len(y)
    return float(nll), grad


def fit_poisson(X: np.ndarray, y: np.ndarray):
    init = np.zeros(X.shape[1], dtype=float)
    res = minimize(lambda b: poisson_objective(b, X, y), init, method="L-BFGS-B", jac=True,
                   options={"maxiter": 5000, "ftol": 1e-10, "gtol": 1e-10, "maxls": 50})
    return res


def nb2_objective(theta: np.ndarray, X: np.ndarray, y: np.ndarray):
    beta = theta[:-1]
    z = float(theta[-1])
    alpha = math.exp(z)
    r = 1.0 / alpha
    eta = np.clip(X @ beta, ETA_MIN, ETA_MAX)
    mu = np.exp(eta)
    logp = (
        gammaln(y + r) - gammaln(r) - gammaln(y + 1.0)
        + r * (math.log(r) - np.log(r + mu))
        + y * (eta - np.log(r + mu))
    )
    nll = -float(np.mean(logp))
    dlogp_deta = y - mu * (r + y) / (r + mu)
    grad_beta = -(X.T @ dlogp_deta) / len(y)
    dlogp_dr = digamma(y + r) - digamma(r) + math.log(r) + 1.0 - np.log(r + mu) - (r + y) / (r + mu)
    grad_z = float(np.mean(r * dlogp_dr))
    grad = np.concatenate([grad_beta, np.array([grad_z])])
    return nll, grad


def fit_nb2(X: np.ndarray, y: np.ndarray, poisson_beta: np.ndarray):
    init = np.concatenate([np.asarray(poisson_beta, dtype=float), np.array([math.log(0.2)])])
    bounds = [(None, None)] * len(poisson_beta) + [(math.log(ALPHA_MIN), math.log(ALPHA_MAX))]
    res = minimize(lambda th: nb2_objective(th, X, y), init, method="L-BFGS-B", jac=True, bounds=bounds,
                   options={"maxiter": 5000, "ftol": 1e-10, "gtol": 1e-10, "maxls": 50})
    return res


def fitted_mu(beta: np.ndarray, X: np.ndarray):
    return np.exp(np.clip(X @ beta, ETA_MIN, ETA_MAX))


def distribution_arrays(kind: str, params: np.ndarray, X: np.ndarray):
    ks = np.arange(MAX_T + 1, dtype=int)
    if kind == "poisson":
        beta = params
        mu = fitted_mu(beta, X)
        pmf = poisson.pmf(ks[None, :], mu[:, None])
        sf60 = poisson.sf(MAX_T, mu)
        sf6 = poisson.sf(6, mu)
        sf7 = poisson.sf(7, mu)
        sf8 = poisson.sf(8, mu)
        alpha = None
    elif kind == "nb2":
        beta = params[:-1]
        alpha = math.exp(float(params[-1]))
        r = 1.0 / alpha
        mu = fitted_mu(beta, X)
        prob = r / (r + mu)
        pmf = nbinom.pmf(ks[None, :], r, prob[:, None])
        sf60 = nbinom.sf(MAX_T, r, prob)
        sf6 = nbinom.sf(6, r, prob)
        sf7 = nbinom.sf(7, r, prob)
        sf8 = nbinom.sf(8, r, prob)
    else:
        raise ValueError(kind)
    conservation = np.abs(pmf.sum(axis=1) + sf60 - 1.0)
    if not np.isfinite(pmf).all() or not np.isfinite(sf60).all() or np.any(pmf < 0) or np.any(sf60 < 0):
        raise RuntimeError(f"invalid {kind} probability")
    return {"pmf": pmf, "sf60": sf60, "sf6": sf6, "sf7": sf7, "sf8": sf8,
            "conservation": conservation, "mu": mu, "alpha": alpha}


def exact_row_metrics(y: np.ndarray, dist: dict):
    y = np.asarray(y, dtype=int)
    pmf = dist["pmf"]
    if np.any(y > MAX_T):
        raise RuntimeError("realized T exceeds evaluation grid")
    p_obs = np.clip(pmf[np.arange(len(y)), y], 1e-300, 1.0)
    ll = -np.log(p_obs)
    one = np.zeros_like(pmf)
    one[np.arange(len(y)), y] = 1.0
    brier = np.square(pmf - one).sum(axis=1)
    cdf = np.cumsum(pmf, axis=1)
    truth = (y[:, None] <= FULL_RPS_THRESHOLDS[None, :]).astype(float)
    rps = np.square(cdf[:, FULL_RPS_THRESHOLDS] - truth).mean(axis=1)
    top1 = (np.argmax(pmf, axis=1) == y).astype(float)
    return {"ll": ll, "brier": brier, "rps": rps, "top1": top1}


def tail_row_metrics(y: np.ndarray, dist: dict):
    y = np.asarray(y, dtype=int)
    mask = y >= 7
    yy = y[mask]
    pmf = dist["pmf"][mask]
    sf6 = np.clip(dist["sf6"][mask], 1e-300, 1.0)
    q = pmf[:, 7:] / sf6[:, None]
    q_obs = np.clip(q[np.arange(len(yy)), yy - 7], 1e-300, 1.0)
    ll = -np.log(q_obs)
    one = np.zeros_like(q)
    one[np.arange(len(yy)), yy - 7] = 1.0
    brier = np.square(q - one).sum(axis=1)
    cq = np.cumsum(q, axis=1)
    # thresholds 7..59 correspond q cumulative indices 0..52
    truth = (yy[:, None] <= TAIL_RPS_THRESHOLDS[None, :]).astype(float)
    rps = np.square(cq[:, :len(TAIL_RPS_THRESHOLDS)] - truth).mean(axis=1)
    return {"mask": mask, "ll": ll, "brier": brier, "rps": rps,
            "cond_residual_beyond60": dist["sf60"][mask] / sf6,
            "p8_cond": dist["sf7"][mask] / sf6,
            "p9_cond": dist["sf8"][mask] / sf6}


def collapsed8(dist: dict):
    p = np.column_stack([dist["pmf"][:, :7], dist["sf6"]])
    p = np.clip(p, 1e-300, 1.0)
    p /= p.sum(axis=1, keepdims=True)
    return p


def categorical_pipeline():
    return make_pipeline(
        SimpleImputer(strategy="median"),
        StandardScaler(),
        LogisticRegression(C=0.1, max_iter=3000, class_weight=None, random_state=0, solver="lbfgs"),
    )


def aligned_cat_proba(model, X: pd.DataFrame):
    raw = model.predict_proba(X)
    cls = model.named_steps["logisticregression"].classes_.astype(int)
    out = np.zeros((len(X), 8), dtype=float)
    out[:, cls] = raw
    out = np.clip(out, 1e-300, 1.0)
    out /= out.sum(axis=1, keepdims=True)
    return out


def cat_metrics(y8: np.ndarray, p: np.ndarray):
    y8 = np.asarray(y8, dtype=int)
    one = np.eye(8)[y8]
    ll = -np.log(np.clip(p[np.arange(len(y8)), y8], 1e-300, 1.0))
    brier = np.square(p - one).sum(axis=1)
    cp = np.cumsum(p, axis=1)[:, :-1]
    cy = np.cumsum(one, axis=1)[:, :-1]
    rps = np.square(cp - cy).mean(axis=1)
    return {"ll": ll, "brier": brier, "rps": rps}


def summarize(metrics: dict):
    return {k: float(np.mean(v)) for k, v in metrics.items() if k not in {"mask", "cond_residual_beyond60", "p8_cond", "p9_cond"}}


def paired_bootstrap(delta: np.ndarray, reps: int, seed: int):
    d = np.asarray(delta, dtype=float)
    rng = np.random.default_rng(seed)
    sims = np.empty(reps, dtype=float)
    for i in range(reps):
        idx = rng.integers(0, len(d), size=len(d))
        sims[i] = float(d[idx].mean())
    return {"n": int(len(d)), "reps": int(reps), "seed": int(seed), "mean_delta": float(d.mean()),
            "ci90_low": float(np.quantile(sims, 0.05)), "ci90_high": float(np.quantile(sims, 0.95)),
            "p_delta_lt_zero": float(np.mean(sims < 0))}


def residual_bootstrap(residual: np.ndarray, seed: int):
    r = np.asarray(residual, dtype=float)
    rng = np.random.default_rng(seed)
    sims = np.empty(BOOT_CAL, dtype=float)
    for i in range(BOOT_CAL):
        idx = rng.integers(0, len(r), size=len(r))
        sims[i] = float(r[idx].mean())
    return {"n": int(len(r)), "mean_residual": float(r.mean()), "reps": BOOT_CAL, "seed": int(seed),
            "ci90_low": float(np.quantile(sims, 0.05)), "ci90_high": float(np.quantile(sims, 0.95)),
            "contains_zero": bool(float(np.quantile(sims, 0.05)) <= 0 <= float(np.quantile(sims, 0.95)))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", required=True)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    out = Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)

    df, inventory = load_source(Path(a.source_root))
    feat = build_history_features(df)
    eligible = feat[feat["eligible"]].copy().reset_index(drop=True)

    fold_reports = []
    pooled_rows = []
    optimizer_all_success = True
    alpha_bound_ok = True
    max_conservation = 0.0
    max_residual60 = 0.0
    max_cond_tail_residual60 = 0.0

    for test_season in TEST_SEASONS:
        sy = season_start(test_season)
        train = eligible[eligible["season_start"] < sy].copy().reset_index(drop=True)
        test = eligible[eligible["Season"] == test_season].copy().reset_index(drop=True)
        if len(train) < 1000 or len(test) < 300:
            raise RuntimeError(f"fold coverage too small {test_season}: train={len(train)} test={len(test)}")

        Xtr, Xte = preprocess(train, test, FEATURES)
        ytr = train["T_exact"].to_numpy(int)
        yte = test["T_exact"].to_numpy(int)

        pres = fit_poisson(Xtr, ytr)
        nres = fit_nb2(Xtr, ytr, pres.x)
        p_success = bool(pres.success)
        n_success = bool(nres.success)
        optimizer_all_success &= p_success and n_success
        alpha = math.exp(float(nres.x[-1]))
        this_alpha_bound_ok = bool(alpha > 1.0001e-6 and alpha < 9.999)
        alpha_bound_ok &= this_alpha_bound_ok

        pdist = distribution_arrays("poisson", pres.x, Xte)
        ndist = distribution_arrays("nb2", nres.x, Xte)
        max_conservation = max(max_conservation, float(np.max(pdist["conservation"])), float(np.max(ndist["conservation"])))
        max_residual60 = max(max_residual60, float(np.max(pdist["sf60"])), float(np.max(ndist["sf60"])))

        pm = exact_row_metrics(yte, pdist)
        nm = exact_row_metrics(yte, ndist)
        pt = tail_row_metrics(yte, pdist)
        nt = tail_row_metrics(yte, ndist)
        if len(nt["ll"]):
            max_cond_tail_residual60 = max(max_cond_tail_residual60, float(np.max(pt["cond_residual_beyond60"])), float(np.max(nt["cond_residual_beyond60"])))

        y8tr = np.minimum(ytr, 7)
        y8te = np.minimum(yte, 7)
        cat_base = categorical_pipeline(); cat_move = categorical_pipeline()
        cat_base.fit(train[BASE_FEATURES], y8tr)
        cat_move.fit(train[FEATURES], y8tr)
        cbp = aligned_cat_proba(cat_base, test[BASE_FEATURES])
        cmp = aligned_cat_proba(cat_move, test[FEATURES])
        nb8 = collapsed8(ndist)
        pois8 = collapsed8(pdist)
        cbm = cat_metrics(y8te, cbp); cmm = cat_metrics(y8te, cmp)
        nb8m = cat_metrics(y8te, nb8); pois8m = cat_metrics(y8te, pois8)

        exact_delta_ll = nm["ll"] - pm["ll"]
        tail_delta_ll = nt["ll"] - pt["ll"]
        fold = {
            "test_season": test_season,
            "train_n": int(len(train)), "test_n": int(len(test)), "tail_n": int(np.sum(yte >= 7)),
            "poisson_optimizer": {"success": p_success, "status": int(pres.status), "message": str(pres.message), "nit": int(pres.nit), "fun": float(pres.fun)},
            "nb2_optimizer": {"success": n_success, "status": int(nres.status), "message": str(nres.message), "nit": int(nres.nit), "fun": float(nres.fun), "alpha": float(alpha), "alpha_bound_ok": this_alpha_bound_ok},
            "poisson_exact": summarize(pm), "nb2_exact": summarize(nm),
            "exact_delta": {"logloss": float(np.mean(nm["ll"] - pm["ll"])), "brier": float(np.mean(nm["brier"] - pm["brier"])), "rps": float(np.mean(nm["rps"] - pm["rps"])), "top1": float(np.mean(nm["top1"] - pm["top1"]))},
            "poisson_tail": summarize(pt), "nb2_tail": summarize(nt),
            "tail_delta": {"logloss": float(np.mean(tail_delta_ll)) if len(tail_delta_ll) else None,
                           "brier": float(np.mean(nt["brier"] - pt["brier"])) if len(tail_delta_ll) else None,
                           "rps": float(np.mean(nt["rps"] - pt["rps"])) if len(tail_delta_ll) else None},
            "collapsed8": {
                "categorical_scorehistory": summarize(cbm),
                "categorical_movement": summarize(cmm),
                "poisson_fullsupport_collapsed": summarize(pois8m),
                "nb2_fullsupport_collapsed": summarize(nb8m),
            },
        }
        fold_reports.append(fold)

        tail_positions = np.flatnonzero(yte >= 7)
        for i in range(len(test)):
            row = {
                "test_season": test_season, "date": str(test.iloc[i]["date"].date()), "league_key": str(test.iloc[i]["league_key"]),
                "HomeTeam": str(test.iloc[i]["HomeTeam"]), "AwayTeam": str(test.iloc[i]["AwayTeam"]), "T_exact": int(yte[i]),
                "poisson_exact_ll": float(pm["ll"][i]), "nb2_exact_ll": float(nm["ll"][i]),
                "poisson_exact_brier": float(pm["brier"][i]), "nb2_exact_brier": float(nm["brier"][i]),
                "poisson_exact_rps": float(pm["rps"][i]), "nb2_exact_rps": float(nm["rps"][i]),
                "poisson_exact_top1": float(pm["top1"][i]), "nb2_exact_top1": float(nm["top1"][i]),
                "cat_scorehistory_ll": float(cbm["ll"][i]), "cat_movement_ll": float(cmm["ll"][i]),
                "poisson_collapsed_ll": float(pois8m["ll"][i]), "nb2_collapsed_ll": float(nb8m["ll"][i]),
                "cat_scorehistory_brier": float(cbm["brier"][i]), "cat_movement_brier": float(cmm["brier"][i]),
                "poisson_collapsed_brier": float(pois8m["brier"][i]), "nb2_collapsed_brier": float(nb8m["brier"][i]),
                "cat_scorehistory_rps": float(cbm["rps"][i]), "cat_movement_rps": float(cmm["rps"][i]),
                "poisson_collapsed_rps": float(pois8m["rps"][i]), "nb2_collapsed_rps": float(nb8m["rps"][i]),
                "is_tail": bool(yte[i] >= 7),
                "poisson_tail_ll": None, "nb2_tail_ll": None, "poisson_tail_brier": None, "nb2_tail_brier": None,
                "poisson_tail_rps": None, "nb2_tail_rps": None, "nb2_p8_cond": None, "nb2_p9_cond": None,
            }
            if yte[i] >= 7:
                j = int(np.where(tail_positions == i)[0][0])
                row.update({
                    "poisson_tail_ll": float(pt["ll"][j]), "nb2_tail_ll": float(nt["ll"][j]),
                    "poisson_tail_brier": float(pt["brier"][j]), "nb2_tail_brier": float(nt["brier"][j]),
                    "poisson_tail_rps": float(pt["rps"][j]), "nb2_tail_rps": float(nt["rps"][j]),
                    "nb2_p8_cond": float(nt["p8_cond"][j]), "nb2_p9_cond": float(nt["p9_cond"][j]),
                })
            pooled_rows.append(row)

    rows = pd.DataFrame(pooled_rows)
    rows.to_csv(out / "row_metrics.csv", index=False)
    tail = rows[rows["is_tail"]].copy()

    exact = {
        "n": int(len(rows)),
        "poisson": {"logloss": float(rows.poisson_exact_ll.mean()), "brier": float(rows.poisson_exact_brier.mean()), "rps": float(rows.poisson_exact_rps.mean()), "top1": float(rows.poisson_exact_top1.mean())},
        "nb2": {"logloss": float(rows.nb2_exact_ll.mean()), "brier": float(rows.nb2_exact_brier.mean()), "rps": float(rows.nb2_exact_rps.mean()), "top1": float(rows.nb2_exact_top1.mean())},
    }
    exact["delta"] = {k: float(exact["nb2"][k] - exact["poisson"][k]) for k in exact["poisson"]}
    exact_boot = paired_bootstrap((rows.nb2_exact_ll - rows.poisson_exact_ll).to_numpy(float), BOOT_EXACT, BOOT_EXACT_SEED)

    tail_summary = {
        "n": int(len(tail)),
        "poisson": {"logloss": float(tail.poisson_tail_ll.mean()), "brier": float(tail.poisson_tail_brier.mean()), "rps": float(tail.poisson_tail_rps.mean())},
        "nb2": {"logloss": float(tail.nb2_tail_ll.mean()), "brier": float(tail.nb2_tail_brier.mean()), "rps": float(tail.nb2_tail_rps.mean())},
    }
    tail_summary["delta"] = {k: float(tail_summary["nb2"][k] - tail_summary["poisson"][k]) for k in tail_summary["poisson"]}
    tail_boot = paired_bootstrap((tail.nb2_tail_ll - tail.poisson_tail_ll).to_numpy(float), BOOT_TAIL, BOOT_TAIL_SEED)

    cal8 = residual_bootstrap((tail.nb2_p8_cond - (tail.T_exact >= 8).astype(float)).to_numpy(float), BOOT_CAL8_SEED)
    cal9 = residual_bootstrap((tail.nb2_p9_cond - (tail.T_exact >= 9).astype(float)).to_numpy(float), BOOT_CAL9_SEED)

    collapsed = {
        "n": int(len(rows)),
        "categorical_scorehistory": {"logloss": float(rows.cat_scorehistory_ll.mean()), "brier": float(rows.cat_scorehistory_brier.mean()), "rps": float(rows.cat_scorehistory_rps.mean())},
        "categorical_movement": {"logloss": float(rows.cat_movement_ll.mean()), "brier": float(rows.cat_movement_brier.mean()), "rps": float(rows.cat_movement_rps.mean())},
        "poisson": {"logloss": float(rows.poisson_collapsed_ll.mean()), "brier": float(rows.poisson_collapsed_brier.mean()), "rps": float(rows.poisson_collapsed_rps.mean())},
        "nb2": {"logloss": float(rows.nb2_collapsed_ll.mean()), "brier": float(rows.nb2_collapsed_brier.mean()), "rps": float(rows.nb2_collapsed_rps.mean())},
    }

    exact_fold_wins = sum(float(f["exact_delta"]["logloss"]) < 0 for f in fold_reports)
    tail_eligible = [f for f in fold_reports if int(f["tail_n"]) >= TAIL_FOLD_MIN]
    tail_fold_wins = sum(float(f["tail_delta"]["logloss"]) < 0 for f in tail_eligible)

    gates = {
        "exact_pooled_dll_lt0": exact["delta"]["logloss"] < 0,
        "exact_boot90_upper_lt0": exact_boot["ci90_high"] < 0,
        "exact_fold_wins_ge4of5": exact_fold_wins >= 4,
        "exact_brier_nonworse": exact["delta"]["brier"] <= 0,
        "exact_rps_nonworse": exact["delta"]["rps"] <= 0,
        "tail_pooled_dll_lt0": tail_summary["delta"]["logloss"] < 0,
        "tail_boot90_upper_lt0": tail_boot["ci90_high"] < 0,
        "tail_brier_nonworse": tail_summary["delta"]["brier"] <= 0,
        "tail_rps_nonworse": tail_summary["delta"]["rps"] <= 0,
        "tail_eligible_folds_ge3": len(tail_eligible) >= 3,
        "tail_strict_majority_fold_wins": len(tail_eligible) >= 3 and tail_fold_wins > len(tail_eligible) / 2,
        "tail_8plus_calibration_ci_contains0": bool(cal8["contains_zero"]),
        "tail_9plus_calibration_ci_contains0": bool(cal9["contains_zero"]),
        "collapsed_nb2_beats_categorical_scorehistory_ll": collapsed["nb2"]["logloss"] < collapsed["categorical_scorehistory"]["logloss"],
        "probability_conservation": max_conservation <= 1e-10,
        "residual_beyond60": max_residual60 <= 1e-8,
        "all_optimizers_success": bool(optimizer_all_success),
        "all_nb2_alpha_not_at_bound": bool(alpha_bound_ok),
    }
    status = "FULL_SUPPORT_DIRECTT_DEVELOPMENT_PASS" if all(gates.values()) else "FAIL_PARK"

    summary = {
        "schema_version": "C078A_FULL_SUPPORT_DIRECTT_NB2_V1",
        "status": status,
        "source_revision": SOURCE_REVISION,
        "source_inventory": inventory,
        "valid_source_rows": int(len(df)), "eligible_rows": int(len(eligible)),
        "test_seasons": TEST_SEASONS,
        "feature_count": len(FEATURES), "features": FEATURES,
        "models": {"baseline": "Poisson log-link full support", "candidate": "NB2 log-link full support + one global alpha"},
        "folds": fold_reports,
        "pooled_exact": exact,
        "exact_bootstrap": exact_boot,
        "pooled_tail": tail_summary,
        "tail_bootstrap": tail_boot,
        "tail_calibration": {"8plus": cal8, "9plus": cal9},
        "collapsed8": collapsed,
        "stability": {"exact_fold_wins": int(exact_fold_wins), "tail_eligible_fold_count": int(len(tail_eligible)), "tail_fold_wins": int(tail_fold_wins)},
        "numerical_audit": {"max_probability_conservation_abs_residual": float(max_conservation),
                            "max_full_residual_mass_beyond60": float(max_residual60),
                            "max_conditional_tail_residual_mass_beyond60": float(max_cond_tail_residual60)},
        "gates": gates,
        "formal_weight": 0,
        "C077B_labels_read": False,
        "C076D_4567_opened": False,
        "C071_reserve52180_opened": False,
        "C070F1597_opened": False,
        "A05_or_protected_opened": False,
        "exact_tail_formally_promoted": False,
        "unified_matrix_generated": False,
        "CURRENT_change": False,
        "no_rescue_after_view": True,
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
