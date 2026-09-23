#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, ssl, urllib.error, urllib.parse, urllib.request
from pathlib import Path
from typing import Any

class ProbeError(RuntimeError): pass
def req(c:bool,m:str)->None:
    if not c: raise ProbeError(m)
def sha256_bytes(b:bytes)->str:return hashlib.sha256(b).hexdigest()
def host_ok(url:str,host:str)->bool:return (urllib.parse.urlparse(url).hostname or "").lower()==host.lower()

def head(url:str,timeout:int)->tuple[int,str,dict[str,str]]:
    rq=urllib.request.Request(url,method="HEAD",headers={"User-Agent":"Football3-Nova-N10-DAPIContentProbe/1.0"})
    try:
        with urllib.request.urlopen(rq,timeout=timeout,context=ssl.create_default_context()) as r:
            return int(getattr(r,"status",200)),r.geturl(),{k.lower():v for k,v in r.headers.items()}
    except urllib.error.HTTPError as e:
        return int(e.code),e.geturl(),{k.lower():v for k,v in e.headers.items()}

def run(registry:Path,out:Path,timeout:int=15)->dict[str,Any]:
    p=json.loads(registry.read_text(encoding="utf-8"))
    req(p["status"]=="DESIGN_LOCKED_ZERO_LABEL","STATUS")
    req(p["exact_base"]=="78b6694b40c45f5aeaf18afcaf8ca8513908e2fc","EXACT_BASE")
    h=p["hard_rules"]
    req(h["method"]=="HEAD" and h["response_body_read"] is False,"HEAD_ONLY")
    req(h["result_labels_read"] is False and h["score_values_read"] is False,"ZERO_LABEL")
    req(h["match_payload_read"] is False and h["standings_payload_read"] is False and h["player_stats_payload_read"] is False,"NO_SPORT_PAYLOADS")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False,"NO_MODEL")
    req(h["paid_or_secret_source_allowed"] is False,"NO_SECRET")
    req(h["candidate_weight"]==0 and h["matrix_delta"]==0,"ZERO_WEIGHT")
    req(len(p["candidates"])==14,"CANDIDATE_N")

    signal_status=set(int(x) for x in p["decision_contract"]["route_signal_statuses"])
    reports=[]; errors=[]; signals=[]
    for c in p["candidates"]:
        try:
            status,final,headers=head(c["url"],timeout)
            req(host_ok(final,p["host"]),"REDIRECT_OUTSIDE_DAPI")
            row={
                "id":c["id"],"source_url":c["url"],"final_url":final,
                "method":"HEAD","http_status":status,
                "content_type":headers.get("content-type"),
                "content_length":headers.get("content-length"),
                "allow":headers.get("allow"),
                "response_body_read":False,
            }
            reports.append(row)
            if status in signal_status:
                signals.append(row)
        except Exception as e:
            errors.append({"id":c["id"],"url":c["url"],"error":f"{type(e).__name__}:{e}"[:400]})

    all404=(len(reports)==len(p["candidates"]) and all(r["http_status"]==404 for r in reports))
    classification="POSITIVE_SIGNAL_SOURCE_FEASIBILITY" if signals else "STOP_DATA_COVERAGE"
    reason="CONTENT_ROUTE_SIGNAL_FOUND" if signals else ("ALL_FROZEN_CONTENT_ROUTES_404" if all404 else "NO_CONTENT_ROUTE_SIGNAL")

    out.mkdir(parents=True,exist_ok=True)
    receipt={
      "schema_version":"football3-nova-n10-referee-lega-dapi-content-probe-receipt-v1",
      "status":"N10_REFEREE_LEGA_DAPI_CONTENT_PROBE_COMPLETE",
      "classification":classification,"reason":reason,
      "exact_base":p["exact_base"],"registry_sha256":sha256_bytes(registry.read_bytes()),
      "candidate_n":len(p["candidates"]),"report_n":len(reports),"error_n":len(errors),
      "signal_n":len(signals),"all_frozen_routes_404":all404,
      "signals":signals,"reports":reports,"errors":errors,
      "response_body_read":False,"match_payload_read":False,"standings_payload_read":False,"player_stats_payload_read":False,
      "full_big5_data_ready":False,"referee_oof_allowed":False,
      "result_labels_read":0,"score_values_read":0,"training_performed":False,"scoring_performed":False,
      "formal_v2_changed":False,"current_changed":False,"production_changed":False,"candidate_weight":0,"matrix_delta":0,
      "next_step":("FREEZE_EXACT_SIGNALED_ROUTE_IN_NEW_BATCH; DO_NOT_EXPAND_IN_THIS_BATCH" if signals else "CLOSE_DAPI_CMS_DISCOVERY_AND_CONTINUE_ONLY_WITH_NEW_LEGAL_FREE_SOURCE")
    }
    (out/"lega_dapi_content_probe_receipt.json").write_text(json.dumps(receipt,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(receipt,sort_keys=True));return receipt

def main():
    a=argparse.ArgumentParser();a.add_argument("--registry",type=Path,required=True);a.add_argument("--out",type=Path,required=True);a.add_argument("--timeout",type=int,default=15)
    x=a.parse_args();run(x.registry,x.out,x.timeout)
if __name__=="__main__":main()
