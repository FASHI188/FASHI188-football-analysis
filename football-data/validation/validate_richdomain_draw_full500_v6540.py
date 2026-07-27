#!/usr/bin/env python3
"""V6.54.0 rich-domain historical gate for the Full500 target domain.

Rationale: V6.49.3 deliberately selected a high-completeness target domain, while prior
historical gates evaluated all eligible league matches. This validator constructs a
result-blind Rich500 analogue inside each historical validation season before model/rule
selection. No outcomes are used in domain membership.

Historical richness dimensions available consistently in V6.51 rows:
- number of complete individual closing 1X2 bookmakers (rich-market extra field);
- xG minimum history depth (base feature xg_min_history_scaled);
- panel minimum history depth (base feature panel_min_n_scaled).
For 2023/24 and 2024/25, rank each season by mean within-season percentile across these
three dimensions and freeze the top 500 as the evaluation domain.

Then run the same draw-only intervention family:
- train on 2022/23, select model/rule on 2023/24 Rich500;
- retrain on 2022/23+2023/24, validate fixed model/rule on 2024/25 Rich500;
- A100 may open only if the Rich500 holdout uplift >= +0.5pp and selection uplift >0;
- no A100 label is used in historical domain construction or rule selection;
- B300/C100 never read.
"""
from __future__ import annotations

import bisect
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from catboost import CatBoostClassifier

ROOT=Path(__file__).resolve().parents[1]
for p in (ROOT/"validation",ROOT/"engine"):
    if str(p) not in sys.path: sys.path.insert(0,str(p))

import validate_rich_market_catboost_full500_v6510 as v651  # noqa: E402

OUT=ROOT/"manifests"/"v6_richdomain_draw_full500_v6540_status.json"
RICH_N=500
DEPTHS=(4,5,6)
DRAW_WEIGHTS=(1.0,1.25,1.5,1.75,2.0)
DRAW_P_THRESHOLDS=(0.30,0.325,0.35,0.375,0.40,0.425,0.45,0.475,0.50)
MARKET_MARGIN_CAPS=(0.05,0.10,0.15,0.20,0.30,1.00)
MIN_SELECTION_OVERRIDES=8
MIN_HOLDOUT_OVERRIDES=8
HOLDOUT_REQUIRED_UPLIFT_PP=0.5
BASE_XG_INDEX=27
BASE_PANEL_INDEX=47
EXTRA_BOOKCOUNT_FROM_END=3


def pct(vals:list[float],x:float)->float:
    l=bisect.bisect_left(vals,x); r=bisect.bisect_right(vals,x)
    return (l+r)/(2.0*len(vals))


def richness_fields(r:dict[str,Any])->tuple[float,float,float]:
    x=r["x"]
    if len(x)<56: raise RuntimeError(f"unexpected rich feature length {len(x)}")
    return float(x[-EXTRA_BOOKCOUNT_FROM_END]),float(x[BASE_XG_INDEX]),float(x[BASE_PANEL_INDEX])


def rich_subset(rows:list[dict[str,Any]],season:str)->tuple[list[dict[str,Any]],dict[str,Any]]:
    rs=[r for r in rows if r["season"]==season]
    if len(rs)<RICH_N: raise RuntimeError(f"{season} rows below Rich500: {len(rs)}")
    raw=[richness_fields(r) for r in rs]
    cols=[sorted(v[j] for v in raw) for j in range(3)]
    scored=[]
    for r,v in zip(rs,raw):
        q=[pct(cols[j],v[j]) for j in range(3)]
        z=dict(r); z["rich_domain_score"]=float(np.mean(q)); z["rich_domain_fields"]={"book_count":v[0],"xg_history":v[1],"panel_history":v[2]}
        scored.append(z)
    scored.sort(key=lambda z:(-z["rich_domain_score"],-z["rich_domain_fields"]["book_count"],-z["rich_domain_fields"]["xg_history"],z["competition_id"],z["date"],z["home_team"],z["away_team"]))
    selected=scored[:RICH_N]
    def avg(k): return float(np.mean([x["rich_domain_fields"][k] for x in selected]))
    return selected,{"season":season,"source_rows":len(rs),"selected":len(selected),"book_count_mean":avg("book_count"),"xg_history_mean":avg("xg_history"),"panel_history_mean":avg("panel_history"),"competition_counts":dict(Counter(x["competition_id"] for x in selected)),"result_used_for_selection":False}


def fit(train:list[dict[str,Any]],depth:int,dw:float)->CatBoostClassifier:
    x=np.asarray([r["x"] for r in train],float); y=np.asarray([r["y"] for r in train],int); sw=np.where(y==1,dw,1.0)
    m=CatBoostClassifier(loss_function="MultiClass",iterations=v651.ITERATIONS,depth=depth,learning_rate=v651.LEARNING_RATE,l2_leaf_reg=v651.L2,random_seed=6540,verbose=False,allow_writing_files=False,thread_count=-1)
    m.fit(x,y,sample_weight=sw); return m


