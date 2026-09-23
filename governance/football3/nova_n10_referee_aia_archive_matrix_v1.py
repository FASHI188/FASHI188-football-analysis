#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import ssl
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

class MatrixError(RuntimeError): pass

def req(c: bool,m: str)->None:
    if not c: raise MatrixError(m)

def sha256_bytes(b: bytes)->str:
    return hashlib.sha256(b).hexdigest()

def domain_ok(url: str,suffix: str)->bool:
    h=(urllib.parse.urlparse(url).hostname or "").lower()
    s=suffix.lower()
    return h==s or h.endswith("."+s)

def host_ok(url: str,host: str)->bool:
    return (urllib.parse.urlparse(url).hostname or "").lower()==host.lower()

def fetch(url: str,timeout: int,limit: int)->tuple[bytes,str,dict[str,str]]:
    rq=urllib.request.Request(url,headers={
        "User-Agent":"Football3-Nova-N10-AIAArchiveMatrix/1.0",
        "Accept":"application/json,text/plain,*/*;q=0.2",
    })
    with urllib.request.urlopen(rq,timeout=timeout,context=ssl.create_default_context()) as r:
        raw=r.read(limit+1)
        req(len(raw)<=limit,"RESPONSE_TOO_LARGE")
        return raw,r.geturl(),{k.lower():v for k,v in r.headers.items()}

def parse_day(v: str)->dt.datetime:
    return dt.datetime.strptime(v,"%Y-%m-%d").replace(tzinfo=dt.timezone.utc)

def ts14(v: str)->dt.datetime:
    return dt.datetime.strptime(v,"%Y%m%d%H%M%S").replace(tzinfo=dt.timezone.utc)

def window(row: dict[str,Any],days: int)->tuple[dt.datetime,dt.datetime]:
    start=parse_day(row["published_date"])
    return start,start+dt.timedelta(days=days+1)

def query_url(endpoint: str,target: str,start: dt.datetime,end_exclusive: dt.datetime,provider: str)->str:
    end=end_exclusive-dt.timedelta(seconds=1)
    if provider=="WAYBACK_CDX":
        q=[
            ("url",target),("output","json"),
            ("fl","timestamp,original,statuscode,digest,mimetype"),
            ("filter","statuscode:200"),
            ("from",start.strftime("%Y%m%d%H%M%S")),
            ("to",end.strftime("%Y%m%d%H%M%S")),
            ("collapse","digest"),("limit","50"),
        ]
    else:
        q=[
            ("url",target),("output","json"),
            ("fl","url,timestamp,status,digest,mime"),
            ("filter","status:200"),
            ("from",start.strftime("%Y%m%d%H%M%S")),
            ("to",end.strftime("%Y%m%d%H%M%S")),
            ("limit","50"),
        ]
    return endpoint+"?"+urllib.parse.urlencode(q)

def normalize_row(r: dict[str,Any])->dict[str,str]:
    return {
        "timestamp":str(r.get("timestamp","")),
        "original":str(r.get("original") or r.get("url") or ""),
        "statuscode":str(r.get("statuscode") or r.get("status") or ""),
        "digest":str(r.get("digest","")),
        "mimetype":str(r.get("mimetype") or r.get("mime") or ""),
    }

def parse_wayback(raw: bytes)->list[dict[str,str]]:
    x=json.loads(raw.decode("utf-8"))
    if not x: return []
    req(isinstance(x,list) and isinstance(x[0],list),"WAYBACK_SHAPE")
    head=x[0]
    return [normalize_row({str(k):v for k,v in zip(head,row)}) for row in x[1:] if isinstance(row,list) and len(row)==len(head)]

def parse_arquivo(raw: bytes)->list[dict[str,str]]:
    text=raw.decode("utf-8","replace").strip()
    if not text: return []
    try:
        x=json.loads(text)
        if isinstance(x,list):
            if x and isinstance(x[0],list):
                head=x[0]
                return [normalize_row({str(k):v for k,v in zip(head,row)}) for row in x[1:] if isinstance(row,list) and len(row)==len(head)]
            return [normalize_row(r) for r in x if isinstance(r,dict)]
        if isinstance(x,dict):
            arr=x.get("results") or x.get("items")
            if isinstance(arr,list):
                return [normalize_row(r) for r in arr if isinstance(r,dict)]
            return [normalize_row(x)]
    except json.JSONDecodeError:
        pass
    out=[]
    for line in text.splitlines():
        try:
            x=json.loads(line)
            if isinstance(x,dict): out.append(normalize_row(x))
        except Exception:
            pass
    return out

