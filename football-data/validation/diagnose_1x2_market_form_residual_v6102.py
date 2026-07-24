#!/usr/bin/env python3
"""Research-only market + lagged form residual diagnostic for 1X2.

Adds only information available before each target match:
- de-vigged retrospective market probabilities (primary anchor),
- Elo state updated from prior results only,
- last-5/last-10 points, goal difference, goals for/against,
- venue-specific recent points,
- rest-day difference,
- rolling actual-minus-market-expected-points surprise from prior matched games.

Every feature snapshot is taken BEFORE updating state with the current match result.
Within each competition the earliest 60% trains, next 20% selects, latest 20% tests.
Legacy prices remain retrospective reference only and cannot satisfy CURRENT snapshot gates.
"""
from __future__ import annotations

import json, math, statistics, sys
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]; VALIDATION=ROOT/"validation"; ENGINE=ROOT/"engine"
for p in (VALIDATION,ENGINE):
    if str(p) not in sys.path: sys.path.insert(0,str(p))

from diagnose_1x2_market_anchor_v697 import _load_model_rows,_match_market,_market_probs,_pick_probs
from platform_core import read_processed_matches
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

OUT=ROOT/"manifests"/"v6_1x2_market_form_residual_v6102_status.json"
DIRECTIONS=("home","draw","away"); C2I={"home":0,"draw":1,"away":2}; I2C={v:k for k,v in C2I.items()}
SEED=20260724+6102

def _key(cid,season,date,home,away): return (cid,season,date,home,away)
def _avg(items,key,n,default=0.0):
    vals=[float(x[key]) for x in list(items)[-n:] if key in x and x[key] is not None]
    return sum(vals)/len(vals) if vals else default

def _entropy(p): return -sum(p[k]*math.log(max(1e-12,p[k])) for k in DIRECTIONS)
def _margin(p):
    v=sorted(p.values(),reverse=True); return v[0]-v[1]
def _expected_points(p,side): return 3.0*p[side] + p["draw"]
def _actual_points(actual,side):
    if actual=="draw": return 1.0
    return 3.0 if actual==side else 0.0

