#!/usr/bin/env python3
from __future__ import annotations

import csv, json, math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from audit_v510_existing_score_market_pit_ledger_r1 import field_name, float_value, valid_price
from diagnose_fixed500_existing_market_pack_t_r1 import (
    add_identity_key, devig, logit, materialize_market,
)
from evaluate_direct_t_gd_joint_fixed200_r1 import KEYS, load_config
from evaluate_direct_t_parity_gd_fixed500_r1 import attach_exact_total, load_experiment, paired_bootstrap, sample_fixed_n
from v510_historical_structure_features_r1 import (
    ResearchError, assign_fold, audit_data_identity, build_features, complete_seasons, select_core_features,
)
from v510_historical_structure_model_r1 import align_probability, make_model, select_C

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "fixed500_even_t_draw_vs_pm2_r3.json"
ROWS_OUT = ROOT / "manifests" / "fixed500_even_t_draw_vs_pm2_r3_rows.csv"
TARGET_TOTALS = (2,4,6)

X12_SOURCES = {
    "ps": (("PSH","PSD","PSA"),("PSCH","PSCD","PSCA")),
    "b365": (("B365H","B365D","B365A"),("B365CH","B365CD","B365CA")),
    "avg": (("AvgH","AvgD","AvgA"),("AvgCH","AvgCD","AvgCA")),
    "max": (("MaxH","MaxD","MaxA"),("MaxCH","MaxCD","MaxCA")),
    "bfe": (("BFEH","BFED","BFEA"),("BFECH","BFECD","BFECA")),
    "bfd": (("BFDH","BFDD","BFDA"),("BFDCH","BFDCD","BFDCA")),
    "mgm": (("BMGMH","BMGMD","BMGMA"),("BMGMCH","BMGMCD","BMGMCA")),
    "bv": (("BVH","BVD","BVA"),("BVCH","BVCD","BVCA")),
    "bw": (("BWH","BWD","BWA"),("BWCH","BWCD","BWCA")),
    "cl": (("CLH","CLD","CLA"),("CLCH","CLCD","CLCA")),
    "lb": (("LBH","LBD","LBA"),("LBCH","LBCD","LBCA")),
}
AH_SOURCES = {
    "ps": (("PAHH","PAHA"),("PCAHH","PCAHA")),
    "b365": (("B365AHH","B365AHA"),("B365CAHH","B365CAHA")),
    "avg": (("AvgAHH","AvgAHA"),("AvgCAHH","AvgCAHA")),
    "max": (("MaxAHH","MaxAHA"),("MaxCAHH","MaxCAHA")),
    "bfe": (("BFEAHH","BFEAHA"),("BFECAHH","BFECAHA")),
}


def triplet(row: dict[str,Any], headers: list[str], aliases: tuple[str,str,str]) -> tuple[np.ndarray,float] | None:
    fs=[field_name(headers,[x]) for x in aliases]
    if not all(fs): return None
    vals=[float_value(row,f) for f in fs]
    if not all(valid_price(v) for v in vals): return None
    inv=np.asarray([1.0/float(v) for v in vals]); p=inv/inv.sum()
    return p,float(inv.sum()-1.0)


def pair(row: dict[str,Any], headers: list[str], aliases: tuple[str,str]) -> tuple[np.ndarray,float] | None:
    fs=[field_name(headers,[x]) for x in aliases]
    if not all(fs): return None
    vals=[float_value(row,f) for f in fs]
    if not all(valid_price(v) for v in vals): return None
    inv=np.asarray([1.0/float(v) for v in vals]); p=inv/inv.sum()
    return p,float(inv.sum()-1.0)


def consensus_x12(rec: dict[str,Any], stage: str, probs: list[np.ndarray], margins: list[float]) -> None:
    if not probs:
        rec[f"side_{stage}_source_count"]=0.0; return
    a=np.vstack(probs); draw=a[:,1]; balance=np.abs(a[:,0]-a[:,2]); ha=a[:,0]-a[:,2]
    rec[f"side_{stage}_source_count"]=float(len(a))
    rec[f"side_{stage}_draw_mean"]=float(draw.mean())
    rec[f"side_{stage}_draw_logit"]=logit(float(draw.mean()))
    rec[f"side_{stage}_draw_std"]=float(draw.std(ddof=0))
    rec[f"side_{stage}_draw_range"]=float(draw.max()-draw.min())
    rec[f"side_{stage}_balance_abs_mean"]=float(balance.mean())
    rec[f"side_{stage}_ha_diff_mean"]=float(ha.mean())
    rec[f"side_{stage}_margin_mean"]=float(np.mean(margins)) if margins else np.nan


