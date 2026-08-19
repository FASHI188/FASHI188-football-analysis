#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
import math
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

BASE_URL = "https://www.football-data.co.uk/mmz4281"
DIVS = {
    "E0": "England Premier League",
    "SP1": "Spain La Liga",
    "I1": "Italy Serie A",
    "D1": "Germany Bundesliga",
    "F1": "France Ligue 1",
    "N1": "Netherlands Eredivisie",
    "B1": "Belgium First Division A",
}
HISTORY_START = 2009
TRAIN_START = 2019
TRAIN_END = 2024
TEST_START = 2025
MIN_HISTORY = 8
C_FIXED = 0.1
K = 8
CLASSES = np.arange(K, dtype=int)
BOOTSTRAPS = 5000
BOOTSTRAP_SEED = 20260819
BASE_FEATURES = [
    "competition_total_mean", "competition_total_sd",
    "home_goals_for_mean", "home_goals_for_sd", "home_goals_against_mean", "home_goals_against_sd",
    "away_goals_for_mean", "away_goals_for_sd", "away_goals_against_mean", "away_goals_against_sd",
    "log1p_home_result_history_n", "log1p_away_result_history_n",
]
CANDIDATE_FEATURES = BASE_FEATURES + ["movement_logit"]
SCORE_COLS = ["Div", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"]
ODDS_COLS = ["Avg>2.5", "Avg<2.5", "AvgC>2.5", "AvgC<2.5"]


def season_code(start_year: int) -> str:
    return f"{start_year % 100:02d}{(start_year + 1) % 100:02d}"


def fetch_bytes(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 C074G frozen research confirmation"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read(), int(getattr(resp, "status", 200))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None, 404
            if exc.code not in {429, 500, 502, 503, 504}:
                raise
        except Exception:
            if attempt == 3:
                raise
        time.sleep(1.5 * (attempt + 1))
    return None, 0


def load_all():
    frames = []
    audit = []
    for sy in range(HISTORY_START, TEST_START + 1):
        code = season_code(sy)
        need_odds = sy >= TRAIN_START
        for div, league in DIVS.items():
            url = f"{BASE_URL}/{code}/{div}.csv"
            raw, status = fetch_bytes(url)
            if raw is None:
                audit.append({"season_start": sy, "season_code": code, "div": div, "league": league, "url": url, "status": status, "rows": 0, "used": False, "reason": "not_available"})
                continue
            header = pd.read_csv(io.BytesIO(raw), nrows=0).columns.tolist()
            required = SCORE_COLS + (ODDS_COLS if need_odds else [])
            missing = [c for c in required if c not in header]
            if missing:
                audit.append({"season_start": sy, "season_code": code, "div": div, "league": league, "url": url, "status": status, "rows": 0, "used": False, "reason": f"missing_columns:{missing}"})
                continue
            # Only frozen identity/result columns and, post-2019/20, the four frozen O/U columns are materialized.
            d = pd.read_csv(io.BytesIO(raw), usecols=required, low_memory=False)
            d["season_start"] = sy
            d["season_code"] = code
            d["league_name"] = league
            for c in ["FTHG", "FTAG"] + (ODDS_COLS if need_odds else []):
                d[c] = pd.to_numeric(d[c], errors="coerce")
            d["date"] = pd.to_datetime(d["Date"], errors="coerce", dayfirst=True)
            d["league_key"] = d["Div"].astype(str)
            d["home_key"] = d["league_key"] + "|" + d["HomeTeam"].astype(str)
            d["away_key"] = d["league_key"] + "|" + d["AwayTeam"].astype(str)
            score_valid = d[["HomeTeam", "AwayTeam", "FTHG", "FTAG"]].notna().all(axis=1) & d["date"].notna() & (d[["FTHG", "FTAG"]] >= 0).all(axis=1)
            d = d.loc[score_valid].copy()
            if need_odds:
                odds_valid = d[ODDS_COLS].notna().all(axis=1) & (d[ODDS_COLS] > 1.0).all(axis=1)
                d["odds_valid"] = odds_valid
                p_open = devig_over(d["Avg>2.5"], d["Avg<2.5"])
                p_close = devig_over(d["AvgC>2.5"], d["AvgC<2.5"])
                d["movement_logit"] = logit_arr(p_close) - logit_arr(p_open)
            else:
                d["odds_valid"] = False
                d["movement_logit"] = np.nan
            d["target"] = np.minimum((d["FTHG"] + d["FTAG"]).astype(int), 7)
            frames.append(d[["season_start","season_code","date","league_key","league_name","HomeTeam","AwayTeam","home_key","away_key","FTHG","FTAG","odds_valid","movement_logit","target"]].copy())
            audit.append({"season_start": sy, "season_code": code, "div": div, "league": league, "url": url, "status": status, "rows": int(len(d)), "used": True, "need_odds": need_odds, "odds_valid_rows": int(d["odds_valid"].sum())})
    if not frames:
        raise RuntimeError("no historical source rows")
    all_df = pd.concat(frames, ignore_index=True)
    all_df = all_df.sort_values(["date","league_key","HomeTeam","AwayTeam"]).reset_index(drop=True)
    return all_df, audit


def devig_over(o, u):
    io_ = 1.0 / np.asarray(o, dtype=float)
    iu_ = 1.0 / np.asarray(u, dtype=float)
    return io_ / (io_ + iu_)


def logit_arr(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def mean_sd(vals):
    if not vals:
        return np.nan, np.nan
    a = np.asarray(vals, dtype=float)
    return float(a.mean()), float(a.std(ddof=0))


def profile(h):
    gf_mu, gf_sd = mean_sd(h["gf"])
    ga_mu, ga_sd = mean_sd(h["ga"])
    return {"n": len(h["gf"]), "gf_mu": gf_mu, "gf_sd": gf_sd, "ga_mu": ga_mu, "ga_sd": ga_sd}


def fresh_hist():
    return {"gf": [], "ga": []}


def build_history_features(df):
    hist = defaultdict(fresh_hist)
    comp_tot = defaultdict(list)
    rows = []
    for date, g in df.groupby("date", sort=True):
        pending = []
        for _, r in g.iterrows():
            hp = profile(hist[r.home_key]); ap = profile(hist[r.away_key])
            cm, cs = mean_sd(comp_tot[r.league_key])
            rows.append({
                "date": r.date, "season_start": int(r.season_start), "season_code": r.season_code,
                "league_key": r.league_key, "league_name": r.league_name,
                "HomeTeam": r.HomeTeam, "AwayTeam": r.AwayTeam,
                "competition_total_mean": cm, "competition_total_sd": cs,
                "home_goals_for_mean": hp["gf_mu"], "home_goals_for_sd": hp["gf_sd"],
                "home_goals_against_mean": hp["ga_mu"], "home_goals_against_sd": hp["ga_sd"],
                "away_goals_for_mean": ap["gf_mu"], "away_goals_for_sd": ap["gf_sd"],
                "away_goals_against_mean": ap["ga_mu"], "away_goals_against_sd": ap["ga_sd"],
                "log1p_home_result_history_n": math.log1p(hp["n"]),
                "log1p_away_result_history_n": math.log1p(ap["n"]),
                "movement_logit": float(r.movement_logit) if pd.notna(r.movement_logit) else np.nan,
                "odds_valid": bool(r.odds_valid), "target": int(r.target),
                "eligible_history": hp["n"] >= MIN_HISTORY and ap["n"] >= MIN_HISTORY,
            })
            pending.append(r)
        # No same-calendar-date result can enter another same-date feature.
        for r in pending:
            hg = float(r.FTHG); ag = float(r.FTAG)
            hh = hist[r.home_key]; ah = hist[r.away_key]
            hh["gf"].append(hg); hh["ga"].append(ag)
            ah["gf"].append(ag); ah["ga"].append(hg)
            comp_tot[r.league_key].append(hg + ag)
    return pd.DataFrame(rows).sort_values(["date","league_key","HomeTeam","AwayTeam"]).reset_index(drop=True)


def pipeline():
    return make_pipeline(
        SimpleImputer(strategy="median"),
        StandardScaler(),
        LogisticRegression(C=C_FIXED, max_iter=3000, class_weight=None, random_state=0, solver="lbfgs", multi_class="multinomial"),
    )


def aligned_proba(model, X):
    raw = model.predict_proba(X)
    cls = model.named_steps["logisticregression"].classes_.astype(int)
    out = np.zeros((len(X), K), dtype=float)
    out[:, cls] = raw
    out = np.clip(out, 1e-15, 1.0)
    out /= out.sum(axis=1, keepdims=True)
    return out


def row_metrics(y, p):
    y = np.asarray(y, dtype=int)
    one = np.eye(K)[y]
    idx = np.arange(len(y))
    ll = -np.log(np.clip(p[idx, y], 1e-15, 1.0))
    brier = np.square(p - one).sum(axis=1)
    cp = np.cumsum(p, axis=1)[:, :-1]; cy = np.cumsum(one, axis=1)[:, :-1]
    rps = np.square(cp - cy).sum(axis=1) / (K - 1)
    top1 = (np.argmax(p, axis=1) == y).astype(float)
    top3_idx = np.argpartition(p, -3, axis=1)[:, -3:]
    top3 = np.array([float(y[i] in top3_idx[i]) for i in range(len(y))])
    return {"ll": ll, "brier": brier, "rps": rps, "top1": top1, "top3": top3}


def ece_binary(y, p, bins=10):
    y = np.asarray(y, dtype=float); p = np.asarray(p, dtype=float)
    edges = np.linspace(0, 1, bins + 1); out = 0.0
    for i in range(bins):
        mask = (p >= edges[i]) & ((p < edges[i+1]) if i < bins-1 else (p <= edges[i+1]))
        if mask.any():
            out += mask.mean() * abs(float(y[mask].mean()) - float(p[mask].mean()))
    return float(out)


def logistic_calibration(y, p):
    y = np.asarray(y, dtype=float)
    if len(np.unique(y)) < 2:
        return {"intercept": None, "slope": None}
    x = logit_arr(p); X = np.column_stack([np.ones(len(x)), x]); beta = np.array([0.0, 1.0])
    for _ in range(100):
        eta = np.clip(X @ beta, -30, 30); mu = 1/(1+np.exp(-eta)); w = np.maximum(mu*(1-mu), 1e-8)
        z = eta + (y-mu)/w
        try: new = np.linalg.solve(X.T @ (w[:,None]*X) + np.eye(2)*1e-8, X.T @ (w*z))
        except np.linalg.LinAlgError: return {"intercept": None, "slope": None}
        if np.max(np.abs(new-beta)) < 1e-8: beta = new; break
        beta = new
    return {"intercept": float(beta[0]), "slope": float(beta[1])}


def summarize(y, p, rm):
    ge3p = p[:,3:].sum(axis=1)
    pred = np.argmax(p, axis=1); conf = np.max(p, axis=1); correct = (pred == y).astype(float)
    return {
        "n": int(len(y)), "logloss": float(rm["ll"].mean()), "brier": float(rm["brier"].mean()), "rps": float(rm["rps"].mean()),
        "top1": float(rm["top1"].mean()), "top3": float(rm["top3"].mean()),
        "auc_t_ge_3": float(roc_auc_score((y>=3).astype(int), ge3p)),
        "toplabel_ece_10": ece_binary(correct, conf), "t_ge_3_ece_10": ece_binary((y>=3).astype(int), ge3p),
        "t_ge_3_calibration": logistic_calibration((y>=3).astype(int), ge3p),
        "probability_sum_max_abs_error": float(np.max(np.abs(p.sum(axis=1)-1.0))),
    }


def delta(sb, sc):
    return {
        "logloss": sc["logloss"]-sb["logloss"], "brier": sc["brier"]-sb["brier"], "rps": sc["rps"]-sb["rps"],
        "top1_pp": 100*(sc["top1"]-sb["top1"]), "top3_pp": 100*(sc["top3"]-sb["top3"]),
        "auc_t_ge_3": sc["auc_t_ge_3"]-sb["auc_t_ge_3"], "toplabel_ece": sc["toplabel_ece_10"]-sb["toplabel_ece_10"],
        "t_ge_3_ece": sc["t_ge_3_ece_10"]-sb["t_ge_3_ece_10"],
    }


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default="artifacts/c074g_confirmation"); args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    raw, source_audit = load_all()
    duplicate_ids = int(raw.duplicated(["league_key","date","HomeTeam","AwayTeam"]).sum())
    feat = build_history_features(raw)
    usable = feat.loc[feat["eligible_history"] & feat["odds_valid"]].copy()
    train = usable.loc[(usable["season_start"] >= TRAIN_START) & (usable["season_start"] <= TRAIN_END)].copy()
    test = usable.loc[usable["season_start"] == TEST_START].copy()

    train_leagues = sorted(train.league_key.unique().tolist()); test_leagues = sorted(test.league_key.unique().tolist())
    train_classes = sorted(train.target.unique().astype(int).tolist()); test_classes = sorted(test.target.unique().astype(int).tolist())
    coverage = {
        "train_leagues": train_leagues, "test_leagues": test_leagues, "train_n": int(len(train)), "test_n": int(len(test)),
        "train_classes": train_classes, "test_classes": test_classes, "duplicate_identity_rows_all_loaded": duplicate_ids,
        "checks": {
            "train_leagues_ge_6": len(train_leagues) >= 6, "test_leagues_ge_6": len(test_leagues) >= 6,
            "train_n_ge_8000": len(train) >= 8000, "test_n_ge_1800": len(test) >= 1800,
            "all_8_classes_train": train_classes == list(range(K)), "all_8_classes_test": test_classes == list(range(K)),
            "duplicate_identity_rows_eq_0": duplicate_ids == 0,
        },
    }
    if not all(coverage["checks"].values()):
        summary = {"experiment":"C074-G","terminal":"STOP_DATA/COVERAGE","formal_weight":0,"coverage":coverage,"source_audit":source_audit,
                   "sealed_assets_touched":{"C071_reserve_52180":0,"C070F_confirmation_1597":0,"A05":0,"protected":0}}
        (out/"summary.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding="utf-8")
        pd.DataFrame(source_audit).to_csv(out/"source_files.csv",index=False)
        print(json.dumps(summary["coverage"],indent=2)); return

    ytr = train.target.to_numpy(dtype=int); yt = test.target.to_numpy(dtype=int)
    b = pipeline(); c = pipeline(); b.fit(train[BASE_FEATURES], ytr); c.fit(train[CANDIDATE_FEATURES], ytr)
    pb = aligned_proba(b, test[BASE_FEATURES]); pc = aligned_proba(c, test[CANDIDATE_FEATURES])
    mb = row_metrics(yt,pb); mc = row_metrics(yt,pc); sb = summarize(yt,pb,mb); sc = summarize(yt,pc,mc); d = delta(sb,sc)

    dll = mc["ll"]-mb["ll"]; rng=np.random.default_rng(BOOTSTRAP_SEED); boot=np.empty(BOOTSTRAPS); n=len(dll)
    for i in range(BOOTSTRAPS): boot[i]=float(dll[rng.integers(0,n,n)].mean())
    boot90=[float(np.quantile(boot,.05)),float(np.quantile(boot,.95))]

    league_rows=[]
    for lg in sorted(test.league_key.unique()):
        mask=(test.league_key.to_numpy()==lg); yy=yt[mask]; ppb=pb[mask]; ppc=pc[mask]
        lmb=row_metrics(yy,ppb); lmc=row_metrics(yy,ppc); lsb=summarize(yy,ppb,lmb); lsc=summarize(yy,ppc,lmc)
        league_rows.append({"league":lg,"n":int(mask.sum()),"baseline_ll":lsb["logloss"],"candidate_ll":lsc["logloss"],"d_ll":lsc["logloss"]-lsb["logloss"],
                            "d_brier":lsc["brier"]-lsb["brier"],"d_rps":lsc["rps"]-lsb["rps"],"d_top1_pp":100*(lsc["top1"]-lsb["top1"])})
    eligible_lg=[r for r in league_rows if r["n"]>=100]; league_wins=sum(r["d_ll"]<0 for r in eligible_lg)
    max_prob_res=max(sb["probability_sum_max_abs_error"],sc["probability_sum_max_abs_error"])
    gate={
        "pooled_dlogloss_lt_0": d["logloss"]<0,
        "bootstrap90_upper_dlogloss_lt_0": boot90[1]<0,
        "pooled_dbrier_le_0": d["brier"]<=0,
        "pooled_drps_le_0": d["rps"]<=0,
        "league_ll_wins_ge_4": league_wins>=4,
        "probability_conservation_le_1e_10": max_prob_res<=1e-10,
    }
    passed=all(gate.values())
    class_counts={str(k):int((yt==k).sum()) for k in range(K)}
    summary={
        "experiment":"C074-G","contract":"research/C074G_CONTRACT.md","formal_weight":0,
        "confirmation_domain":"Football-Data.co.uk 2025/26 seven frozen leagues",
        "training_seasons":"2019/20-2024/25","score_history_backfill_from":"2009/10 when available",
        "target":"T=min(FTHG+FTAG,7)","coverage":coverage,
        "model":{"C":C_FIXED,"min_history":MIN_HISTORY,"baseline_features":BASE_FEATURES,"candidate_added_feature":"movement_logit","feature_search":0,"C_search":0,"subset_search":0,"transform_search":0},
        "baseline":sb,"candidate":sc,"delta_candidate_minus_baseline":d,
        "paired_bootstrap":{"n":BOOTSTRAPS,"seed":BOOTSTRAP_SEED,"dlogloss_90_interval":boot90},
        "league_ll_wins":int(league_wins),"eligible_leagues_for_stability":len(eligible_lg),"leagues":league_rows,
        "confirmation_class_counts":class_counts,"gate_checks":gate,
        "terminal":"CONFIRMATION_PASS" if passed else "CONFIRMATION_FAIL",
        "source_audit":source_audit,
        "sealed_assets_touched":{"C071_reserve_52180":0,"C070F_confirmation_1597":0,"A05":0,"protected":0},
        "scientific_boundary":"PASS confirms only the frozen C074-E research component on a later season. It does not establish formal timestamped multi-line OU, formal weight, production Direct-T, exact tail, D|T, or exact-score matrix.",
    }
    (out/"summary.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding="utf-8")
    pd.DataFrame(league_rows).to_csv(out/"league_metrics.csv",index=False); pd.DataFrame(source_audit).to_csv(out/"source_files.csv",index=False)
    print(json.dumps({"terminal":summary["terminal"],"train_n":len(train),"test_n":len(test),"baseline_ll":sb["logloss"],"candidate_ll":sc["logloss"],"dlogloss":d["logloss"],"dbrier":d["brier"],"drps":d["rps"],"top1_pp":d["top1_pp"],"top3_pp":d["top3_pp"],"bootstrap90":boot90,"league_wins":league_wins,"gate":gate},indent=2))

if __name__ == "__main__": main()