def _build_rows():
    market_rows,providers=_match_market(_load_model_rows())
    target={_key(r["competition_id"],r["season"],r["date"],r["home_team"],r["away_team"]):r for r in market_rows}
    out=[]
    for cid in sorted({r["competition_id"] for r in market_rows}):
        matches=read_processed_matches(cid)
        # Only seasons represented in target rows, normally one completed season.
        seasons={r["season"] for r in market_rows if r["competition_id"]==cid}
        matches=sorted([m for m in matches if m.season in seasons],key=lambda m:(m.date,m.home_team,m.away_team))
        elo=defaultdict(lambda:1500.0); hist=defaultdict(lambda:deque(maxlen=20)); venue_hist=defaultdict(lambda:deque(maxlen=12)); last_date={}
        for m in matches:
            date_iso=m.date.isoformat(); key=_key(cid,m.season,date_iso,m.home_team,m.away_team)
            row=target.get(key)
            h=m.home_team; a=m.away_team
            if row is not None:
                q=_market_probs(row)
                hh=hist[h]; ah=hist[a]; hv=venue_hist[(h,"home")]; av=venue_hist[(a,"away")]
                rest_h=(m.date-last_date[h]).days if h in last_date else 14
                rest_a=(m.date-last_date[a]).days if a in last_date else 14
                f={
                    "market_home":q["home"],"market_draw":q["draw"],"market_away":q["away"],
                    "market_max":max(q.values()),"market_margin":_margin(q),"market_entropy":_entropy(q),
                    "elo_diff_home_adv":elo[h]+60.0-elo[a],
                    "home_ppg5":_avg(hh,"points",5),"away_ppg5":_avg(ah,"points",5),
                    "home_ppg10":_avg(hh,"points",10),"away_ppg10":_avg(ah,"points",10),
                    "home_gd5":_avg(hh,"gd",5),"away_gd5":_avg(ah,"gd",5),
                    "home_gd10":_avg(hh,"gd",10),"away_gd10":_avg(ah,"gd",10),
                    "home_gf5":_avg(hh,"gf",5),"away_gf5":_avg(ah,"gf",5),
                    "home_ga5":_avg(hh,"ga",5),"away_ga5":_avg(ah,"ga",5),
                    "home_venue_ppg5":_avg(hv,"points",5),"away_venue_ppg5":_avg(av,"points",5),
                    "home_market_surprise5":_avg(hh,"market_surprise",5),"away_market_surprise5":_avg(ah,"market_surprise",5),
                    "home_market_surprise10":_avg(hh,"market_surprise",10),"away_market_surprise10":_avg(ah,"market_surprise",10),
                    "rest_diff_days":float(max(-21,min(21,rest_h-rest_a))),
                    "home_games_seen":float(len(hh)),"away_games_seen":float(len(ah)),
                }
                out.append({**row,"features":f})
            # update all result-derived states only AFTER feature snapshot
            actual="home" if m.home_goals>m.away_goals else "draw" if m.home_goals==m.away_goals else "away"
            hp=_actual_points(actual,"home"); ap=_actual_points(actual,"away")
            q=_market_probs(row) if row is not None else None
            hs=q and hp-_expected_points(q,"home"); ass=q and ap-_expected_points(q,"away")
            he={"points":hp,"gf":m.home_goals,"ga":m.away_goals,"gd":m.home_goals-m.away_goals,"market_surprise":hs}
            ae={"points":ap,"gf":m.away_goals,"ga":m.home_goals,"gd":m.away_goals-m.home_goals,"market_surprise":ass}
            hist[h].append(he); hist[a].append(ae); venue_hist[(h,"home")].append(he); venue_hist[(a,"away")].append(ae)
            # Elo update strictly after result
            exp_h=1.0/(1.0+10.0**((elo[a]-(elo[h]+60.0))/400.0)); score_h=1.0 if actual=="home" else 0.5 if actual=="draw" else 0.0
            delta=20.0*(score_h-exp_h); elo[h]+=delta; elo[a]-=delta
            last_date[h]=m.date; last_date[a]=m.date
    return out,providers

def _split(rows):
    tr=[]; va=[]; te=[]
    for cid in sorted({r["competition_id"] for r in rows}):
        sub=sorted([r for r in rows if r["competition_id"]==cid],key=lambda r:(r["date"],r["home_team"],r["away_team"]))
        n=len(sub); i1=int(n*.6); i2=int(n*.8)
        tr+=sub[:i1]; va+=sub[i1:i2]; te+=sub[i2:]
    return tr,va,te

def _names(rows):
    comps=sorted({r["competition_id"] for r in rows}); nums=sorted(rows[0]["features"])
    return nums,comps

def _X(rows,nums,comps):
    return [[float(r["features"].get(n,0.0)) for n in nums]+[1.0 if r["competition_id"]==c else 0.0 for c in comps] for r in rows]
def _y(rows): return [C2I[r["actual"]] for r in rows]
def _labels(model,X): return [I2C[int(x)] for x in model.predict(X)]
def _acc(rows,picks):
    h=sum(1 for r,p in zip(rows,picks) if r["actual"]==p); return {"count":len(rows),"hits":h,"accuracy":h/len(rows)}
def _market_picks(rows): return [_pick_probs(_market_probs(r)) for r in rows]
def _brier(proba,y): return sum(sum((float(p[j])-(1 if yi==j else 0))**2 for j in range(3)) for p,yi in zip(proba,y))/len(y)
def _models():
    out=[]
    for c in (.03,.1,.3,1.0,3.0): out.append((f"logreg_C{c}",Pipeline([("s",StandardScaler()),("m",LogisticRegression(C=c,max_iter=3000,solver="lbfgs",random_state=SEED))])))
    for lr in (.02,.04,.06):
        for leaves in (5,9):
            for l2 in (3.0,10.0,30.0): out.append((f"hgb_{lr}_{leaves}_{l2}",HistGradientBoostingClassifier(learning_rate=lr,max_iter=160,max_leaf_nodes=leaves,min_samples_leaf=40,l2_regularization=l2,random_state=SEED)))
    return out

