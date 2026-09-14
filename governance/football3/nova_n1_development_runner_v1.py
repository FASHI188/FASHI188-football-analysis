#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from nova_n1_common_v1 import *
from nova_n1_pit_replay_v1 import build_replay
from nova_n1_residual_math_v1 import *

def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--artifact",required=True,type=Path)
    ap.add_argument("--contract",required=True,type=Path)
    ap.add_argument("--work-dir",required=True,type=Path)
    ap.add_argument("--out-dir",required=True,type=Path)
    args=ap.parse_args()
    started=time.time()
    execution_head=os.environ.get("NOVA_N1_EXECUTION_HEAD","")
    if not re.fullmatch(r"[0-9a-f]{40}", execution_head):
        raise N1Error("exact execution head missing")
    load_contract(args.contract)
    db,source_audit=prepare_db(args.artifact,args.work_dir)
    con=sqlite3.connect(f"file:{db}?mode=ro",uri=True)
    try:
        rows,audit=build_replay(con,FIT_SEASONS|DEV_SEASONS)
    finally:
        con.close()
    actual={s:sum(1 for r in rows if r.season_key==s) for s in sorted(FIT_SEASONS|DEV_SEASONS)}
    if sum(actual[s] for s in FIT_SEASONS)!=7303 or sum(actual[s] for s in DEV_SEASONS)!=3551:
        raise N1Error(f"development cohort count drift {actual}")
    configs=[]
    for window in WINDOWS:
        for route in ROUTES:
            cid=f"{route}-W{window}"
            try:
                means,stds,standardizer_n=fit_standardizer(rows,window,route)
                samples=make_fit_samples(rows,window,route,means,stds)
                fit=fit_residual(samples)
                dev=evaluate(rows,DEV_SEASONS,window,route,means,stds,fit["theta"])
                cfg={
                    "config_id":cid,"route":route,"window":window,
                    "feature_dimension":len(samples[0][0]),"standardizer_n":standardizer_n,
                    "fit_n":len(samples),"means":means,"stds":stds,
                    "fit":{"iterations":fit["iterations"],"loss":fit["loss"],"initial_loss":fit["initial_loss"],"grad_inf":fit["grad_inf"]},
                    "theta":fit["theta"],"development":dev,
                    "selection_constraints_pass":development_pass(dev),
                    "status":"PASS_IMPLEMENTATION",
                }
            except Exception as exc:
                cfg={"config_id":cid,"route":route,"window":window,"status":"FAIL_CURRENT_IMPLEMENTATION","error":f"{type(exc).__name__}:{exc}","selection_constraints_pass":False}
            configs.append(cfg)
    eligible=[c for c in configs if c.get("status")=="PASS_IMPLEMENTATION" and c["selection_constraints_pass"]]
    selected=min(eligible,key=selection_key) if eligible else None
    if selected:
        frozen_model={
            "schema_version":"football3-nova-n1-selected-development-model-v1",
            "config_id":selected["config_id"],"route":selected["route"],"window":selected["window"],
            "feature_dimension":selected["feature_dimension"],"means":selected["means"],"stds":selected["stds"],
            "theta":selected["theta"],"formal_head":FORMAL_HEAD,"l2":L2,
            "fit_seasons":sorted(FIT_SEASONS),"development_seasons":sorted(DEV_SEASONS),
            "prelabel_freeze_head":PRELABEL_FREEZE_HEAD,"adopted_mechanics_head":ADOPTED_MECHANICS_HEAD,
            "test_labels_read":False,"promotion_authorized":False,
        }
        frozen_model["model_sha256"]=canonical_sha(frozen_model)
        status="DEVELOPMENT_SELECTION_FROZEN"
    else:
        frozen_model=None
        status="FAIL_CURRENT_IMPLEMENTATION" if all(c["status"]=="FAIL_CURRENT_IMPLEMENTATION" for c in configs) else "FAIL_RESEARCH_DIRECTION"
    report={
        "schema_version":"football3-nova-n1-development-report-v1","execution_head":execution_head,"status":status,
        "formal_head":FORMAL_HEAD,"current_sha256":CURRENT_SHA256,
        "prelabel_freeze_head":PRELABEL_FREEZE_HEAD,"adopted_mechanics_head":ADOPTED_MECHANICS_HEAD,
        "development_artifact_id":EXPECTED_ARTIFACT_ID,"development_artifact_sha256":EXPECTED_ARTIFACT_SHA256,
        "database_sha256":EXPECTED_DB_SHA256,"cohort_counts":actual,
        "fit_n_total":sum(actual[s] for s in FIT_SEASONS),"development_n_total":sum(actual[s] for s in DEV_SEASONS),
        "source_audit":source_audit,"replay_audit":audit,"candidate_budget":12,"candidate_evaluated_n":len(configs),
        "configs":configs,"selected_config_id":selected["config_id"] if selected else None,
        "selected_model_sha256":frozen_model["model_sha256"] if frozen_model else None,
        "test_artifact_opened":False,"test_labels_read":False,
        "j1":{"status":"NOT_AVAILABLE","weight":0,"matrix_delta":0},"k1":{"status":"NOT_AVAILABLE","weight":0,"matrix_delta":0},
        "formal_v2_modified":False,"current_modified":False,"production_modified":False,"elapsed_seconds":time.time()-started,
    }
    args.out_dir.mkdir(parents=True,exist_ok=True)
    (args.out_dir/"development_report.json").write_text(json.dumps(report,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    if frozen_model:
        (args.out_dir/"selected_model.json").write_text(json.dumps(frozen_model,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":status,"selected_config_id":report["selected_config_id"],"selected_model_sha256":report["selected_model_sha256"],"fit_n":report["fit_n_total"],"dev_n":report["development_n_total"]},sort_keys=True))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
