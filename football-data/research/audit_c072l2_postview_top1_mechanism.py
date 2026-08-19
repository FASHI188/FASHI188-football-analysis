#!/usr/bin/env python3
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

import evaluate_c072e2_ou25_movement_directt as e2
import evaluate_c072k2_joint_low_score_confirm as k2
import run_c072h2_dgiven_t_development as fixed_h2

h2=fixed_h2.ns


def stats(a):
    x=np.asarray(a,float)
    return {"n":int(len(x)),"mean":float(np.mean(x)),"median":float(np.median(x)),"p10":float(np.quantile(x,.10)),"p25":float(np.quantile(x,.25)),"p75":float(np.quantile(x,.75)),"p90":float(np.quantile(x,.90))}


def call_concentration(pred,names):
    c=Counter(names[int(i)] for i in pred); n=sum(c.values()); probs=np.asarray([v/n for v in c.values()],float)
    ent=float(-np.sum(probs*np.log(np.clip(probs,1e-15,1))))
    return {"n":n,"distinct_called":len(c),"entropy_nats":ent,"effective_called_classes":float(np.exp(ent)),"top1_call_counts":dict(c.most_common())}


def main():
    # Exact frozen K2 model reconstruction; no parameter choice occurs here.
    pt_frame,_=e2.load_source(); pt_feat=e2.build_rows(pt_frame); pt_train=pt_feat[pt_feat.eligible].copy(); ypt=pt_train.target.to_numpy(int)
    pt_ref=e2.pipeline(); pt_cand=e2.pipeline(); pt_ref.fit(pt_train[e2.REF],ypt); pt_cand.fit(pt_train[e2.CAND],ypt)

    d_frame=h2["load_source"](); d_feat=h2["build_rows"](d_frame); d_train=d_feat[d_feat.eligible & d_feat["T"].isin([1,2,3,4,5,6])].copy(); d_tabs=h2["baseline_tables"](d_train); d_models={}
    for total in range(1,7):
        tr=d_train[d_train["T"]==total].copy(); m=h2["pipe"](); m.fit(tr[h2["OPEN"]],tr.H.to_numpy(int)); d_models[total]=m

    raws={d:k2.fetch("2526",d) for d in k2.DIVS}
    ident=pd.concat([k2.identity(raws[d],d) for d in k2.DIVS],ignore_index=True)
    if len(ident)!=k2.EXPECTED_IDENTITIES:
        raise RuntimeError("J2 identity drift")
    w=pd.concat([k2.warmup(k2.fetch("2425",d),d) for d in k2.DIVS],ignore_index=True)
    t=pd.concat([k2.target(raws[d],d) for d in k2.DIVS],ignore_index=True)
    z=k2.build_rows(w,t); z=z[z.eligible].copy().sort_values(["date","division","home","away"]).reset_index(drop=True)

    pref=e2.predict8(pt_ref,z[e2.REF]); pcand=e2.predict8(pt_cand,z[e2.CAND])
    dbase={}; dcand={}
    for total in range(1,7):
        dbase[total]=np.tile(d_tabs[total],(len(z),1)); dcand[total]=h2["support_predict"](d_models[total],z[h2["OPEN"]],total)

    def joint(pt,dtype):
        p=np.zeros((len(z),29),float); p[:,k2.CELL_INDEX[(0,0)]]=pt[:,0]
        for total in range(1,7):
            dd=dbase[total] if dtype=="emp" else dcand[total]
            for home in range(total+1): p[:,k2.CELL_INDEX[(home,total-home)]]=pt[:,total]*dd[:,home]
        p[:,k2.TAIL_INDEX]=pt[:,7]
        return p/p.sum(1,keepdims=True)

    mats={"BASE":joint(pref,"emp"),"PT_ONLY":joint(pcand,"emp"),"D_ONLY":joint(pref,"cand"),"BOTH":joint(pcand,"cand")}
    low=z["total"].to_numpy(int)<=6
    low_mats={}
    for name,p in mats.items():
        q=p[low,:28].copy(); low_mats[name]=q/q.sum(1,keepdims=True)

    # P(T) mechanism.
    pt_names=["0","1","2","3","4","5","6","7+"]
    actual_tot=np.minimum(z["total"].to_numpy(int),7)
    pt_diag={
        "reference_top1_counts":dict(Counter(pt_names[int(i)] for i in np.argmax(pref,axis=1)).most_common()),
        "candidate_top1_counts":dict(Counter(pt_names[int(i)] for i in np.argmax(pcand,axis=1)).most_common()),
        "actual_total_counts":dict(Counter(pt_names[int(i)] for i in actual_tot).most_common()),
        "candidate_probabilities":{f"T{k}":stats(pcand[:,k]) for k in range(4)},
        "reference_probabilities":{f"T{k}":stats(pref[:,k]) for k in range(4)},
    }

    # T=2 allocation mechanism, evaluated as a conditional distribution for all eligible rows.
    p2=dcand[2]
    alloc2={
        "H1_conditional_top1_rate":float(np.mean(np.argmax(p2,axis=1)==1)),
        "P_H0":stats(p2[:,0]),"P_H1":stats(p2[:,1]),"P_H2":stats(p2[:,2]),
    }

    names=k2.CELL_NAMES[:28]
    pred_both=np.argmax(low_mats["BOTH"],axis=1); pred_base=np.argmax(low_mats["BASE"],axis=1)
    idx11=names.index("1-1")
    one11=np.flatnonzero(pred_both==idx11)
    q=low_mats["BOTH"][one11]
    sorted_idx=np.argsort(-q,axis=1); ptop=q[np.arange(len(q)),sorted_idx[:,0]]; psecond=q[np.arange(len(q)),sorted_idx[:,1]]; margins=ptop-psecond
    runnerups=[names[int(i)] for i in sorted_idx[:,1]]
    low_rows=z.loc[low].reset_index(drop=True)
    pt_top_low=np.argmax(pcand[low],axis=1)
    call11={
        "calls":int(len(one11)),
        "share_of_low_rows":float(len(one11)/len(low_rows)),
        "pt_top1_is_T2_share":float(np.mean(pt_top_low[one11]==2)) if len(one11) else None,
        "top_probability":stats(ptop) if len(one11) else None,
        "margin_over_second":stats(margins) if len(one11) else None,
        "margin_share_lt_0_005":float(np.mean(margins<.005)) if len(one11) else None,
        "margin_share_lt_0_01":float(np.mean(margins<.01)) if len(one11) else None,
        "margin_share_lt_0_02":float(np.mean(margins<.02)) if len(one11) else None,
        "margin_share_lt_0_05":float(np.mean(margins<.05)) if len(one11) else None,
        "runner_up_counts":dict(Counter(runnerups).most_common()),
    }

    concentration={name:call_concentration(np.argmax(p,axis=1),names) for name,p in low_mats.items()}

    out={
        "schema":"C072L2_POSTVIEW_TOP1_MECHANISM_AUDIT_V1",
        "terminal":"POSTVIEW_DIAGNOSTIC_COMPLETE",
        "scientific_confirmation":False,
        "eligible_hybrid_rows":int(len(z)),"eligible_low_rows":int(np.sum(low)),
        "P_T":pt_diag,
        "T2_allocation":alloc2,
        "BOTH_1_1_top1":call11,
        "exact_score_call_concentration":concentration,
        "boundary":{"model_recipe_changed":False,"tuning_performed":False,"score_boost":False,"C073_C077_quarantined":True,"C070F_confirmation1597_opened":False,"protected_opened":False,"formal_weight":0}
    }
    Path("football-data/research/c072l2_top1_mechanism_summary.json").write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
