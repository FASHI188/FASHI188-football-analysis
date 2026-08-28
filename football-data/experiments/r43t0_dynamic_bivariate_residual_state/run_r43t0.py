#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
Q0DIR = ROOT / "experiments" / "r43q0_sharp_market_score_base"
if str(Q0DIR) not in sys.path:
    sys.path.insert(0, str(Q0DIR))
import run_r43q0 as q0  # noqa: E402

LEDGER = ROOT / "forward" / "v6_market_first_events_v651.json"
OUT = HERE / "results" / "summary_r43t0_dynamic_bivariate_residual_state.json"

WARMUP_MIN = 30
FOLDS = 3
STATE_AR = 0.90
PROCESS_VAR = 0.04
INITIAL_VAR = 0.25
OBS_NOISE_FLOOR = 0.20
STATE_APPLY_SHRINK = 0.50
MAX_STATE_ABS = 1.50
BREAKTHROUGH_PP = 1.0
MIN_SCORED = 45
CLASSES = ("home", "draw", "away")


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def iso(x: str) -> datetime:
    dt = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def top1(p: dict[str, float]) -> str:
    return max(CLASSES, key=lambda k: (p[k], -CLASSES.index(k)))


def draw_cal(rows: list[dict], key: str) -> dict:
    ps = np.array([float(r[key]["draw"]) for r in rows], dtype=float)
    ys = np.array([1.0 if r["y"] == "draw" else 0.0 for r in rows], dtype=float)
    if not len(rows): return {"n": 0}
    ll = float(np.mean(-(ys*np.log(np.clip(ps,1e-15,1.0)) + (1.0-ys)*np.log(np.clip(1.0-ps,1e-15,1.0)))))
    br = float(np.mean((ps-ys)**2))
    order = np.argsort(ps); bins = np.array_split(order, min(5, len(rows))); ece = 0.0; out=[]
    for idx in bins:
        if not len(idx): continue
        mp=float(ps[idx].mean()); ar=float(ys[idx].mean()); w=len(idx)/len(rows); ece += w*abs(mp-ar)
        out.append({"n":int(len(idx)),"mean_pred":mp,"actual_rate":ar})
    return {"n":len(rows),"mean_pred":float(ps.mean()),"actual_rate":float(ys.mean()),"logloss":ll,"brier":br,"ece5":float(ece),"bins":out}


def metrics(rows: list[dict], key: str) -> dict:
    n=len(rows); hits=0; ll=br=rps=0.0
    picks={k:0 for k in CLASSES}; hit_by={k:0 for k in CLASSES}; actuals={k:0 for k in CLASSES}
    for r in rows:
        p=r[key]; y=r["y"]; t=top1(p); hits+=int(t==y); picks[t]+=1; hit_by[t]+=int(t==y); actuals[y]+=1
        ll -= math.log(max(float(p[y]),1e-15))
        br += sum((float(p[k])-(1.0 if y==k else 0.0))**2 for k in CLASSES)
        ph=float(p["home"]); pd=float(p["draw"])
        rps += ((ph-(1.0 if y=="home" else 0.0))**2 + ((ph+pd)-(1.0 if y in {"home","draw"} else 0.0))**2)/2.0
    return {"count":n,"hits":hits,"top1_accuracy":hits/n if n else None,"logloss":ll/n if n else None,"brier":br/n if n else None,"rps":rps/n if n else None,"top1_picks":picks,"top1_hits":hit_by,"actuals":actuals,"draw_calibration":draw_cal(rows,key)}


def delta(base: dict, cand: dict) -> dict:
    return {"hits":cand["hits"]-base["hits"],"accuracy_pp":100.0*(cand["top1_accuracy"]-base["top1_accuracy"]),"logloss":cand["logloss"]-base["logloss"],"brier":cand["brier"]-base["brier"],"rps":cand["rps"]-base["rps"],"draw_logloss":cand["draw_calibration"]["logloss"]-base["draw_calibration"]["logloss"],"draw_brier":cand["draw_calibration"]["brier"]-base["draw_calibration"]["brier"]}