def consensus_ah(rec: dict[str,Any], stage: str, probs: list[float], margins: list[float]) -> None:
    if not probs:
        rec[f"ah_{stage}_source_count"]=0.0; return
    x=np.asarray(probs,float)
    rec[f"ah_{stage}_source_count"]=float(len(x))
    rec[f"ah_{stage}_home_mean"]=float(x.mean())
    rec[f"ah_{stage}_home_logit"]=logit(float(x.mean()))
    rec[f"ah_{stage}_home_std"]=float(x.std(ddof=0))
    rec[f"ah_{stage}_margin_mean"]=float(np.mean(margins)) if margins else np.nan


def materialize_rich_side(ledger: pd.DataFrame) -> pd.DataFrame:
    keyed=add_identity_key(ledger); wanted: dict[str,dict[int,str]]={}
    for r in keyed[["source_file","row_number","identity_key"]].itertuples(index=False):
        wanted.setdefault(str(r.source_file),{})[int(r.row_number)]=str(r.identity_key)
    records=[]
    for source_file in sorted(wanted):
        path=ROOT/source_file
        if not path.is_file(): continue
        with path.open("r",encoding="utf-8-sig",newline="") as h:
            reader=csv.DictReader(h); headers=list(reader.fieldnames or [])
            for row_number,row in enumerate(reader,start=2):
                identity=wanted[source_file].get(row_number)
                if identity is None: continue
                rec: dict[str,Any]={"identity_key":identity}
                op=[]; cp=[]; om=[]; cm=[]
                for source,(oa,ca) in X12_SOURCES.items():
                    o=triplet(row,headers,oa); c=triplet(row,headers,ca)
                    if o is not None:
                        p,m=o; op.append(p); om.append(m)
                        rec[f"side_{source}_open_draw_logit"]=logit(float(p[1])); rec[f"side_{source}_open_balance_abs"]=float(abs(p[0]-p[2]))
                    if c is not None:
                        p,m=c; cp.append(p); cm.append(m)
                        rec[f"side_{source}_close_draw_logit"]=logit(float(p[1])); rec[f"side_{source}_close_balance_abs"]=float(abs(p[0]-p[2]))
                    if o is not None and c is not None:
                        rec[f"side_{source}_draw_shift"]=float(c[0][1]-o[0][1])
                        rec[f"side_{source}_balance_shift"]=float(abs(c[0][0]-c[0][2])-abs(o[0][0]-o[0][2]))
                consensus_x12(rec,"open",op,om); consensus_x12(rec,"close",cp,cm)
                if op and cp:
                    rec["side_consensus_draw_shift"]=float(np.mean([x[1] for x in cp])-np.mean([x[1] for x in op]))
                    rec["side_consensus_balance_shift"]=float(np.mean([abs(x[0]-x[2]) for x in cp])-np.mean([abs(x[0]-x[2]) for x in op]))

                open_line=float_value(row,field_name(headers,["AHh","asian_handicap_line","handicap_line","spread_line"]))
                close_line=float_value(row,field_name(headers,["AHCh","closing_handicap_line"]))
                if open_line is not None: rec["ah_open_line"]=float(open_line)
                if close_line is not None: rec["ah_close_line"]=float(close_line)
                if open_line is not None and close_line is not None: rec["ah_line_shift"]=float(close_line-open_line)
                ah_op=[]; ah_cp=[]; ah_om=[]; ah_cm=[]
                for source,(oa,ca) in AH_SOURCES.items():
                    o=pair(row,headers,oa); c=pair(row,headers,ca)
                    if o is not None:
                        p,m=o; ah_op.append(float(p[0])); ah_om.append(m); rec[f"ah_{source}_open_home_logit"]=logit(float(p[0]))
                    if c is not None:
                        p,m=c; ah_cp.append(float(p[0])); ah_cm.append(m); rec[f"ah_{source}_close_home_logit"]=logit(float(p[0]))
                    if o is not None and c is not None: rec[f"ah_{source}_home_shift"]=float(c[0][0]-o[0][0])
                consensus_ah(rec,"open",ah_op,ah_om); consensus_ah(rec,"close",ah_cp,ah_cm)
                if ah_op and ah_cp: rec["ah_consensus_home_shift"]=float(np.mean(ah_cp)-np.mean(ah_op))
                if len(rec)>1: records.append(rec)
    if not records: raise ResearchError("no rich side rows")
    return pd.DataFrame(records).sort_values("identity_key").drop_duplicates("identity_key").reset_index(drop=True)


