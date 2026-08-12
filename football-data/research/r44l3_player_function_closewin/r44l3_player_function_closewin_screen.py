#!/usr/bin/env python3
from __future__ import annotations
import argparse, csv, hashlib, io, json, math, re, urllib.request
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, roc_auc_score, log_loss, brier_score_loss

TARGETS = ("2023/24","2024/25","2025/26")
MARKET = ["fair_home","fair_draw","fair_away","home_away_balance","draw_vs_side_margin","market_entropy"]
FAMILIES = {
    "absence": ["regular_absent_count","regular_absent_goalkeeper","regular_absent_defender","regular_absent_midfielder","regular_absent_forward"],
    "experience": ["lineup_prior_minutes_10_sum","lineup_prior_starts_10_sum","lineup_low_history_count"],
    "attack": ["lineup_prior_bps_per90_mean","lineup_prior_xgi_per90_sum"],
    "defense": ["lineup_prior_xgc_per90_mean","lineup_prior_defensive_per90_sum"],
    "goalkeeper": ["goalkeeper_prior_saves_per90","goalkeeper_prior_goals_conceded_per90"],
}
FAMILIES["all_player"] = sum(FAMILIES.values(), [])
ALIASES = {
    "manchesterunited":"manutd","manunited":"manutd","manutd":"manutd",
    "manchestercity":"mancity","mancity":"mancity",
    "tottenhamhotspur":"tottenham","tottenham":"tottenham","spurs":"tottenham",
    "nottinghamforest":"nottmforest","nottmforest":"nottmforest",
    "wolverhamptonwanderers":"wolves","wolverhampton":"wolves","wolves":"wolves",
    "newcastleunited":"newcastle","newcastle":"newcastle",
    "westhamunited":"westham","westham":"westham",
    "brightonandhovealbion":"brighton","brighton":"brighton",
    "sheffieldunited":"sheffieldutd","sheffieldutd":"sheffieldutd",
    "leicestercity":"leicester","leicester":"leicester",
    "ipswichtown":"ipswich","ipswich":"ipswich",
    "lutontown":"luton","luton":"luton","leedsunited":"leeds","leeds":"leeds",
    "norwichcity":"norwich","norwich":"norwich",
}

def sha256_bytes(b: bytes)->str: return hashlib.sha256(b).hexdigest()
def norm(x)->str:
    k=re.sub(r"[^a-z0-9]","",str(x).lower().replace("&","and").replace("'",""))
    return ALIASES.get(k,k)
def parse_date(x:str)->str:
    for fmt in ("%d/%m/%Y","%d/%m/%y","%Y-%m-%d"):
        try: return datetime.strptime(str(x).strip(),fmt).date().isoformat()
        except ValueError: pass
    raise ValueError(x)
def fetch(url:str)->bytes:
    req=urllib.request.Request(url,headers={"User-Agent":"r44l3-player-function-closewin-screen"})
    with urllib.request.urlopen(req,timeout=120) as r: return r.read()

def verify_r2(r2:Path):
    manifest=json.loads((r2/"artifact_manifest.json").read_text(encoding="utf-8"))
    for name, meta in manifest["files"].items():
        p=r2/name
        if not p.exists(): raise RuntimeError(f"R2 artifact missing {name}")
        got=sha256_bytes(p.read_bytes())
        if got != meta["sha256"]: raise RuntimeError(f"R2 artifact hash mismatch {name}: {got}")
    return manifest

def score_index(r2:Path):
    ledger=pd.read_csv(r2/"EPL_LINEUP_QUALITY_R2_source_ledger.csv")
    score={}; source_audit=[]
    for _,row in ledger[ledger["source"].eq("Football-Data")].iterrows():
        data=fetch(str(row["url"])); got=sha256_bytes(data); expected=str(row["sha256"])
        if got != expected: raise RuntimeError(f"Football-Data frozen hash mismatch {row['season']} {got} != {expected}")
        df=pd.read_csv(io.BytesIO(data))
        source_audit.append({"season":row["season"],"url":row["url"],"sha256":got,"rows":int(len(df))})
        for _,r in df.iterrows():
            if pd.isna(r.get("FTHG")) or pd.isna(r.get("FTAG")): continue
            day=parse_date(r["Date"]); key=(str(row["season"]),day,norm(r["HomeTeam"]),norm(r["AwayTeam"]))
            score[key]=(int(r["FTHG"]),int(r["FTAG"]))
    return score,source_audit

def family_columns(base_names):
    return [f"{prefix}_{name}" for name in base_names for prefix in ("home","away","diff")]

def make_model(name):
    if name=="logistic":
        return Pipeline([("imputer",SimpleImputer(strategy="median")),("scale",StandardScaler()),("model",LogisticRegression(C=0.2,max_iter=1000,random_state=20260812))])
    return Pipeline([("imputer",SimpleImputer(strategy="median")),("model",HistGradientBoostingClassifier(learning_rate=0.05,max_iter=200,max_leaf_nodes=15,l2_regularization=4.0,random_state=20260812))])