def build_rows() -> list[dict]:
    ledger=load(LEDGER); preds={}; settled={}
    for e in ledger.get("events",[]):
        mid=str(e.get("match_id"))
        if e.get("event_type")=="MARKET_PREDICTION_FROZEN": preds[mid]=e
        elif e.get("event_type")=="RESULT_SETTLED": settled[mid]=e
    rows=[]
    for mid,se in settled.items():
        pe=preds.get(mid)
        if pe is None: continue
        fx=pe["payload"]["fixture_identity"]; surf=pe["payload"]["frozen_surfaces"]
        kickoff=iso(fx["kickoff_at"])
        if not iso(pe["event_timestamp_utc"]) < kickoff: continue
        result=se["payload"]["result"]; y=str(result["actual_result"])
        if y not in CLASSES: continue
        hg=int(result["home_goals_90"]); ag=int(result["away_goals_90"])
        market=q0.devig_1x2(surf["one_x_two_odds"])
        lh,la,obj=q0.infer_lambdas(surf["asian_handicap"],surf["over_under"],market)
        static=q0.matrix_1x2(q0.score_matrix(lh,la))
        rows.append({"match_id":mid,"kickoff_utc":kickoff.isoformat(),"competition_id":str(fx["competition_id"]),"y":y,"hg":hg,"ag":ag,"market":market,"lambda_home":lh,"lambda_away":la,"static_matrix":static,"fit_objective":obj})
    rows.sort(key=lambda r:(r["kickoff_utc"],r["match_id"]))
    return rows


def observation_cov(lh: float, la: float) -> np.ndarray:
    t=max(lh+la,0.05); d=lh-la
    r=np.array([[t,d],[d,t]],dtype=float)
    r += np.eye(2)*OBS_NOISE_FLOOR
    return r


def project_lambdas(lh: float, la: float, x: np.ndarray) -> tuple[float,float]:
    total=lh+la + STATE_APPLY_SHRINK*float(x[0])
    diff=lh-la + STATE_APPLY_SHRINK*float(x[1])
    total=max(0.20,total)
    diff=float(np.clip(diff,-total+0.10,total-0.10))
    return max(0.05,(total+diff)/2.0), max(0.05,(total-diff)/2.0)


def simultaneous_update(x_pred: np.ndarray, p_pred: np.ndarray, group: list[dict]) -> tuple[np.ndarray,np.ndarray]:
    pinv=np.linalg.inv(p_pred); info=pinv@x_pred; precision=pinv.copy()
    for r in group:
        lh=float(r["lambda_home"]); la=float(r["lambda_away"])
        z=np.array([(r["hg"]+r["ag"])-(lh+la),(r["hg"]-r["ag"])-(lh-la)],dtype=float)
        R=observation_cov(lh,la); rinv=np.linalg.inv(R)
        precision += rinv; info += rinv@z
    p_post=np.linalg.inv(precision); x_post=p_post@info
    x_post=np.clip(x_post,-MAX_STATE_ABS,MAX_STATE_ABS)
    return x_post,p_post


def group_rows(rows: list[dict]) -> list[list[dict]]:
    out=[]; cur=[]; key=None
    for r in rows:
        k=r["kickoff_utc"]
        if key is None or k==key: cur.append(r); key=k
        else: out.append(cur); cur=[r]; key=k
    if cur: out.append(cur)
    return out


def time_folds(rows: list[dict], k: int) -> list[list[dict]]:
    groups=group_rows(rows); total=len(rows); folds=[]; acc=[]; cum=0
    for g in groups:
        boundary=total*(len(folds)+1)/k
        if len(folds)<k-1 and acc and cum+len(g)>boundary:
            folds.append(acc); acc=[]
        acc.extend(g); cum+=len(g)
    if acc: folds.append(acc)
    if len(folds)!=k or any(not f for f in folds): raise RuntimeError(f"bad fold sizes {[len(f) for f in folds]}")
    return folds


