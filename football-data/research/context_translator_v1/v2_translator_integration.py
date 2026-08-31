from __future__ import annotations

import importlib
import math
import sys
from pathlib import Path
from typing import Any

from translator_schema import canonical_sha,validate_translated_context


class IntegrationError(RuntimeError):pass


def _load_engine(repo_root:Path):
    p=repo_root/"football-data"/"new_engine_v2_joint_score"
    if not p.exists():raise IntegrationError(f"V2 engine path missing: {p}")
    sys.path.insert(0,str(p))
    try:return importlib.import_module("engine")
    finally:
        if sys.path and sys.path[0]==str(p):sys.path.pop(0)


def _validate_matrix(mat:list[list[float]],name:str="matrix")->float:
    if not mat or not mat[0] or any(len(r)!=len(mat[0]) for r in mat):raise IntegrationError(f"{name} invalid shape")
    vals=[float(x) for r in mat for x in r]
    if any(not math.isfinite(x) or x<0 for x in vals):raise IntegrationError(f"{name} nonfinite/negative")
    s=sum(vals)
    if s<=0:raise IntegrationError(f"{name} zero mass")
    return s


def _mix(mats:list[list[list[float]]],weights:list[float])->list[list[float]]:
    if not mats or len(mats)!=len(weights) or abs(sum(weights)-1)>1e-8 or any(w<0 for w in weights):raise IntegrationError("matrix mixture invalid")
    n=len(mats[0]);m=len(mats[0][0]);out=[[0.0]*m for _ in range(n)]
    for mat,w in zip(mats,weights):
        _validate_matrix(mat,"scenario matrix")
        if len(mat)!=n or any(len(r)!=m for r in mat):raise IntegrationError("matrix shape mismatch")
        for i in range(n):
            for j in range(m):out[i][j]+=w*float(mat[i][j])
    s=_validate_matrix(out,"mixed matrix");return [[v/s for v in row] for row in out]


def draw_structure(mat:list[list[float]])->dict[str,float]:
    s=_validate_matrix(mat);n=min(len(mat),len(mat[0]));diag=sum(float(mat[i][i]) for i in range(n))/s
    p00=float(mat[0][0])/s if n>0 else 0.0;p11=float(mat[1][1])/s if n>1 else 0.0;p22=float(mat[2][2])/s if n>2 else 0.0
    return {"natural_draw":diag,"p_0_0":p00,"p_1_1":p11,"p_2_2":p22,"other_draw":max(0.0,diag-p00-p11-p22)}


def fit_independent_head(rows:list[dict[str,Any]],*,iterations:int=500,lr:float=0.02,ridge:float=4.0)->list[list[float]]:
    w=[[0.0]*5 for _ in range(3)]
    for _ in range(iterations):
        g=[[0.0]*5 for _ in range(3)]
        for r in rows:
            mh=max(float(r["mu_home"]),1e-8);ma=max(float(r["mu_away"]),1e-8);x=[1.0,math.log(mh/ma),mh+ma-2.6,float(r.get("context_delta",0.0)),float(r.get("uncertainty",0.0))]
            logits=[sum(a*b for a,b in zip(row,x)) for row in w];mx=max(logits);ex=[math.exp(v-mx) for v in logits];ss=sum(ex);p=[v/ss for v in ex]
            y=[0.0,0.0,0.0];y[int(r["target_class"])]=1.0
            for c in range(3):
                for j in range(5):g[c][j]+=(p[c]-y[c])*x[j]
        n=max(len(rows),1)
        for c in range(3):
            for j in range(5):g[c][j]=g[c][j]/n+ridge*w[c][j]/n;w[c][j]-=lr*g[c][j]
    return w


def head_predict(mu_home:float,mu_away:float,context_delta:float,uncertainty:float,weights:list[list[float]])->dict[str,float]:
    if mu_home<=0 or mu_away<=0:raise IntegrationError("head intensities must be positive")
    x=[1.0,math.log(mu_home/mu_away),mu_home+mu_away-2.6,context_delta,uncertainty];logits=[sum(a*b for a,b in zip(row,x)) for row in weights];mx=max(logits);ex=[math.exp(v-mx) for v in logits];s=sum(ex)
    return {"home":ex[0]/s,"draw":ex[1]/s,"away":ex[2]/s}


