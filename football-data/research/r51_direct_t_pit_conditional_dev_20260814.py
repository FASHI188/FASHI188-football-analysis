#!/usr/bin/env python3
"""R51 development: Direct-T -> conditional D=0|T with PIT transition residuals.

Hard boundary: only 2021-22..2024-25 result labels are read. 2025-26 is never
opened by this executable. Research only, formal_weight=0.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import numpy as np
from scipy.optimize import minimize

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from r50_pit_draw_architecture_dev_20260814 import build_transitions, norm_team

DEV_SEASONS = ("2021-22", "2022-23", "2023-24", "2024-25")
FOLDS = (
    (("2021-22",), "2022-23"),
    (("2021-22", "2022-23"), "2023-24"),
    (("2021-22", "2022-23", "2023-24"), "2024-25"),
)
FORBIDDEN = "2025-26"
K = 8
L2_BASE = 2.0
L2_RESIDUAL = 2.0
MAX_ITER = 500
TOL = 1e-9
EPS = 1e-12
SEED = 20260814
CANDIDATES = (
    "A_PIT_TO_DIRECT_T_ONLY",
    "B_PIT_TO_CONDITIONAL_D_ONLY",
    "C_FACTORIZED_PIT_TO_T_AND_D",
)
INTENSITY_IDX = (0, 2, 4)
SYMMETRY_IDX = (1, 3, 5)
BASE_FEATURE_NAMES = [
    "no_vig_pH", "no_vig_pD", "no_vig_pA", "no_vig_pOver2.5",
    "one_x_two_overround", "side_balance", "market_entropy",
    "home_prior_gf", "home_prior_ga", "home_prior_total", "home_prior_draw",
    "home_prior_low2", "home_prior_btts", "home_prior_absdiff", "log1p_home_prior_n",
    "away_prior_gf", "away_prior_ga", "away_prior_total", "away_prior_draw",
    "away_prior_low2", "away_prior_btts", "away_prior_absdiff", "log1p_away_prior_n",
    "pair_total_sum", "pair_total_absdiff", "pair_draw_mean", "pair_draw_absdiff",
    "pair_low2_mean", "pair_low2_absdiff", "league_prior_total", "league_prior_draw",
    "league_prior_low2",
]


def fnum(x):
    try:
        v = float(str(x).strip())
        return v if math.isfinite(v) else None
    except Exception:
        return None


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def softmax(logits: np.ndarray) -> np.ndarray:
    z = logits - logits.max(axis=1, keepdims=True)
    e = np.exp(np.clip(z, -50, 50))
    return e / np.maximum(e.sum(axis=1, keepdims=True), EPS)


def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -40, 40)))


def logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-9, 1 - 1e-9)
    return np.log(p / (1 - p))


def standardize(train: np.ndarray, test: np.ndarray):
    m = train.mean(axis=0)
    s = train.std(axis=0)
    s = np.where(s > 1e-12, s, 1.0)
    return (train - m) / s, (test - m) / s, m, s


def market_1x2(r: dict):
    for cols, label in (
        (("AvgCH", "AvgCD", "AvgCA"), "AvgC"),
        (("B365CH", "B365CD", "B365CA"), "B365C"),
        (("MaxCH", "MaxCD", "MaxCA"), "MaxC"),
        (("PSCH", "PSCD", "PSCA"), "PSC"),
    ):
        v = [fnum(r.get(c)) for c in cols]
        if all(x is not None and x > 1 for x in v):
            inv = np.array([1 / x for x in v], dtype=float)
            return inv / inv.sum(), float(inv.sum() - 1), label
    return None, None, None


def market_ou25(r: dict):
    for cols, label in (
        (("AvgC>2.5", "AvgC<2.5"), "AvgC"),
        (("B365C>2.5", "B365C<2.5"), "B365C"),
        (("MaxC>2.5", "MaxC<2.5"), "MaxC"),
        (("PC>2.5", "PC<2.5"), "PC"),
    ):
        over, under = fnum(r.get(cols[0])), fnum(r.get(cols[1]))
        if over is not None and under is not None and over > 1 and under > 1:
            io, iu = 1 / over, 1 / under
            return io / (io + iu), label
    return None, None


class State:
    __slots__ = ("n", "gf", "ga", "total", "draw", "low2", "btts", "absdiff")
    def __init__(self):
        self.n = 0; self.gf = 0.; self.ga = 0.; self.total = 0.; self.draw = 0.; self.low2 = 0.; self.btts = 0.; self.absdiff = 0.
    def mean(self, attr: str, fallback: float) -> float:
        return getattr(self, attr) / self.n if self.n else fallback
    def update(self, gf: int, ga: int):
        self.n += 1; self.gf += gf; self.ga += ga; t = gf + ga; self.total += t
        self.draw += int(gf == ga); self.low2 += int(t <= 2); self.btts += int(gf > 0 and ga > 0); self.absdiff += abs(gf - ga)


def parse_date(s: str):
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try: return datetime.strptime(s, fmt)
        except ValueError: pass
    raise ValueError(s)


def load_full_rows(repo_root: Path) -> Dict[str, List[dict]]:
    raw = []
    for season in DEV_SEASONS:
        assert season != FORBIDDEN
        p = repo_root / "football-data" / "processed" / "ENG_PremierLeague" / f"{season}.csv"
        with p.open("r", encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                phda, overround, hda_src = market_1x2(r); pover, ou_src = market_ou25(r)
                hg, ag = fnum(r.get("FTHG")), fnum(r.get("FTAG"))
                if phda is None or pover is None or hg is None or ag is None: continue
                hg, ag = int(hg), int(ag); dt = parse_date(str(r["Date"]).strip())
                raw.append({
                    "season": season, "date": dt.date().isoformat(), "dt": dt,
                    "home": str(r["HomeTeam"]).strip(), "away": str(r["AwayTeam"]).strip(),
                    "hg": hg, "ag": ag, "total": hg + ag, "tclass": min(hg + ag, 7),
                    "actual": "H" if hg > ag else "D" if hg == ag else "A",
                    "p": phda, "pover": pover, "overround": overround,
                    "hda_source": hda_src, "ou_source": ou_src,
                })
    raw.sort(key=lambda z: (z["dt"], z["home"], z["away"]))
    teams: Dict[str, State] = defaultdict(State); league = State(); out_by_season = defaultdict(list)
    for r in raw:
        hkey, akey = norm_team(r["home"]), norm_team(r["away"])
        hs, as_ = teams[hkey], teams[akey]
        league_total = league.total / league.n if league.n else 2.6
        league_draw = league.draw / league.n if league.n else 0.25
        league_low2 = league.low2 / league.n if league.n else 0.50
        league_gf = league.gf / league.n if league.n else 1.3
        league_ga = league.ga / league.n if league.n else 1.3
        league_btts = league.btts / league.n if league.n else 0.50
        league_abs = league.absdiff / league.n if league.n else 1.2
        def vals(s: State):
            return {
                "gf": s.mean("gf", league_gf), "ga": s.mean("ga", league_ga),
                "total": s.mean("total", league_total), "draw": s.mean("draw", league_draw),
                "low2": s.mean("low2", league_low2), "btts": s.mean("btts", league_btts),
                "absdiff": s.mean("absdiff", league_abs), "n": s.n,
            }
        hv, av = vals(hs), vals(as_); p = r["p"]
        ent = -float(np.sum(p * np.log(np.clip(p, EPS, 1))))
        feat = [
            p[0], p[1], p[2], r["pover"], r["overround"], abs(p[0] - p[2]), ent,
            hv["gf"], hv["ga"], hv["total"], hv["draw"], hv["low2"], hv["btts"], hv["absdiff"], math.log1p(hv["n"]),
            av["gf"], av["ga"], av["total"], av["draw"], av["low2"], av["btts"], av["absdiff"], math.log1p(av["n"]),
            hv["total"] + av["total"], abs(hv["total"] - av["total"]),
            0.5 * (hv["draw"] + av["draw"]), abs(hv["draw"] - av["draw"]),
            0.5 * (hv["low2"] + av["low2"]), abs(hv["low2"] - av["low2"]),
            league_total, league_draw, league_low2,
        ]
        assert len(feat) == len(BASE_FEATURE_NAMES)
        rr = dict(r); rr["base_features"] = np.array(feat, dtype=float); rr["prior_h_n"] = hs.n; rr["prior_a_n"] = as_.n
        # Require a minimal true prior for both teams; no target result has been updated yet.
        if hs.n >= 5 and as_.n >= 5 and league.n >= 20:
            out_by_season[r["season"]].append(rr)
        hs.update(r["hg"], r["ag"]); as_.update(r["ag"], r["hg"]); league.update(r["hg"], r["ag"])
    return dict(out_by_season)


def join_pit(full_rows: Sequence[dict], transitions: Sequence[dict]):
    idx = defaultdict(list)
    for r in full_rows:
        idx[(r["date"], norm_team(r["home"]), norm_team(r["away"]))].append(r)
    out = []; ambiguous = unmatched = 0
    for t in transitions:
        hits = idx.get((t["date"], norm_team(t["home"]), norm_team(t["away"])), [])
        if len(hits) != 1:
            ambiguous += int(len(hits) > 1); unmatched += int(len(hits) == 0); continue
        x = dict(hits[0]); x["pit_X"] = np.array(t["X"], dtype=float); x["pit_current_snapshot"] = t["current_snapshot"]; x["pit_previous_snapshot"] = t["previous_snapshot"]
        out.append(x)
    return out, {"transitions": len(transitions), "matched": len(out), "ambiguous": ambiguous, "unmatched": unmatched}


def fit_multinomial(X: np.ndarray, y: np.ndarray, k: int = K, l2: float = L2_BASE):
    n, d = X.shape; X1 = np.column_stack([np.ones(n), X]); d1 = d + 1
    def unpack(v):
        b = np.zeros((k, d1)); b[:k-1] = v.reshape(k-1, d1); return b
    def fg(v):
        b = unpack(v); pr = softmax(X1 @ b.T); loss = -np.log(np.clip(pr[np.arange(n), y], EPS, 1)).mean()
        loss += 0.5 * l2 / n * np.sum(b[:k-1, 1:] ** 2)
        Y = np.zeros_like(pr); Y[np.arange(n), y] = 1
        g = ((pr - Y).T @ X1) / n; g[:k-1, 1:] += l2 / n * b[:k-1, 1:]
        return float(loss), g[:k-1].ravel()
    res = minimize(lambda v: fg(v), np.zeros((k-1)*d1), jac=True, method="L-BFGS-B", options={"maxiter": MAX_ITER, "ftol": TOL, "gtol": 1e-7})
    return unpack(res.x), {"success": bool(res.success), "iterations": int(res.nit), "fun": float(res.fun), "message": str(res.message)}


def predict_multinomial(X: np.ndarray, b: np.ndarray):
    return softmax(np.column_stack([np.ones(len(X)), X]) @ b.T)


def fit_binary(X: np.ndarray, y: np.ndarray, l2=L2_BASE, intercept=True, offset=None):
    n, d = X.shape; X1 = np.column_stack([np.ones(n), X]) if intercept else X
    pen_start = 1 if intercept else 0
    off = np.zeros(n) if offset is None else np.asarray(offset, dtype=float)
    def fg(v):
        eta = off + X1 @ v; p = sigmoid(eta)
        loss = -np.mean(y*np.log(np.clip(p, EPS, 1)) + (1-y)*np.log(np.clip(1-p, EPS, 1)))
        loss += 0.5*l2/n*np.sum(v[pen_start:]**2)
        g = X1.T @ (p-y) / n; g[pen_start:] += l2/n*v[pen_start:]
        return float(loss), g
    res = minimize(lambda v: fg(v), np.zeros(X1.shape[1]), jac=True, method="L-BFGS-B", options={"maxiter": MAX_ITER, "ftol": TOL, "gtol": 1e-7})
    return res.x, {"success": bool(res.success), "iterations": int(res.nit), "fun": float(res.fun), "message": str(res.message)}


def predict_binary(X: np.ndarray, b: np.ndarray, intercept=True, offset=None):
    X1 = np.column_stack([np.ones(len(X)), X]) if intercept else X
    off = np.zeros(len(X)) if offset is None else np.asarray(offset, dtype=float)
    return sigmoid(off + X1 @ b)


def bucket_features(Z: np.ndarray, bucket: int):
    # T2 is reference; T4/T6/T7+ are explicit dummies.
    d = np.tile(np.array([bucket == 4, bucket == 6, bucket == 7], dtype=float), (len(Z), 1))
    return np.column_stack([Z, d])


def fit_conditional(Z: np.ndarray, rows: Sequence[dict]):
    mask = np.array([r["tclass"] in {2,4,6,7} for r in rows])
    buckets = np.array([r["tclass"] for r in rows], dtype=int)[mask]
    X = np.vstack([bucket_features(Z[mask][i:i+1], int(b))[0] for i, b in enumerate(buckets)])
    y = np.array([rows[i]["actual"] == "D" for i in np.where(mask)[0]], dtype=float)
    if len(y) < 50 or len(np.unique(y)) < 2: raise RuntimeError("insufficient conditional rows/classes")
    b, audit = fit_binary(X, y, l2=L2_BASE, intercept=True); audit["training_rows"] = int(len(y)); audit["draw_rows"] = int(y.sum())
    return b, audit


def conditional_grid(Z: np.ndarray, b: np.ndarray):
    return {bucket: predict_binary(bucket_features(Z, bucket), b, intercept=True) for bucket in (2,4,6,7)}


def fit_offset_multinomial(base_p: np.ndarray, Z: np.ndarray, y: np.ndarray, l2=L2_RESIDUAL):
    n, d = Z.shape
    def unpack(v):
        b = np.zeros((K, d)); b[:K-1] = v.reshape(K-1, d); return b
    logbase = np.log(np.clip(base_p, 1e-10, 1))
    def fg(v):
        b = unpack(v); pr = softmax(logbase + Z @ b.T)
        loss = -np.log(np.clip(pr[np.arange(n), y], EPS, 1)).mean() + 0.5*l2/n*np.sum(b[:K-1]**2)
        Y = np.zeros_like(pr); Y[np.arange(n), y] = 1
        g = ((pr-Y).T @ Z)/n; g[:K-1] += l2/n*b[:K-1]
        return float(loss), g[:K-1].ravel()
    res = minimize(lambda v: fg(v), np.zeros((K-1)*d), jac=True, method="L-BFGS-B", options={"maxiter": MAX_ITER, "ftol": TOL, "gtol": 1e-7})
    return unpack(res.x), {"success": bool(res.success), "iterations": int(res.nit), "fun": float(res.fun), "message": str(res.message)}


def apply_offset_multinomial(base_p: np.ndarray, Z: np.ndarray, b: np.ndarray):
    return softmax(np.log(np.clip(base_p, 1e-10, 1)) + Z @ b.T)


def draw_from_structure(pt: np.ndarray, cond: Dict[int, np.ndarray]):
    q = pt[:,0].copy()
    q += pt[:,2]*cond[2] + pt[:,4]*cond[4] + pt[:,6]*cond[6] + pt[:,7]*cond[7]
    return np.clip(q, 1e-9, 1-1e-9)


def hda_from_draw(qd: np.ndarray, market: np.ndarray):
    den = np.maximum(market[:,0] + market[:,2], EPS); rh = market[:,0]/den
    return np.column_stack([(1-qd)*rh, qd, (1-qd)*(1-rh)])


def y_hda(rows):
    mp={"H":0,"D":1,"A":2}; return np.array([mp[r["actual"]] for r in rows], dtype=int)


def auc(y, s):
    y=np.asarray(y,dtype=int); n1=int(y.sum()); n0=len(y)-n1
    if n1==0 or n0==0: return float("nan")
    order=np.argsort(s,kind="mergesort"); ranks=np.empty(len(s),dtype=float); ss=s[order]; i=0
    while i<len(s):
        j=i+1
        while j<len(s) and ss[j]==ss[i]: j+=1
        ranks[order[i:j]]=0.5*((i+1)+j); i=j
    return float((ranks[y==1].sum()-n1*(n1+1)/2)/(n1*n0))


def metrics(rows, hda, pt):
    yi=y_hda(rows); yd=(yi==1).astype(float); p=np.clip(hda,EPS,1-EPS); n=len(rows); pred=np.argmax(p,axis=1); dm=pred==1
    ty=np.array([r["tclass"] for r in rows],dtype=int); tp=np.clip(pt,EPS,1)
    hits=int(np.sum(dm&(yi==1))); actual=int(yd.sum())
    return {
        "n":n, "draw_actual":actual, "draw_prevalence":actual/n,
        "hda_logloss":-float(np.mean(np.log(p[np.arange(n),yi]))),
        "draw_logloss":-float(np.mean(yd*np.log(p[:,1])+(1-yd)*np.log(1-p[:,1]))),
        "draw_brier":float(np.mean((p[:,1]-yd)**2)), "draw_auc":auc(yd,p[:,1]),
        "hda_accuracy":float(np.mean(pred==yi)), "natural_top1_draw_count":int(dm.sum()),
        "natural_top1_draw_hits":hits, "natural_top1_draw_precision":hits/int(dm.sum()) if dm.sum() else 0.0,
        "natural_top1_draw_recall":hits/actual if actual else 0.0,
        "direct_t_logloss":-float(np.mean(np.log(tp[np.arange(n),ty]))),
        "direct_t_top1_accuracy":float(np.mean(np.argmax(pt,axis=1)==ty)),
        "hda_probability_sum_max_abs_error":float(np.max(np.abs(hda.sum(axis=1)-1))),
        "direct_t_probability_sum_max_abs_error":float(np.max(np.abs(pt.sum(axis=1)-1))),
    }


def deltas(c,b):
    keys=("hda_logloss","draw_logloss","draw_brier","draw_auc","hda_accuracy","direct_t_logloss","direct_t_top1_accuracy")
    return {k:c[k]-b[k] for k in keys}


def bootstrap(rows, cand, base, reps=5000):
    yi=y_hda(rows); cp=np.clip(cand,EPS,1); bp=np.clip(base,EPS,1)
    d=-np.log(cp[np.arange(len(rows)),yi])+np.log(bp[np.arange(len(rows)),yi])
    dates=sorted(set(r["date"] for r in rows)); groups={x:np.array([i for i,r in enumerate(rows) if r["date"]==x]) for x in dates}
    rng=np.random.default_rng(SEED); vals=np.empty(reps)
    for b in range(reps):
        ds=rng.choice(dates,size=len(dates),replace=True); idx=np.concatenate([groups[x] for x in ds]); vals[b]=d[idx].mean()
    return {"p05":float(np.quantile(vals,.05)),"median":float(np.quantile(vals,.5)),"p95":float(np.quantile(vals,.95)),"p_delta_lt_0":float(np.mean(vals<0)),"reps":reps,"seed":SEED,"unit":"calendar_date"}


def base_models(train_full, train_pit, test_pit):
    Xf=np.vstack([r["base_features"] for r in train_full]); Xp=np.vstack([r["base_features"] for r in train_pit]); Xt=np.vstack([r["base_features"] for r in test_pit])
    Zf,Zt,m,s=standardize(Xf,Xt); Zp=(Xp-m)/s
    yT=np.array([r["tclass"] for r in train_full],dtype=int); mb,ma=fit_multinomial(Zf,yT); pt_train_pit=predict_multinomial(Zp,mb); pt_test=predict_multinomial(Zt,mb)
    cb,ca=fit_conditional(Zf,train_full); cond_train=conditional_grid(Zp,cb); cond_test=conditional_grid(Zt,cb)
    return {"mean":m,"sd":s,"Zp":Zp,"Zt":Zt,"pt_train_pit":pt_train_pit,"pt_test":pt_test,"cond_train":cond_train,"cond_test":cond_test,"multinomial_audit":ma,"conditional_audit":ca}


def fit_cond_residual(train_pit, Zpit, cond_train, indices):
    mask=np.array([r["tclass"] in {2,4,6,7} for r in train_pit]); pos=np.where(mask)[0]
    X=Zpit[mask][:,indices]; base=np.array([cond_train[int(train_pit[i]["tclass"])][i] for i in pos]); y=np.array([train_pit[i]["actual"]=="D" for i in pos],dtype=float)
    if len(y)<25 or len(np.unique(y))<2: raise RuntimeError("insufficient PIT conditional residual rows/classes")
    b,a=fit_binary(X,y,l2=L2_RESIDUAL,intercept=False,offset=logit(base)); a["training_rows"]=int(len(y)); a["draw_rows"]=int(y.sum()); return b,a


def apply_cond_residual(cond_base,Zpit,b,indices):
    return {bucket:predict_binary(Zpit[:,indices],b,intercept=False,offset=logit(q)) for bucket,q in cond_base.items()}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--repo-root",default="."); ap.add_argument("--fpl-repo",required=True); ap.add_argument("--out",required=True); ap.add_argument("--prereg",default="football-data/research/r51_direct_t_pit_conditional_prereg_20260814.json"); args=ap.parse_args()
    root=Path(args.repo_root).resolve(); fpl=Path(args.fpl_repo).resolve(); out=Path(args.out).resolve(); out.mkdir(parents=True,exist_ok=True)
    prereg=root/args.prereg; po=json.loads(prereg.read_text(encoding="utf-8")); assert po["confirmation_protection"]["labels_open"] is False; assert po["governance"]["confirmation_2025_26_label_access"] is False
    assert FORBIDDEN not in DEV_SEASONS and all(FORBIDDEN not in tr and te!=FORBIDDEN for tr,te in FOLDS)
    full=load_full_rows(root); pit={}; input_audit={}
    for s in DEV_SEASONS:
        tr=build_transitions(fpl,s); joined,ja=join_pit(full[s],tr); pit[s]=joined; input_audit[s]={"full_market_prior_rows":len(full[s]),**ja,"joined_pit_rows":len(joined)}
        if len(full[s])<250 or len(joined)<50: raise RuntimeError(f"insufficient R51 rows {s}: full={len(full[s])} pit={len(joined)}")
    fold_results=[]; pooled_rows=[]; pooled_base_h=[]; pooled_base_t=[]; pooled_c={c:{"h":[],"t":[]} for c in CANDIDATES}; fit_audits=[]
    for fno,(train_seasons,test_season) in enumerate(FOLDS,1):
        train_full=[r for s in train_seasons for r in full[s]]; train_pit=[r for s in train_seasons for r in pit[s]]; test=list(pit[test_season])
        bm=base_models(train_full,train_pit,test); market=np.vstack([r["p"] for r in test]); q0=draw_from_structure(bm["pt_test"],bm["cond_test"]); h0=hda_from_draw(q0,market); base_m=metrics(test,h0,bm["pt_test"])
        Xpit=np.vstack([r["pit_X"] for r in train_pit]); Xtest=np.vstack([r["pit_X"] for r in test]); Zpit,Ztest,pm,ps=standardize(Xpit,Xtest); yt=np.array([r["tclass"] for r in train_pit],dtype=int)
        fold={"fold":fno,"train_seasons":list(train_seasons),"test_season":test_season,"train_full_n":len(train_full),"train_pit_n":len(train_pit),"test_pit_n":len(test),"baseline":base_m,"candidates":{}}
        # A: PIT -> Direct-T only
        rbA,raA=fit_offset_multinomial(bm["pt_train_pit"],Zpit,yt); ptA=apply_offset_multinomial(bm["pt_test"],Ztest,rbA); hA=hda_from_draw(draw_from_structure(ptA,bm["cond_test"]),market)
        # B: PIT -> conditional D only
        cbB,caB=fit_cond_residual(train_pit,Zpit,bm["cond_train"],tuple(range(6))); condB=apply_cond_residual(bm["cond_test"],Ztest,cbB,tuple(range(6))); ptB=bm["pt_test"]; hB=hda_from_draw(draw_from_structure(ptB,condB),market)
        # C: factorized
        rbC,raC=fit_offset_multinomial(bm["pt_train_pit"],Zpit[:,INTENSITY_IDX],yt); ptC=apply_offset_multinomial(bm["pt_test"],Ztest[:,INTENSITY_IDX],rbC)
        cbC,caC=fit_cond_residual(train_pit,Zpit,bm["cond_train"],SYMMETRY_IDX); condC=apply_cond_residual(bm["cond_test"],Ztest,cbC,SYMMETRY_IDX); hC=hda_from_draw(draw_from_structure(ptC,condC),market)
        vals={CANDIDATES[0]:(hA,ptA),CANDIDATES[1]:(hB,ptB),CANDIDATES[2]:(hC,ptC)}
        fit_audits.append({"fold":fno,"baseline_multinomial":bm["multinomial_audit"],"baseline_conditional":bm["conditional_audit"],"A_total_residual":raA,"B_cond_residual":caB,"C_total_residual":raC,"C_cond_residual":caC,"pit_mean":pm.tolist(),"pit_sd":ps.tolist()})
        for c,(hh,tt) in vals.items():
            cm=metrics(test,hh,tt); fold["candidates"][c]={"metrics":cm,"delta":deltas(cm,base_m)}; pooled_c[c]["h"].append(hh); pooled_c[c]["t"].append(tt)
        fold_results.append(fold); pooled_rows.extend(test); pooled_base_h.append(h0); pooled_base_t.append(bm["pt_test"])
    bh=np.vstack(pooled_base_h); bt=np.vstack(pooled_base_t); base_pooled=metrics(pooled_rows,bh,bt); pooled={}; eligible=[]
    for c in CANDIDATES:
        hh=np.vstack(pooled_c[c]["h"]); tt=np.vstack(pooled_c[c]["t"]); cm=metrics(pooled_rows,hh,tt); de=deltas(cm,base_pooled); nonworse=sum(1 for fr in fold_results if fr["candidates"][c]["delta"]["hda_logloss"]<=0)
        structural=cm["hda_probability_sum_max_abs_error"]<=1e-10 and cm["direct_t_probability_sum_max_abs_error"]<=1e-10
        proper=de["hda_logloss"]<0 and de["draw_logloss"]<0 and de["draw_brier"]<0 and de["draw_auc"]>0 and nonworse>=2
        min_count=max(10,math.ceil(.01*cm["n"])); min_precision=max(cm["draw_prevalence"]+.08,.35); execution=cm["natural_top1_draw_count"]>=min_count and cm["natural_top1_draw_precision"]>=min_precision and cm["natural_top1_draw_recall"]>=.05 and de["hda_accuracy"]>=-.005
        pooled[c]={"metrics":cm,"delta":de,"fold_hda_ll_nonworse_count":nonworse,"structural_gate":structural,"proper_score_gate":proper,"execution_gate":execution,"requirements":{"min_draw_count":min_count,"min_draw_precision":min_precision,"min_draw_recall":.05,"min_accuracy_delta":-.005},"bootstrap90_hda_ll_delta":bootstrap(pooled_rows,hh,bh),"development_gate_pass":bool(structural and proper and execution)}
        if structural and proper and execution: eligible.append(c)
    winner=min(eligible,key=lambda c:pooled[c]["metrics"]["hda_logloss"]) if eligible else None; status="PASS_R51_DEVELOPMENT_ONE_WINNER_FREEZE_BEFORE_CONFIRMATION" if winner else "FAIL_R51_DEVELOPMENT_NO_CANDIDATE_EARNS_CONFIRMATION"
    result={"schema_version":"R51-DIRECT-T-PIT-CONDITIONAL-RESULT-R1","status":status,"winner":winner,"eligible_candidates":eligible,"prereg_sha256":sha256_file(prereg),"development_seasons":list(DEV_SEASONS),"input_audit":input_audit,"folds":fold_results,"pooled_baseline":base_pooled,"pooled_candidates":pooled,"fit_audits":fit_audits,"governance":{"confirmation_2025_26_label_access":0,"confirmation_2025_26_score_access":0,"confirmation_window_remains_closed":True,"formal_weight":0,"provider_requests":0,"paid_provider_requests":0,"formal_model_data_config_current_changes":[0,0,0,0]},"interpretation":"Development PASS only earns a separately preregistered confirmation test. It is not formal promotion. FAIL keeps 2025/26 untouched."}
    text=json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True)+"\n"; (out/"r51_result.json").write_text(text,encoding="utf-8")
    receipt={"status":status,"winner":winner,"pooled_test_n":base_pooled["n"],"pooled_actual_draws":base_pooled["draw_actual"],"prereg_sha256":result["prereg_sha256"],"result_sha256":hashlib.sha256(text.encode()).hexdigest(),"confirmation_2025_26_label_access":0,"confirmation_window_remains_closed":True}
    (out/"r51_receipt.json").write_text(json.dumps(receipt,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8"); print(json.dumps(receipt,ensure_ascii=False,sort_keys=True))

if __name__ == "__main__": main()
