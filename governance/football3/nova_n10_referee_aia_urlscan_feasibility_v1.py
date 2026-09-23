#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

class UrlscanError(RuntimeError):
    pass

def req(c: bool, m: str) -> None:
    if not c:
        raise UrlscanError(m)

def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def host(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()

def parse_day(v: str) -> dt.datetime:
    return dt.datetime.strptime(v,"%Y-%m-%d").replace(tzinfo=dt.timezone.utc)

def parse_time(v: Any) -> dt.datetime | None:
    if not isinstance(v,str) or not v.strip():
        return None
    try:
        x=dt.datetime.fromisoformat(v.strip().replace("Z","+00:00"))
        if x.tzinfo is None:
            x=x.replace(tzinfo=dt.timezone.utc)
        return x.astimezone(dt.timezone.utc)
    except Exception:
        return None

def exact_url(a: str | None, b: str) -> bool:
    if not isinstance(a,str):
        return False
    return a.rstrip("/")==b.rstrip("/")

def date_query_bounds(published_date: str) -> tuple[str,str,dt.datetime,dt.datetime]:
    start=parse_day(published_date)
    end=start+dt.timedelta(days=8)
    q_end=end-dt.timedelta(milliseconds=1)
    return (
        start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        q_end.strftime("%Y-%m-%dT%H:%M:%S.999Z"),
        start,end
    )

def build_query(endpoint: str, published_date: str, size: int) -> str:
    q_start,q_end,_,_=date_query_bounds(published_date)
    q=f"page.domain:aia-figc.it AND date:[{q_start} TO {q_end}]"
    return endpoint+"?"+urllib.parse.urlencode({"q":q,"size":str(size)})

def request_json(url: str, timeout: int=25, limit: int=2_000_000) -> tuple[int,bytes,str,dict[str,str]]:
    rq=urllib.request.Request(url,headers={
        "User-Agent":"Football3-Nova-N10-urlscanFeasibility/1.0",
        "Accept":"application/json",
    })
    try:
        with urllib.request.urlopen(rq,timeout=timeout,context=ssl.create_default_context()) as r:
            data=r.read(limit+1)
            req(len(data)<=limit,"RESPONSE_TOO_LARGE")
            return int(getattr(r,"status",200)),data,r.geturl(),{k.lower():v for k,v in r.headers.items()}
    except urllib.error.HTTPError as e:
        data=e.read(limit+1)
        req(len(data)<=limit,"ERROR_RESPONSE_TOO_LARGE")
        return int(e.code),data,e.geturl(),{k.lower():v for k,v in e.headers.items()}

def parse_search(raw: bytes) -> tuple[list[dict[str,Any]],dict[str,Any]]:
    x=json.loads(raw.decode("utf-8"))
    req(isinstance(x,dict),"SEARCH_SHAPE")
    arr=x.get("results")
    req(isinstance(arr,list),"SEARCH_RESULTS_SHAPE")
    meta={
        "total":x.get("total"),
        "has_more":x.get("has_more"),
        "took":x.get("took"),
    }
    return [r for r in arr if isinstance(r,dict)],meta

def safe_result_metadata(r: dict[str,Any]) -> dict[str,Any]:
    task=r.get("task") if isinstance(r.get("task"),dict) else {}
    page=r.get("page") if isinstance(r.get("page"),dict) else {}
    scan_id=r.get("_id")
    scan_time=task.get("time")
    return {
        "scan_id":str(scan_id) if scan_id is not None else None,
        "scan_time":scan_time if isinstance(scan_time,str) else None,
        "task_url":task.get("url") if isinstance(task.get("url"),str) else None,
        "page_url":page.get("url") if isinstance(page.get("url"),str) else None,
    }

def eligible_results(results: list[dict[str,Any]], sample: dict[str,Any]) -> list[dict[str,Any]]:
    _,_,start,end=date_query_bounds(sample["published_date"])
    out=[]
    for raw in results:
        m=safe_result_metadata(raw)
        when=parse_time(m["scan_time"])
        if when is None or not (start <= when < end):
            continue
        if not (exact_url(m["task_url"],sample["url"]) or exact_url(m["page_url"],sample["url"])):
            continue
        m["scan_time_utc"]=when.isoformat()
        out.append(m)
    return sorted(out,key=lambda x:(x["scan_time_utc"],x["scan_id"] or ""))

def run(registry: Path,out: Path,timeout: int=25)->dict[str,Any]:
    p=json.loads(registry.read_text(encoding="utf-8"))
    req(p["status"]=="DESIGN_LOCKED_ZERO_LABEL_METADATA_ONLY","STATUS")
    req(p["exact_base"]=="3364c9c30dc1480aef8555c64e348b78792b9560","EXACT_BASE")
    req(p["parent"]["canonical_ledger_sha256"]=="0be7e00db3f370808aa8d7caab69bc24c7d9b117efab3f6dccd2fcfebb885a9b","LEDGER_SHA")

    h=p["hard_rules"]
    req(h["result_labels_read"] is False and h["score_values_read"] is False,"ZERO_LABEL")
    req(h["match_payload_read"] is False and h["standings_payload_read"] is False and h["player_stats_payload_read"] is False,"NO_SPORT_PAYLOAD")
    req(h["article_body_read"] is False and h["scan_content_read"] is False,"NO_CONTENT")
    req(h["scan_submission_allowed"] is False,"NO_SUBMISSION")
    req(h["api_key_or_secret_allowed"] is False,"NO_SECRET")
    req(h["hidden_endpoint_guessing_allowed"] is False,"NO_HIDDEN_ENDPOINT")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False,"NO_MODEL")
    req(h["candidate_weight"]==0 and h["matrix_delta"]==0,"ZERO_WEIGHT")
    req(len(p["samples"])==5,"FROZEN_SAMPLE_N")

    src=p["source"]
    req(src["api_key_allowed"] is False and src["scan_submission_allowed"] is False,"SOURCE_NO_KEY_NO_SUBMIT")
    req(src["result_detail_fetch_allowed"] is False and src["dom_fetch_allowed"] is False and src["response_fetch_allowed"] is False and src["screenshot_fetch_allowed"] is False,"NO_SCAN_CONTENT")

    reports=[]
    errors=[]
    positive_rounds=[]
    for sample in p["samples"]:
        q=build_query(src["search_endpoint"],sample["published_date"],int(p["search_contract"]["size"]))
        try:
            status,raw,final,headers=request_json(q,timeout)
            req(host(final)==src["allowed_host"],"REDIRECT_OUTSIDE_URLSCAN")
            report={
                "round":sample["round"],
                "published_date":sample["published_date"],
                "official_url":sample["url"],
                "query_url":q,
                "final_url":final,
                "http_status":status,
                "response_bytes":len(raw),
                "response_sha256":sha256_bytes(raw),
                "content_type":headers.get("content-type"),
                "search_result_n":0,
                "eligible_exact_url_scan_n":0,
                "eligible_scans":[],
            }
            if status==200:
                results,meta=parse_search(raw)
                eligible=eligible_results(results,sample)
                report["search_result_n"]=len(results)
                report["search_meta"]=meta
                report["eligible_exact_url_scan_n"]=len(eligible)
                report["eligible_scans"]=eligible
                if eligible:
                    positive_rounds.append(sample["round"])
            else:
                # Persist only a bounded service error preview, never any page/scan content.
                report["service_error_preview"]=raw.decode("utf-8","replace")[:500]
            reports.append(report)
        except Exception as e:
            errors.append({
                "round":sample["round"],
                "official_url":sample["url"],
                "query_url":q,
                "error":f"{type(e).__name__}:{e}"[:500],
            })

    positive=bool(positive_rounds)
    classification=(
        p["decision_contract"]["positive_classification"]
        if positive else p["decision_contract"]["fail_classification"]
    )
    status_counts={}
    for r in reports:
        k=str(r["http_status"])
        status_counts[k]=status_counts.get(k,0)+1

    out.mkdir(parents=True,exist_ok=True)
    receipt={
        "schema_version":"football3-nova-n10-referee-aia-urlscan-feasibility-receipt-v1",
        "status":"N10_REFEREE_AIA_URLSCAN_FEASIBILITY_COMPLETE",
        "classification":classification,
        "exact_base":p["exact_base"],
        "registry_sha256":sha256_bytes(registry.read_bytes()),
        "sample_n":len(p["samples"]),
        "report_n":len(reports),
        "error_n":len(errors),
        "reports":reports,
        "errors":errors,
        "http_status_counts":dict(sorted(status_counts.items())),
        "positive_sample_n":len(positive_rounds),
        "positive_sample_rounds":sorted(positive_rounds),
        "api_key_used":False,
        "scan_submission_performed":False,
        "scan_result_detail_fetched":False,
        "scan_dom_fetched":False,
        "scan_response_fetched":False,
        "scan_screenshot_fetched":False,
        "article_body_read":False,
        "scan_content_read":False,
        "match_payload_read":False,
        "standings_payload_read":False,
        "player_stats_payload_read":False,
        "formal_available_at_proven":False,
        "fixture_level_binding_complete":False,
        "full_big5_data_ready":False,
        "referee_oof_allowed":False,
        "result_labels_read":0,
        "score_values_read":0,
        "training_performed":False,
        "scoring_performed":False,
        "formal_v2_changed":False,
        "current_changed":False,
        "production_changed":False,
        "candidate_weight":0,
        "matrix_delta":0,
        "next_step":(
            "IF_POSITIVE_EXPAND_URLSCAN_METADATA_TO_ALL_38_CANONICAL_AIA_ROUNDS"
            if positive else
            "STOP_URLSCAN_ROUTE_AND_CONTINUE_ONLY_WITH_NEW_LEGAL_FREE_HISTORICAL_OBSERVATION_SOURCE"
        ),
    }
    (out/"aia_urlscan_feasibility_receipt.json").write_text(
        json.dumps(receipt,indent=2,sort_keys=True,ensure_ascii=False)+"\n",encoding="utf-8"
    )
    print(json.dumps(receipt,sort_keys=True,ensure_ascii=False))
    return receipt

def main():
    a=argparse.ArgumentParser()
    a.add_argument("--registry",type=Path,required=True)
    a.add_argument("--out",type=Path,required=True)
    a.add_argument("--timeout",type=int,default=25)
    x=a.parse_args()
    run(x.registry,x.out,x.timeout)

if __name__=="__main__":
    main()