def integrate_plan(plan:dict[str,Any],v2_lock:dict[str,Any],*,repo_root:Path,head_weights:list[list[float]]|None=None)->dict[str,Any]:
    eng=_load_engine(repo_root);mats=[];heads=[];weights=[];scenario_outputs=[]
    for item in plan["scenarios"]:
        sc=item["scenario"];p=float(sc.probability);weights.append(p)
        feat={"mu_home":float(item["translated_mu_home"]),"mu_away":float(item["translated_mu_away"]),"home_evidence":10.0,"away_evidence":10.0}
        mat=eng.joint_matrix(v2_lock["joint_family"],feat,dispersion_home=float(v2_lock.get("dispersion_home",50.0)),dispersion_away=float(v2_lock.get("dispersion_away",50.0)),dependence=float(v2_lock["dependence"]),max_goals=int(v2_lock["max_goals"]))
        s=_validate_matrix(mat,"scenario matrix");mat=[[float(v)/s for v in row] for row in mat];mats.append(mat)
        if head_weights is None:head=eng.matrix_1x2(mat)
        else:
            cd=float(item["deltas"]["context_home"]-item["deltas"]["context_away"]+item["deltas"].get("coach_home",0)-item["deltas"].get("coach_away",0)+item["deltas"].get("process_home",0)-item["deltas"].get("process_away",0))
            head=head_predict(item["translated_mu_home"],item["translated_mu_away"],cd,item["lineup_uncertainty"],head_weights)
        if abs(sum(head.values())-1)>1e-9:raise IntegrationError("head probability conservation failed")
        heads.append(head);scenario_outputs.append({"scenario_id":sc.scenario_id,"probability":p,"base_mu_home":item["base_mu_home"],"base_mu_away":item["base_mu_away"],"translated_mu_home":item["translated_mu_home"],"translated_mu_away":item["translated_mu_away"],"score_matrix_sha256":canonical_sha(mat)})
    mix=_mix(mats,weights);target={k:sum(w*h[k] for w,h in zip(weights,heads)) for k in ("home","draw","away")}
    if abs(sum(target.values())-1)>1e-9:raise IntegrationError("mixed head probability conservation failed")
    final=eng.kl_project_to_1x2(mix,target);s=_validate_matrix(final,"projected matrix");final=[[float(v)/s for v in row] for row in final];final_1x2=eng.matrix_1x2(final)
    if any(v<0 or not math.isfinite(v) for v in final_1x2.values()) or abs(sum(final_1x2.values())-1)>1e-8:raise IntegrationError("final 1X2 invalid")
    draw=draw_structure(final)
    if abs(draw["natural_draw"]-float(final_1x2["draw"]))>1e-8:raise IntegrationError("matrix-integrated draw != final 1X2 draw")
    unc_components={"lineup":sum(w*i["lineup_uncertainty"] for w,i in zip(weights,plan["scenarios"])),"coach":plan["coach_tactical"].uncertainty,"context":plan["match_context"].uncertainty,"process":plan["process_hazard"].uncertainty}
    obj={"schema_version":"football3.context_translator.v1","research_status":"RESEARCH_ONLY","match_id":plan["match_id"],"cutoff":plan["cutoff"],"coverage_grade":plan["coverage_grade"],"provenance_manifest_sha256":plan["provenance_manifest_sha256"],"team_state":plan["team_state"],"player_state":plan["player_state"],"lineup_scenarios":[i["scenario"].schema_dict() for i in plan["scenarios"]],"coach_tactical":plan["coach_tactical"].schema_dict(),"match_context":plan["match_context"].schema_dict(),"process_hazard":plan["process_hazard"].schema_dict(),"scenario_outputs":scenario_outputs,"uncertainty":{"total":sum(unc_components.values()),"components":unc_components}}
    validate_translated_context(obj);feature_sha=canonical_sha(obj)
    return {"translated_context":obj,"final_matrix":final,"final_1x2":final_1x2,"independent_head":target,"draw_structure":draw,"feature_sha256":feature_sha,"final_prediction_sha256":canonical_sha({"matrix":final,"one_x_two":final_1x2})}