def main():
    rows,providers=_build_rows(); tr,va,te=_split(rows); nums,comps=_names(rows)
    Xtr=_X(tr,nums,comps); ytr=_y(tr); Xv=_X(va,nums,comps); yv=_y(va); Xt=_X(te,nums,comps); yt=_y(te)
    leaderboard=[]
    for name,model in _models():
        model.fit(Xtr,ytr); picks=_labels(model,Xv); score=_acc(va,picks); proba=model.predict_proba(Xv)
        leaderboard.append({"name":name,**score,"brier":_brier(proba,yv),"log_loss":float(log_loss(yv,proba,labels=[0,1,2]))})
    leaderboard.sort(key=lambda z:(z["accuracy"],-z["brier"],-z["log_loss"]),reverse=True); chosen=leaderboard[0]["name"]
    model=dict(_models())[chosen]; model.fit(Xtr+Xv,ytr+yv); ml=_labels(model,Xt); market=_market_picks(te)
    mlacc=_acc(te,ml); macc=_acc(te,market)
    pair=Counter()
    for r,p,q in zip(te,ml,market):
        a=p==r["actual"]; b=q==r["actual"]; pair["both_correct" if a and b else "ml_only" if a else "market_only" if b else "both_wrong"]+=1
    by_comp={}
    for cid in comps:
        ids=[i for i,r in enumerate(te) if r["competition_id"]==cid]
        if not ids: continue
        mh=sum(1 for i in ids if ml[i]==te[i]["actual"]); qh=sum(1 for i in ids if market[i]==te[i]["actual"]); n=len(ids)
        by_comp[cid]={"count":n,"market_accuracy":qh/n,"ml_accuracy":mh/n,"uplift_pp":(mh-qh)/n*100.0}
    # non-overlapping 100-match blocks on untouched latest test
    idx=list(range(len(te))); import random; random.Random(SEED).shuffle(idx); full=len(idx)//100; blocks=[]; cmp=Counter()
    for b in range(full):
        ids=idx[b*100:(b+1)*100]; mh=sum(1 for i in ids if ml[i]==te[i]["actual"]); qh=sum(1 for i in ids if market[i]==te[i]["actual"]); u=mh-qh
        cmp["win" if u>0 else "tie" if u==0 else "loss"]+=1; blocks.append({"block":b+1,"market_accuracy":qh/100,"ml_accuracy":mh/100,"uplift_pp":u})
    payload={"schema_version":"V6.10.2-market-form-residual-1x2-r1","generated_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"status":"PASS","formal_current_version":"V5.0.1","market_data_classification":"RETROSPECTIVE_REFERENCE_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP","split":{"train":len(tr),"validation":len(va),"test":len(te),"chronological_within_competition":True},"numeric_features":nums,"competitions":comps,"validation_leaderboard":leaderboard,"selected_model":chosen,"untouched_latest_test":{"market":macc,"selected_ml":mlacc,"uplift_pp":(mlacc["accuracy"]-macc["accuracy"])*100.0,"paired":dict(pair)},"disjoint_100":{"blocks":blocks,"win_tie_loss":dict(cmp)},"by_competition":by_comp,"provider_counts":providers,"governance":{"research_only":True,"features_are_lagged_before_current_result":True,"formal_probability_change":False,"formal_weight_change":False,"current_rule_change":False}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(json.dumps({"selected_model":chosen,"test":payload["untouched_latest_test"],"blocks":dict(cmp),"by_competition":by_comp},ensure_ascii=False,indent=2)); return 0
if __name__=="__main__": raise SystemExit(main())