def evaluate(rows:list[dict[str,Any]],model:CatBoostClassifier,thr:float,cap:float)->dict[str,Any]:
    x=np.asarray([r["x"] for r in rows],float); y=np.asarray([r["y"] for r in rows],int); market=np.asarray([r["market"] for r in rows],float); p=np.asarray(model.predict_proba(x),float)
    mp=market.argmax(1); margin=np.sort(market,axis=1)[:,-1]-np.sort(market,axis=1)[:,-2]
    ov=(mp!=1)&(p[:,1]>=thr-1e-12)&(margin<=cap+1e-12); cp=mp.copy(); cp[ov]=1
    mh=int(np.sum(mp==y)); ch=int(np.sum(cp==y)); wins=int(np.sum(ov&(y==1))); losses=int(np.sum(ov&(mp==y))); neutral=int(ov.sum())-wins-losses
    return {"count":len(rows),"market_hits":mh,"candidate_hits":ch,"market_top1":mh/len(rows),"candidate_top1":ch/len(rows),"uplift_pp":100*(ch-mh)/len(rows),"overrides":int(ov.sum()),"override_wins":wins,"override_losses":losses,"override_neutral":neutral,"net_override_gain":wins-losses,"predicted_counts":dict(Counter(str(int(z)) for z in cp)),"actual_counts":dict(Counter(str(int(z)) for z in y)),"proper_scores":"identical_to_market_by_construction"}


def load_a_labels()->np.ndarray:
    out=[]
    with v651.LABELS.open("r",encoding="utf-8") as h:
        for _ in range(100):
            r=json.loads(h.readline())
            if r.get("partition")!=v651.PART or int(r["full_index"])!=len(out): raise RuntimeError("A100 label contract changed")
            out.append(int(r["label"]))
    return np.asarray(out,int)


def main()->int:
    hist,audit=v651.build_historical(); sel,sel_domain=rich_subset(hist,"2023/24"); hold,hold_domain=rich_subset(hist,"2024/25")
    train1=[r for r in hist if r["season"]=="2022/23"]
    board=[]
    for d in DEPTHS:
        for dw in DRAW_WEIGHTS:
            m=fit(train1,d,dw)
            for thr in DRAW_P_THRESHOLDS:
                for cap in MARKET_MARGIN_CAPS:
                    met=evaluate(sel,m,thr,cap)
                    if met["overrides"]>=MIN_SELECTION_OVERRIDES: board.append({"depth":d,"draw_weight":dw,"draw_p_threshold":thr,"market_margin_cap":cap,"selection":met})
    if not board: raise RuntimeError("no rich-domain selection rule support")
    board.sort(key=lambda z:(z["selection"]["net_override_gain"],z["selection"]["uplift_pp"],-z["selection"]["overrides"],z["draw_p_threshold"],-z["market_margin_cap"]),reverse=True); chosen=board[0]
    train2=[r for r in hist if r["season"] in {"2022/23","2023/24"}]; m2=fit(train2,int(chosen["depth"]),float(chosen["draw_weight"])); hm=evaluate(hold,m2,float(chosen["draw_p_threshold"]),float(chosen["market_margin_cap"]))
    hist_gate=bool(chosen["selection"]["uplift_pp"]>0 and hm["uplift_pp"]>=HOLDOUT_REQUIRED_UPLIFT_PP-1e-12 and hm["overrides"]>=MIN_HOLDOUT_OVERRIDES)
    payload:dict[str,Any]={"schema_version":"V6.54.0-richdomain-draw-full500-r1","status":"PASS","formal_current_version":"V5.0.1","formal_weight":0,"governance":{"historical_domain_membership_result_blind":True,"selection_season":"2023/24_Rich500","holdout_season":"2024/25_Rich500","A100_values_used_for_selection":False,"probability_vector_modified":False,"B_CONFIRM300_labels_read":False,"C_SEALED100_labels_read":False,"CURRENT_unchanged":True},"historical_audit":audit,"rich_domains":{"selection":sel_domain,"holdout":hold_domain},"grid":{"depths":DEPTHS,"draw_weights":DRAW_WEIGHTS,"draw_p_thresholds":DRAW_P_THRESHOLDS,"market_margin_caps":MARKET_MARGIN_CAPS},"selected_rule":chosen,"holdout_2024_25_rich500":hm,"historical_gate":hist_gate,"leaderboard_top10":board[:10]}
    if not hist_gate:
        payload["A_FAST100"]={"status":"NOT_OPENED_RICHDOMAIN_HOLDOUT_GATE_FAILED"}; payload["next_step"]="DO_NOT_OPEN_B300"; OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(json.dumps(payload,ensure_ascii=False,indent=2)); return 0
    arows,aa=v651.load_a100_features(); y=load_a_labels(); ar=[]
    for r,yy in zip(arows,y): z=dict(r); z["y"]=int(yy); ar.append(z)
    mall=fit(hist,int(chosen["depth"]),float(chosen["draw_weight"])); am=evaluate(ar,mall,float(chosen["draw_p_threshold"]),float(chosen["market_margin_cap"]))
    gate={"required_candidate_hits":63,"required_uplift_vs_market_pp":3.0,"candidate_hits":am["candidate_hits"],"market_hits":am["market_hits"],"uplift_vs_market_pp":am["uplift_pp"],"top1_gate":am["candidate_hits"]>=63,"uplift_gate":am["uplift_pp"]>=3.0-1e-12,"proper_score_guard":True}; gate["A_FAST100_passed"]=bool(gate["top1_gate"] and gate["uplift_gate"])
    payload["A_FAST100"]={"status":"SCORED_AFTER_RICHDOMAIN_HISTORICAL_GATE","feature_audit":aa,"metrics":am,"gate":gate}; payload["next_step"]="OPEN_B_CONFIRM300" if gate["A_FAST100_passed"] else "DO_NOT_OPEN_B300"; OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(json.dumps(payload,ensure_ascii=False,indent=2)); return 0


if __name__=="__main__": raise SystemExit(main())