def binary_metrics(y: np.ndarray, p_draw: np.ndarray) -> dict[str,float]:
    y=np.asarray(y,int); p=np.clip(np.asarray(p_draw,float),1e-15,1-1e-15); pred=(p>=0.5).astype(int)
    ll=-(y*np.log(p)+(1-y)*np.log(1-p)); b=(p-y)**2
    tp=int(np.sum((pred==1)&(y==1))); fp=int(np.sum((pred==1)&(y==0))); fn=int(np.sum((pred==0)&(y==1)))
    pr=tp/(tp+fp) if tp+fp else 0.0; rc=tp/(tp+fn) if tp+fn else 0.0; f1=2*pr*rc/(pr+rc) if pr+rc else 0.0
    return {"accuracy":float(np.mean(pred==y)),"logloss":float(ll.mean()),"brier":float(b.mean()),"auc":float(roc_auc_score(y,p)) if len(np.unique(y))==2 else float("nan"),"draw_precision":pr,"draw_recall":rc,"draw_f1":f1,"draw_calls":int(pred.sum()),"draw_hits":tp}


def fit_by_total(fold: pd.DataFrame, target: pd.DataFrame, features: list[str], config: dict[str,Any]) -> tuple[np.ndarray,dict[str,Any]]:
    out=pd.Series(index=target.index,dtype=float); receipt={}
    for total in TARGET_TOTALS:
        mask_support=fold.goal_difference.isin([-2,0,2])
        train=fold[(fold.split=="train")&(fold.total_class==total)&mask_support].copy()
        policy=fold[(fold.split=="policy")&(fold.total_class==total)&mask_support].copy()
        fit=fold[(fold.split.isin(["train","policy"]))&(fold.total_class==total)&mask_support].copy()
        test=target[target.total_class==total].copy()
        for f in (train,policy,fit,test): f["draw_binary"]=(f.goal_difference==0).astype(int)
        if len(test)==0: continue
        if min(train.draw_binary.nunique(),policy.draw_binary.nunique())<2: raise ResearchError(f"binary class missing for T={total}")
        c,grid=select_C(train,policy,features,"draw_binary",[0,1],config)
        model=make_model(c,config); model.fit(fit[features],fit.draw_binary)
        p=align_probability(model,test[features],[0,1])[:,1]
        out.loc[test.index]=p
        receipt[str(total)]={"fit_rows":int(len(fit)),"train_rows":int(len(train)),"policy_rows":int(len(policy)),"test_rows":int(len(test)),"draw_rate_fit":float(fit.draw_binary.mean()),"selected_C":c,"policy_grid":grid}
    if out.isna().any(): raise ResearchError("missing conditional draw probability")
    return out.to_numpy(float),receipt