def run() -> dict:
    rows=build_rows(); groups=group_rows(rows)
    x=np.zeros(2,dtype=float); P=np.eye(2)*INITIAL_VAR
    scored=[]; warmup=[]; scoring=False; state_trace=[]
    for group in groups:
        x_pred=STATE_AR*x
        P_pred=(STATE_AR**2)*P + np.eye(2)*PROCESS_VAR
        if not scoring and len(warmup)>=WARMUP_MIN:
            scoring=True
        for r in group:
            dh,da=project_lambdas(float(r["lambda_home"]),float(r["lambda_away"]),x_pred)
            r["dynamic_lambda_home"]=dh; r["dynamic_lambda_away"]=da
            r["dynamic_matrix"]=q0.matrix_1x2(q0.score_matrix(dh,da))
            r["state_total_pred"]=float(x_pred[0]); r["state_diff_pred"]=float(x_pred[1])
            (scored if scoring else warmup).append(r)
        x,P=simultaneous_update(x_pred,P_pred,group)
        state_trace.append({"kickoff_utc":group[0]["kickoff_utc"],"group_n":len(group),"x_pred":[float(v) for v in x_pred],"x_post":[float(v) for v in x],"p_diag_post":[float(P[0,0]),float(P[1,1])]})
    if len(scored)<MIN_SCORED: raise RuntimeError(f"insufficient scored {len(scored)}")
    market=metrics(scored,"market"); static=metrics(scored,"static_matrix"); dynamic=metrics(scored,"dynamic_matrix")
    dm=delta(market,dynamic); ds=delta(static,dynamic)
    fold_receipts=[]
    for i,f in enumerate(time_folds(scored,FOLDS),1):
        mm=metrics(f,"market"); sm=metrics(f,"static_matrix"); dy=metrics(f,"dynamic_matrix")
        fold_receipts.append({"fold":i,"n":len(f),"dates":[f[0]["kickoff_utc"],f[-1]["kickoff_utc"]],"market":mm,"static_matrix":sm,"dynamic_matrix":dy,"dynamic_minus_market":delta(mm,dy),"dynamic_minus_static":delta(sm,dy),"mean_abs_state_total":float(np.mean([abs(r["state_total_pred"]) for r in f])),"mean_abs_state_diff":float(np.mean([abs(r["state_diff_pred"]) for r in f]))})
    nonneg=sum(1 for f in fold_receipts if f["dynamic_minus_market"]["accuracy_pp"]>=-1e-12)
    posll=sum(1 for f in fold_receipts if f["dynamic_minus_market"]["logloss"]<0)
    gate=bool(dm["accuracy_pp"]>=0 and dm["logloss"]<0 and dm["brier"]<0 and dm["rps"]<0 and dm["draw_logloss"]<0 and dm["draw_brier"]<0 and nonneg>=2 and posll>=2 and dynamic["top1_picks"]["draw"]>0)
    result={
        "schema_version":"football3-r43t0-dynamic-bivariate-residual-state-v1","status":"COMPLETE","classification":"POSTVIEW_DEVELOPMENT_ON_EXISTING_PREMATCH_FROZEN_MARKETS","formal_weight":0,
        "question":"Can a fixed dynamic bivariate total/difference residual state update the market-implied score matrix and naturally activate better draw decisions?",
        "governance":{"prematch_market_only_before_prediction":True,"same_kickoff_results_update_each_other":False,"outcome_used_only_after_group_prediction":True,"parameter_search":False,"threshold_search":False,"draw_override":False,"draw_count_forced":False,"main_merge":False,"publication":False},
        "design":{"state_vector":["total_goal_residual","goal_difference_residual"],"transition_ar":STATE_AR,"process_var":PROCESS_VAR,"initial_var":INITIAL_VAR,"observation_noise_floor":OBS_NOISE_FLOOR,"state_apply_shrink":STATE_APPLY_SHRINK,"state_clip_abs":MAX_STATE_ABS,"observation_covariance":"Poisson implied [[T,D],[D,T]] + noise floor","warmup_min":WARMUP_MIN,"folds":FOLDS,"breakthrough_pp":BREAKTHROUGH_PP,"full_volume_target_accuracy_floor":0.53},
        "coverage":{"settled_rows":len(rows),"warmup_n":len(warmup),"scored_n":len(scored),"scored_first":scored[0]["kickoff_utc"],"scored_last":scored[-1]["kickoff_utc"]},
        "aggregate":{"direct_market":market,"static_ah_ou_matrix":static,"dynamic_bivariate_matrix":dynamic,"dynamic_minus_market":dm,"dynamic_minus_static":ds,"nonnegative_top1_folds":nonneg,"positive_logloss_folds":posll},
        "folds":fold_receipts,"state_trace_tail":state_trace[-10:],
        "gate":{"architecture_passed":gate,"full_volume_53pct_target_met":bool(dynamic["top1_accuracy"]>=0.53),"breakthrough_candidate":bool(gate and dm["accuracy_pp"]>=BREAKTHROUGH_PP),"action":"FREEZE_DYNAMIC_BIVARIATE_STATE_FOR_NEW_FORWARD_CONFIRMATION" if gate else "DO_NOT_PROMOTE_AND_DO_NOT_RETUNE_ON_THESE_SETTLED_MATCHES"}
    }
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(json.dumps(result,ensure_ascii=False,indent=2)); return result


def verify():
    x=load(OUT); g=x["governance"]
    assert x["status"]=="COMPLETE" and x["formal_weight"]==0
    assert g["prematch_market_only_before_prediction"] and g["same_kickoff_results_update_each_other"] is False and g["outcome_used_only_after_group_prediction"]
    assert g["parameter_search"] is False and g["threshold_search"] is False and g["draw_override"] is False and g["draw_count_forced"] is False
    assert x["design"]["transition_ar"]==STATE_AR and x["design"]["state_apply_shrink"]==STATE_APPLY_SHRINK
    print("R43T0 contract verified")

if __name__=="__main__":
    cmd=sys.argv[1] if len(sys.argv)>1 else "run"
    if cmd=="run": run()
    elif cmd=="verify": verify()
    else: raise SystemExit(cmd)