def metric(y,p):
    p=np.clip(np.asarray(p,float),1e-9,1-1e-9); y=np.asarray(y,int)
    return {"pr_auc":float(average_precision_score(y,p)),"roc_auc":float(roc_auc_score(y,p)),"log_loss":float(log_loss(y,p)),"brier":float(brier_score_loss(y,p))}

def run_oos(df):
    rows=[]; preds=[]; seasons=["2021/22","2022/23","2023/24","2024/25","2025/26"]
    for target in TARGETS:
        i=seasons.index(target); train=df[df.season.isin(seasons[:i])].copy(); test=df[df.season.eq(target)].copy()
        for model_name in ("logistic","hist_gradient_boosting"):
            scores={}; feature_sets={"market":MARKET}
            for fam,bases in FAMILIES.items(): feature_sets[f"market_plus_{fam}"]=MARKET+family_columns(bases)
            for fam,cols in feature_sets.items():
                model=make_model(model_name); model.fit(train[cols].astype(float),train.target_draw); p=model.predict_proba(test[cols].astype(float))[:,1]
                scores[fam]=p; m=metric(test.target_draw,p)
                rows.append({"target_season":target,"train_rows":len(train),"test_rows":len(test),"test_draws":int(test.target_draw.sum()),"model":model_name,"family":fam,**m})
            for j,(_,r) in enumerate(test.iterrows()):
                for fam,p in scores.items(): preds.append({"season":target,"fixture":int(r.fixture),"date":r.date,"home_team":r.home_team,"away_team":r.away_team,"target_draw":int(r.target_draw),"model":model_name,"family":fam,"score":float(p[j])})
    return pd.DataFrame(rows),pd.DataFrame(preds)

def delta_table(metrics):
    out=[]
    for (season,model),g in metrics.groupby(["target_season","model"]):
        base=g[g.family.eq("market")].iloc[0]
        for _,r in g[~g.family.eq("market")].iterrows(): out.append({"target_season":season,"model":model,"family":r.family.replace("market_plus_",""),"delta_pr_auc":r.pr_auc-base.pr_auc,"delta_roc_auc":r.roc_auc-base.roc_auc,"delta_log_loss":r.log_loss-base.log_loss,"delta_brier":r.brier-base.brier})
    return pd.DataFrame(out)

def bootstrap(preds,n=5000):
    rng=np.random.default_rng(20260812); out=[]
    for model in preds.model.unique():
        b=preds[(preds.model==model)&(preds.family=="market")].sort_values(["season","fixture"]).reset_index(drop=True); y=b.target_draw.to_numpy(int); pb=b.score.to_numpy(float)
        for family in [x for x in preds.family.unique() if x!="market"]:
            c=preds[(preds.model==model)&(preds.family==family)].sort_values(["season","fixture"]).reset_index(drop=True)
            if not np.array_equal(c[["season","fixture"]].to_numpy(),b[["season","fixture"]].to_numpy()): raise RuntimeError("prediction identity mismatch")
            pc=c.score.to_numpy(float); dll=[]; db=[]; da=[]; N=len(y)
            for _ in range(n):
                idx=rng.integers(0,N,N); yy=y[idx]
                if len(np.unique(yy))<2: continue
                mb=metric(yy,pb[idx]); mc=metric(yy,pc[idx]); dll.append(mc["log_loss"]-mb["log_loss"]); db.append(mc["brier"]-mb["brier"]); da.append(mc["roc_auc"]-mb["roc_auc"])
            out.append({"model":model,"family":family.replace("market_plus_",""),"n":len(dll),"delta_ll_mean":float(np.mean(dll)),"delta_ll_p05":float(np.quantile(dll,.05)),"delta_ll_p95":float(np.quantile(dll,.95)),"delta_brier_mean":float(np.mean(db)),"delta_brier_p05":float(np.quantile(db,.05)),"delta_brier_p95":float(np.quantile(db,.95)),"delta_roc_mean":float(np.mean(da)),"delta_roc_p05":float(np.quantile(da,.05)),"delta_roc_p95":float(np.quantile(da,.95))})
    return pd.DataFrame(out)