def eligible(rows: list[dict[str,str]],start: dt.datetime,end: dt.datetime,suffix: str)->list[dict[str,str]]:
    out=[]
    for r in rows:
        v=r.get("timestamp","")
        if len(v)!=14 or not v.isdigit() or r.get("statuscode")!="200": continue
        try: t=ts14(v)
        except Exception: continue
        if not (start<=t<end): continue
        if not domain_ok(r.get("original",""),suffix): continue
        out.append(r)
    return sorted(out,key=lambda x:x["timestamp"])

def provider_query(provider: dict[str,Any],row: dict[str,Any],start: dt.datetime,end: dt.datetime,timeout: int,suffix: str)->dict[str,Any]:
    u=query_url(provider["endpoint"],row["url"],start,end,provider["id"])
    try:
        raw,final,headers=fetch(u,timeout,1_000_000)
        req(host_ok(final,provider["allowed_host"]),"ARCHIVE_REDIRECT_OUTSIDE_PROVIDER")
        parsed=parse_wayback(raw) if provider["id"]=="WAYBACK_CDX" else parse_arquivo(raw)
        good=eligible(parsed,start,end,suffix)
        return {
            "provider":provider["id"],"query_url":u,"final_url":final,
            "response_sha256":sha256_bytes(raw),"row_n":len(parsed),
            "eligible_n":len(good),"eligible":good,
            "content_type":headers.get("content-type"),"error":None
        }
    except Exception as e:
        return {
            "provider":provider["id"],"query_url":u,"row_n":0,"eligible_n":0,
            "eligible":[],"error":f"{type(e).__name__}:{e}"[:400]
        }

def audit_round(row: dict[str,Any],providers: list[dict[str,Any]],days: int,timeout: int,suffix: str)->dict[str,Any]:
    start,end=window(row,days)
    reports=[]
    first=provider_query(providers[0],row,start,end,timeout,suffix)
    reports.append(first)
    selected=None
    if first["eligible"]:
        selected=first["eligible"][0] | {"provider":first["provider"]}
    else:
        second=provider_query(providers[1],row,start,end,timeout,suffix)
        reports.append(second)
        if second["eligible"]:
            selected=second["eligible"][0] | {"provider":second["provider"]}
    return {
        "round":row["round"],"published_date":row["published_date"],
        "title":row["title"],"url":row["url"],
        "witness_window_start_utc":start.isoformat(),
        "witness_window_end_exclusive_utc":end.isoformat(),
        "provider_reports":[{k:v for k,v in x.items() if k!="eligible"} for x in reports],
        "selected_capture":selected,
        "archive_metadata_witness_found":selected is not None,
    }