def run() -> dict[str,Any]:
    exp=load_experiment(); config=load_config(); raw=pd.read_csv(ROOT/str(config["input_ledger"])); data_identity=audit_data_identity(raw,config)
    base=add_identity_key(build_features(raw)); core=select_core_features(base); seasons,excluded=complete_seasons(raw,config)
    pos=int(exp["test_position_zero_based"]); latest=max(int(x) for x in config["split_contract"]["rolling_test_positions_zero_based"])
    if pos>=latest: raise ResearchError("must reuse PR197 non-latest fixed500")
    base["split"]=assign_fold(base,seasons,pos); sample_base,sample_hash=sample_fixed_n(base[base.split=="test"].copy(),int(exp["sample_n"]))
    fold=attach_exact_total(base,raw); fold=fold.merge(materialize_market(raw),on="identity_key",how="left",validate="one_to_one"); fold=fold.merge(materialize_rich_side(raw),on="identity_key",how="left",validate="one_to_one")
    sample=fold.merge(sample_base[KEYS+["match_identity","identity_hash"]],on=KEYS,how="inner",validate="one_to_one")
    if len(sample)!=500: raise ResearchError("fixed500 mismatch")

    side_mask=fold[["mkt_draw_logit","mkt_home_minus_away"]].notna().all(axis=1)
    sync_mask=fold[["mkt_draw_logit","mkt_home_minus_away","mkt_ah_line","mkt_ah_home_logit"]].notna().all(axis=1)
    support=lambda f: f.total_class.isin(TARGET_TOTALS)&f.goal_difference.isin([-2,0,2])
    side_target=sample[support(sample)&sample[["mkt_draw_logit","mkt_home_minus_away"]].notna().all(axis=1)].copy()
    sync_target=sample[support(sample)&sample[["mkt_draw_logit","mkt_home_minus_away","mkt_ah_line","mkt_ah_home_logit"]].notna().all(axis=1)].copy()
    if min(len(side_target),len(sync_target))<40: raise ResearchError(f"target cohorts too small side={len(side_target)} sync={len(sync_target)}")

    x12_consensus=["side_close_draw_logit","side_close_draw_std","side_close_draw_range","side_close_source_count","side_close_balance_abs_mean","side_close_ha_diff_mean","side_close_margin_mean","side_open_draw_logit","side_open_draw_std","side_open_balance_abs_mean","side_consensus_draw_shift","side_consensus_balance_shift"]
    x12_detail=[]
    for s in ("ps","b365","avg","max","bfe"):
        x12_detail += [f"side_{s}_open_draw_logit",f"side_{s}_close_draw_logit",f"side_{s}_draw_shift",f"side_{s}_close_balance_abs"]
    rich_x12=x12_consensus+x12_detail
    ah_consensus=["ah_open_line","ah_close_line","ah_line_shift","ah_open_home_logit","ah_open_home_std","ah_open_source_count","ah_close_home_logit","ah_close_home_std","ah_close_source_count","ah_consensus_home_shift"]
    ah_detail=[]
    for s in ("ps","b365","avg","max","bfe"):
        ah_detail += [f"ah_{s}_open_home_logit",f"ah_{s}_close_home_logit",f"ah_{s}_home_shift"]
    rich_ah=ah_consensus+ah_detail

    comparisons={}
    # Cohort A: maximize power, 1X2 only.
    fit_side=fold[(fold.split.isin(["train","policy"]))&side_mask].copy()
    side_packs={"core":core,"core_plus_single_1x2":core+["mkt_draw_logit","mkt_home_minus_away"],"core_plus_rich_1x2":core+rich_x12}
    side_probs={}; side_receipts={}; side_metrics={}; y_side=(side_target.goal_difference==0).astype(int).to_numpy()
    for name,feats in side_packs.items():
        p,r=fit_by_total(fit_side,side_target,feats,config); side_probs[name]=p; side_receipts[name]=r; side_metrics[name]=binary_metrics(y_side,p)
    side_delta={m:paired_bootstrap((-(y_side*np.log(np.clip(side_probs["core_plus_rich_1x2"],1e-15,1))+(1-y_side)*np.log(np.clip(1-side_probs["core_plus_rich_1x2"],1e-15,1))))-(-(y_side*np.log(np.clip(side_probs["core_plus_single_1x2"],1e-15,1))+(1-y_side)*np.log(np.clip(1-side_probs["core_plus_single_1x2"],1e-15,1)))),5000,870100, ) for m in []}
    # Explicit bootstrap arrays for the two proper losses.
    def ll_rows(y,p): p=np.clip(p,1e-15,1-1e-15); return -(y*np.log(p)+(1-y)*np.log(1-p))
    side_boot={"logloss":paired_bootstrap(ll_rows(y_side,side_probs["core_plus_rich_1x2"])-ll_rows(y_side,side_probs["core_plus_single_1x2"]),5000,870100),"brier":paired_bootstrap((side_probs["core_plus_rich_1x2"]-y_side)**2-(side_probs["core_plus_single_1x2"]-y_side)**2,5000,870101)}
    comparisons["side_cohort"]={"n":int(len(side_target)),"draws":int(y_side.sum()),"metrics":side_metrics,"bootstrap_rich_minus_single":side_boot,"receipts":side_receipts}

    # Cohort B: same synchronized market cohort, test whether AH adds to rich 1X2.
    fit_sync=fold[(fold.split.isin(["train","policy"]))&sync_mask].copy(); y_sync=(sync_target.goal_difference==0).astype(int).to_numpy()
    sync_packs={"core_plus_rich_1x2":core+rich_x12,"core_plus_single_side_ah":core+["mkt_draw_logit","mkt_home_minus_away","mkt_ah_line","mkt_ah_home_logit"],"core_plus_rich_1x2_ah":core+rich_x12+rich_ah}
    sync_probs={}; sync_receipts={}; sync_metrics={}
    for name,feats in sync_packs.items():
        p,r=fit_by_total(fit_sync,sync_target,feats,config); sync_probs[name]=p; sync_receipts[name]=r; sync_metrics[name]=binary_metrics(y_sync,p)
    sync_boot={"logloss":paired_bootstrap(ll_rows(y_sync,sync_probs["core_plus_rich_1x2_ah"])-ll_rows(y_sync,sync_probs["core_plus_rich_1x2"]),5000,870110),"brier":paired_bootstrap((sync_probs["core_plus_rich_1x2_ah"]-y_sync)**2-(sync_probs["core_plus_rich_1x2"]-y_sync)**2,5000,870111)}
    comparisons["synchronized_cohort"]={"n":int(len(sync_target)),"draws":int(y_sync.sum()),"metrics":sync_metrics,"bootstrap_rich_ah_minus_rich_1x2":sync_boot,"receipts":sync_receipts}

    rows=side_target[KEYS+["match_identity","identity_hash","total_class","exact_total","goal_difference"]].copy(); rows["actual_draw"]=(rows.goal_difference==0).astype(int)
    for name,p in side_probs.items(): rows[f"side_{name}_p_draw"]=p; rows[f"side_{name}_pred_draw"]=(p>=0.5).astype(int)
    sync_index=set(sync_target.index)
    for name,p in sync_probs.items():
        mapping=dict(zip(sync_target.index,p)); rows[f"sync_{name}_p_draw"]=[mapping.get(i,np.nan) for i in rows.index]
        rows[f"sync_{name}_pred_draw"]=(rows[f"sync_{name}_p_draw"]>=0.5).astype("Int64")

    result={"schema_version":"FIXED500_EVEN_T_DRAW_VS_PM2_R3","classification":"ORACLE_EXACT_T_CONDITIONAL_GD_INFORMATION_DIAGNOSTIC","question":"Given exact even T in {2,4,6}, can existing pre-match side-market structure distinguish GD=0 from GD=±2?","sample":{"parent_fixed500_n":500,"parent_fixed500_identity_sha256":sample_hash,"new_sample_consumed":False,"latest_position4_confirmation_opened":False},"target_contract":{"exact_total_classes":[2,4,6],"goal_difference_support":[-2,0,2],"draw_label":"GD=0","non_draw_label":"GD=±2","uses_oracle_exact_T":True},"comparisons":comparisons,"data_identity":data_identity,"excluded_incomplete_latest_seasons":excluded,"interpretation_guard":{"retrospective_information_ceiling_only":True,"oracle_T_diagnostic_only":True,"formal_PIT_claim":False,"can_authorize_promotion":False,"same_fixed500_already_viewed":True},"governance":{"formal_weight":0,"provider_requests":0,"new_data_collection":False,"new_sample_consumed":False,"latest_position4_confirmation_opened":False,"formal_model_mutation":False,"formal_data_mutation":False,"formal_config_mutation":False,"current_mutation":False,"main_mutation":False}}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); rows.to_csv(ROWS_OUT,index=False)
    return result


def main():
    x=run(); print(json.dumps({"sample":x["sample"],"target":x["target_contract"],"comparisons":x["comparisons"]},ensure_ascii=False,indent=2))
if __name__=="__main__": main()