def univariate(df):
    out=[]; y=df.target_draw.to_numpy(int)
    for col in family_columns(FAMILIES["all_player"]):
        x=df[col].astype(float).to_numpy()
        if np.nanstd(x)<1e-12: continue
        med=float(np.nanmedian(x)); x=np.where(np.isfinite(x),x,med); auc=float(roc_auc_score(y,x)); d=x[y==1]; n=x[y==0]
        pooled=math.sqrt(max(((len(d)-1)*np.var(d,ddof=1)+(len(n)-1)*np.var(n,ddof=1))/max(len(d)+len(n)-2,1),1e-15)); smd=float((np.mean(d)-np.mean(n))/pooled)
        out.append({"feature":col,"auc_raw":auc,"auc_separation":max(auc,1-auc),"smd_draw_minus_onegoal":smd,"draw_mean":float(np.mean(d)),"onegoal_mean":float(np.mean(n))})
    return pd.DataFrame(out).sort_values(["auc_separation","feature"],ascending=[False,True])

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--r2-dir",type=Path,required=True); ap.add_argument("--out",type=Path,required=True); args=ap.parse_args(); args.out.mkdir(parents=True,exist_ok=True)
    verify_r2(args.r2_dir); df=pd.read_csv(args.r2_dir/"EPL_LINEUP_QUALITY_R2_dataset.csv"); score,source_audit=score_index(args.r2_dir)
    matched=[]; missing=[]
    for idx,r in df.iterrows():
        key=(str(r.season),str(r.date),norm(r.home_team),norm(r.away_team))
        if key not in score: missing.append({"row":int(idx),"key":key}); matched.append((np.nan,np.nan))
        else: matched.append(score[key])
    if missing: raise RuntimeError(f"score join missing {len(missing)} rows: {missing[:5]}")
    df["home_goals"]=[x[0] for x in matched]; df["away_goals"]=[x[1] for x in matched]; df["goal_diff"]=df.home_goals-df.away_goals
    close=df[df.goal_diff.abs()<=1].copy(); close["target_draw"]=(close.goal_diff==0).astype(int)
    if close.target_draw.nunique()!=2: raise RuntimeError("close target has one class")
    metrics,preds=run_oos(close); deltas=delta_table(metrics); boots=bootstrap(preds,5000); uni=univariate(close)
    consistency=[]
    for (model,fam),g in deltas.groupby(["model","family"]): consistency.append({"model":model,"family":fam,"positive_pr_auc_folds":int((g.delta_pr_auc>0).sum()),"positive_roc_auc_folds":int((g.delta_roc_auc>0).sum()),"nonworse_logloss_folds":int((g.delta_log_loss<=0).sum()),"nonworse_brier_folds":int((g.delta_brier<=0).sum()),"median_delta_pr_auc":float(g.delta_pr_auc.median()),"median_delta_roc_auc":float(g.delta_roc_auc.median()),"median_delta_log_loss":float(g.delta_log_loss.median()),"median_delta_brier":float(g.delta_brier.median())})
    consistency=pd.DataFrame(consistency)
    audit={"schema_version":"R44L3-PLAYER-FUNCTION-CLOSEWIN-SCREEN-R1","status":"VIEWED_HISTORICAL_DEVELOPMENT_DIAGNOSTIC_ONLY","question":"Do existing detailed player/XI features distinguish draws from one-goal wins, the known draw bottleneck?","source_r2_artifact_id":8848368411,"source_r2_zip_sha256":"025bf7c11ca01dcda720977777af404079e26feb992bf6ed51cc27b5ba285946","dataset_rows":int(len(df)),"score_join_rows":int(len(df)),"score_join_missing":0,"close_rows":int(len(close)),"draws":int(close.target_draw.sum()),"one_goal_wins":int((close.target_draw==0).sum()),"rows_by_season":{k:int(v) for k,v in close.season.value_counts().sort_index().items()},"draws_by_season":{k:int(v) for k,v in close[close.target_draw==1].season.value_counts().sort_index().items()},"feature_families":{k:family_columns(v) for k,v in FAMILIES.items()},"market_features":MARKET,"models":{"logistic":{"C":0.2,"max_iter":1000,"random_state":20260812},"hist_gradient_boosting":{"learning_rate":0.05,"max_iter":200,"max_leaf_nodes":15,"l2_regularization":4.0,"random_state":20260812}},"evaluation":"rolling by season; train only earlier seasons; targets 2023/24, 2024/25, 2025/26; no hyperparameter selection","bootstrap":{"resamples":5000,"seed":20260812,"unit":"match"},"important_limitations":["All target seasons are already viewed historical development evidence; no promotion or confirmation claim is permitted.","Current-match starters in the R2 source are actual historical starters; their release time is not proven pre-kickoff. This screen asks information content conditional on XI knowledge, not strict forward PIT deployability.","Football-Data market prices are frozen historical static references without raw quote timestamps.","The close-match filter uses final goal margin and is mechanism analysis, not a deployable pre-match filter."],"football_data_sources":source_audit,"formal_weight":0,"promotion_allowed":False,"formal_model_data_config_current_writes":[0,0,0,0]}
    metrics.to_csv(args.out/"fold_metrics.csv",index=False); deltas.to_csv(args.out/"fold_deltas.csv",index=False); preds.to_csv(args.out/"oos_predictions.csv",index=False); boots.to_csv(args.out/"bootstrap_summary.csv",index=False); uni.to_csv(args.out/"univariate_player_separation.csv",index=False); consistency.to_csv(args.out/"consistency_summary.csv",index=False); close[["season","fixture","date","home_team","away_team","home_goals","away_goals","target_draw"]].to_csv(args.out/"close_match_identity.csv",index=False); (args.out/"audit.json").write_text(json.dumps(audit,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":audit["status"],"close_rows":audit["close_rows"],"draws":audit["draws"],"one_goal_wins":audit["one_goal_wins"],"top_consistency":consistency.sort_values(["positive_roc_auc_folds","positive_pr_auc_folds","median_delta_log_loss"],ascending=[False,False,True]).head(6).to_dict("records")},ensure_ascii=False))
if __name__=="__main__": main()
