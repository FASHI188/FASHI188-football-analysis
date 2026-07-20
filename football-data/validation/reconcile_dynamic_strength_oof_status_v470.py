#!/usr/bin/env python3
"""Reconcile raw dynamic-strength OOF results with the strict data-readiness gate.

A run that should never have been started because chronological evidence is too
short is not a model failure.  This receipt separates model rejection, data
insufficiency, stage-adapter blocking and genuine engineering failure.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
RAW=ROOT/"manifests"/"dynamic_strength_oof_screen_v470_status.json"
READY=ROOT/"manifests"/"dynamic_strength_oof_readiness_v470_status.json"
OUT=ROOT/"manifests"/"dynamic_strength_oof_effective_status_v470.json"
def main()->int:
    raw=json.loads(RAW.read_text(encoding="utf-8"));ready=json.loads(READY.read_text(encoding="utf-8"))
    ready_set=set(ready.get("chronological_oof_ready",[]));stage=set(ready.get("stage_adapter_required",[]));insufficient=set(ready.get("insufficient_chronological_history",[]));unavailable=set(ready.get("partial_or_unavailable",[]))
    reports={};candidates=[];model_rejected=[];engineering_failures=[]
    all_ids=set(ready.get("reports",{}))|set(raw.get("reports",{}))
    for cid in sorted(all_ids):
        r=raw.get("reports",{}).get(cid,{});raw_status=r.get("status")
        if cid in ready_set:
            if raw_status=="DYNAMIC_STRENGTH_REVIEW_CANDIDATE":status="STAGE1_REVIEW_CANDIDATE";candidates.append(cid)
            elif raw_status=="KEEP_RESEARCH_WEIGHT_0":status="MODEL_SCREEN_REJECTED";model_rejected.append(cid)
            elif raw_status=="FAILED":status="ENGINEERING_OR_RUNTIME_FAILURE";engineering_failures.append(cid)
            else:status="READY_BUT_SCREEN_RECEIPT_MISSING_OR_UNKNOWN";engineering_failures.append(cid)
        elif cid in insufficient:status="NOT_RUNNABLE_INSUFFICIENT_CHRONOLOGICAL_HISTORY"
        elif cid in stage:status="NOT_RUNNABLE_STAGE_ADAPTER_REQUIRED"
        elif cid in unavailable:status="NOT_RUNNABLE_PUBLIC_EVIDENCE_UNAVAILABLE"
        else:status="UNCLASSIFIED"
        reports[cid]={"competition_id":cid,"effective_status":status,"raw_screen_status":raw_status,"formal_weight":0,"probability_change":False}
    out={"schema_version":"V4.7.0-dynamic-strength-oof-effective-status-r1","generated_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"status":"PASS" if not engineering_failures else "PARTIAL","stage1_review_candidates":candidates,"model_screen_rejected":model_rejected,"engineering_failures":engineering_failures,"insufficient_chronological_history":sorted(insufficient),"stage_adapter_required":sorted(stage),"public_evidence_unavailable":sorted(unavailable),"formal_weight_change":False,"automatic_promotion":False,"probability_change":False,"reports":reports,"policy":"This receipt supersedes raw aggregate failure counts for governance classification. Data insufficiency or stage blocking is never relabeled as model failure. Raw research artifacts remain available for audit only."}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8");print(json.dumps({k:out[k] for k in ("status","stage1_review_candidates","model_screen_rejected","engineering_failures","insufficient_chronological_history","stage_adapter_required")},ensure_ascii=False,indent=2));return 0 if not engineering_failures else 1
if __name__=="__main__":raise SystemExit(main())