def run(registry: Path,ledger_path: Path,out: Path,timeout: int=15)->dict[str,Any]:
    p=json.loads(registry.read_text(encoding="utf-8"))
    ledger=json.loads(ledger_path.read_text(encoding="utf-8"))
    req(p["status"]=="DESIGN_LOCKED_ZERO_LABEL","STATUS")
    req(p["exact_base"]=="c82910ca1da103bd9528c1de58680c07cacdd34e","EXACT_BASE")
    req(ledger["status"]=="FROZEN_FROM_INDEX_INVENTORY_V2","LEDGER_STATUS")
    req(ledger["parent_inventory_sha256"]==p["expected_parent_inventory_sha256"],"PARENT_INVENTORY_SHA")
    req(ledger["canonical_round_n"]==38 and [x["round"] for x in ledger["rows"]]==list(range(1,39)),"LEDGER_38")
    req(all(domain_ok(x["url"],p["witness_contract"]["official_domain_suffix"]) for x in ledger["rows"]),"LEDGER_DOMAIN")
    req(all(not any(t in x["title"].casefold() for t in ("variaz","anticipo")) for x in ledger["rows"]),"LEDGER_CANONICAL_ONLY")
    h=p["hard_rules"]; e=p["evidence_semantics"]; w=p["witness_contract"]
    req(h["result_labels_read"] is False and h["score_values_read"] is False,"ZERO_LABEL")
    req(h["appointment_names_parsed"] is False and h["article_body_read"] is False and h["archived_page_content_fetched"] is False,"NO_CONTENT")
    req(h["match_payload_read"] is False and h["standings_payload_read"] is False and h["player_stats_payload_read"] is False,"NO_SPORT_PAYLOAD")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False,"NO_MODEL")
    req(h["paid_or_secret_source_allowed"] is False,"NO_SECRET")
    req(h["candidate_weight"]==0 and h["matrix_delta"]==0,"ZERO_WEIGHT")
    req(e["formal_available_at_proven"] is False and e["fixture_level_binding_complete"] is False,"NO_PIT_OVERCLAIM")
    req(w["archived_content_fetch_allowed"] is False,"NO_ARCHIVE_CONTENT")

    days=int(w["window_days"])
    reports=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        futs={
            ex.submit(audit_round,row,p["providers"],days,timeout,w["official_domain_suffix"]):row["round"]
            for row in ledger["rows"]
        }
        for fut in concurrent.futures.as_completed(futs):
            try:
                reports.append(fut.result())
            except Exception as exc:
                rnd=futs[fut]
                reports.append({"round":rnd,"archive_metadata_witness_found":False,"fatal_error":f"{type(exc).__name__}:{exc}"[:400]})
    reports=sorted(reports,key=lambda x:x["round"])
    witnessed=[x["round"] for x in reports if x.get("archive_metadata_witness_found")]
    gaps=[x["round"] for x in reports if not x.get("archive_metadata_witness_found")]
    provider_counts={}
    for x in reports:
        s=x.get("selected_capture")
        if s:
            provider_counts[s["provider"]]=provider_counts.get(s["provider"],0)+1
    coverage=len(witnessed)/38.0
    classification="POSITIVE_SIGNAL" if witnessed else "STOP_DATA_COVERAGE"

    matrix={
        "schema_version":"football3-nova-n10-referee-aia-archive-witness-matrix-v1",
        "competition":"Serie_A","season":"2022/23",
        "ledger_sha256":sha256_bytes(ledger_path.read_bytes()),
        "parent_inventory_sha256":ledger["parent_inventory_sha256"],
        "witness_window_days":days,
        "round_n":38,"witness_round_n":len(witnessed),
        "witness_rounds":witnessed,"gap_rounds":gaps,
        "coverage":coverage,"provider_counts":provider_counts,
        "formal_available_at_proven":False,
        "fixture_level_binding_complete":False,
        "rows":reports,
    }
    matrix_raw=json.dumps(matrix,sort_keys=True,ensure_ascii=False,separators=(",",":")).encode()
    matrix["matrix_sha256"]=sha256_bytes(matrix_raw)
    out.mkdir(parents=True,exist_ok=True)
    (out/"aia_archive_witness_matrix.json").write_text(json.dumps(matrix,indent=2,sort_keys=True,ensure_ascii=False)+"\n",encoding="utf-8")
    receipt={
        "schema_version":"football3-nova-n10-referee-aia-archive-matrix-receipt-v1",
        "status":"N10_REFEREE_AIA_ARCHIVE_MATRIX_COMPLETE",
        "classification":classification,
        "signal_subtype":e["signal_subtype"] if witnessed else None,
        "exact_base":p["exact_base"],
        "registry_sha256":sha256_bytes(registry.read_bytes()),
        "ledger_sha256":matrix["ledger_sha256"],
        "parent_inventory_sha256":ledger["parent_inventory_sha256"],
        "round_n":38,"archive_witness_round_n":len(witnessed),
        "archive_witness_rounds":witnessed,"gap_rounds":gaps,
        "archive_coverage":coverage,"provider_counts":provider_counts,
        "matrix_sha256":matrix["matrix_sha256"],
        "archived_page_content_fetched":False,
        "article_body_read":False,"appointment_names_parsed":False,
        "independent_archive_metadata_observed":bool(witnessed),
        "formal_available_at_proven":False,
        "fixture_level_binding_complete":False,
        "full_big5_data_ready":False,"referee_oof_allowed":False,
        "result_labels_read":0,"score_values_read":0,
        "training_performed":False,"scoring_performed":False,
        "formal_v2_changed":False,"current_changed":False,"production_changed":False,
        "candidate_weight":0,"matrix_delta":0,
        "next_step":(
            "PRESERVE_ARCHIVE_COVERAGE_SIGNAL; BIND FIXTURE CUTOFFS AND CLOSE GAP_ROUNDS BEFORE ANY APPOINTMENT_CONTENT_OR_REFEREE_OOF"
            if witnessed else
            "STOP_ARCHIVE_MATRIX_ROUTE_AND_CONTINUE_ONLY_WITH_NEW_LEGAL_FREE_ARCHIVE_SOURCE"
        )
    }
    (out/"aia_archive_matrix_receipt.json").write_text(json.dumps(receipt,indent=2,sort_keys=True,ensure_ascii=False)+"\n",encoding="utf-8")
    print(json.dumps(receipt,sort_keys=True,ensure_ascii=False))
    return receipt

def main():
    a=argparse.ArgumentParser()
    a.add_argument("--registry",type=Path,required=True)
    a.add_argument("--ledger",type=Path,required=True)
    a.add_argument("--out",type=Path,required=True)
    a.add_argument("--timeout",type=int,default=15)
    x=a.parse_args(); run(x.registry,x.ledger,x.out,x.timeout)
if __name__=="__main__": main()
