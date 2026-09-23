#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

class DiscoveryError(RuntimeError): pass

def req(c: bool, m: str) -> None:
    if not c: raise DiscoveryError(m)

def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def host_ok(url: str, host: str) -> bool:
    return (urllib.parse.urlparse(url).hostname or "").lower() == host.lower()

def request(url: str, method: str, timeout: int, limit: int) -> tuple[int,bytes,str,dict[str,str]]:
    rq=urllib.request.Request(url,method=method,headers={
        "User-Agent":"Football3-Nova-N10-LegaDAPI/1.0",
        "Accept":"application/json,text/html,text/plain,*/*;q=0.2",
    })
    try:
        with urllib.request.urlopen(rq,timeout=timeout,context=ssl.create_default_context()) as r:
            data=b"" if method=="HEAD" else r.read(limit+1)
            req(len(data)<=limit,"RESPONSE_TOO_LARGE")
            return int(getattr(r,"status",200)),data,r.geturl(),{k.lower():v for k,v in r.headers.items()}
    except urllib.error.HTTPError as e:
        data=b"" if method=="HEAD" else e.read(min(limit,65536))
        return int(e.code),data,e.geturl(),{k.lower():v for k,v in e.headers.items()}

def html_title(raw: bytes) -> str | None:
    m=re.search(br"<title\b[^>]*>(.*?)</title>",raw,re.I|re.S)
    if not m:return None
    return re.sub(r"\s+"," ",m.group(1).decode("utf-8","replace")).strip()[:240]

def schema_paths(raw: bytes) -> tuple[list[str],list[str]]:
    try:x=json.loads(raw.decode("utf-8"))
    except Exception:return [],[]
    if not isinstance(x,dict):return [],[]
    paths=x.get("paths")
    if not isinstance(paths,dict):return [],[]
    names=sorted(str(k) for k in paths.keys())
    preferred=[p for p in names if any(t in p.casefold() for t in ("content","news","article","tag","category","search"))]
    forbidden=[p for p in names if any(t in p.casefold() for t in ("match","score","result","standing","player","stat"))]
    return preferred,forbidden

def run(registry: Path,out: Path,timeout: int=20)->dict[str,Any]:
    p=json.loads(registry.read_text(encoding="utf-8"))
    req(p["status"]=="DESIGN_LOCKED_ZERO_LABEL","STATUS")
    req(p["exact_base"]=="69fa131dc2429b584cf88f93574b0d9284735d0a","EXACT_BASE")
    h=p["hard_rules"]
    req(h["result_labels_read"] is False and h["score_values_read"] is False,"ZERO_LABEL")
    req(h["match_payload_read"] is False and h["standings_payload_read"] is False and h["player_stats_payload_read"] is False,"NO_SPORT_PAYLOADS")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False,"NO_MODEL")
    req(h["paid_or_secret_source_allowed"] is False,"NO_SECRET")
    req(h["schema_path_names_only"] is True and h["article_body_read"] is False,"METADATA_ONLY")

    reports=[]; errors=[]; preferred_paths=set(); forbidden_paths=set()
    for c in p["candidates"]:
        kind=c["kind"]; method="HEAD" if kind=="content_root" else "GET"
        limit=4096 if kind=="service_root" else (512000 if kind=="api_schema" else 262144)
        try:
            status,raw,final,headers=request(c["url"],method,timeout,limit)
            req(host_ok(final,p["host"]),"REDIRECT_OUTSIDE_DAPI")
            report={
                "id":c["id"],"kind":kind,"method":method,"source_url":c["url"],"final_url":final,
                "http_status":status,"content_type":headers.get("content-type"),
                "content_length_header":headers.get("content-length"),
                "response_body_read":method=="GET","response_bytes":len(raw),
                "response_sha256":sha256_bytes(raw) if raw else None,
            }
            if kind=="service_root" and raw:
                report["root_text"]=raw.decode("utf-8","replace").strip()[:300]
            elif kind=="documentation" and raw:
                report["html_title"]=html_title(raw)
            elif kind=="api_schema" and raw:
                pref,forbid=schema_paths(raw)
                report["preferred_metadata_path_names"]=pref
                report["forbidden_sports_path_name_n"]=len(forbid)
                preferred_paths.update(pref); forbidden_paths.update(forbid)
            reports.append(report)
        except Exception as e:
            errors.append({"id":c["id"],"url":c["url"],"error":f"{type(e).__name__}:{e}"[:400]})

    public_root=any(r["id"]=="ROOT" and 200<=r["http_status"]<300 for r in reports)
    schema_signal=bool(preferred_paths)
    content_root_signal=any(r["kind"]=="content_root" and r["http_status"] in (200,204,301,302,400,401,403,405,422) for r in reports)
    classification="POSITIVE_SIGNAL_SOURCE_FEASIBILITY" if public_root and (schema_signal or content_root_signal) else "STOP_DATA_COVERAGE"

    out.mkdir(parents=True,exist_ok=True)
    receipt={
        "schema_version":"football3-nova-n10-referee-lega-dapi-discovery-receipt-v1",
        "status":"N10_REFEREE_LEGA_DAPI_DISCOVERY_COMPLETE",
        "classification":classification,
        "exact_base":p["exact_base"],
        "registry_sha256":sha256_bytes(registry.read_bytes()),
        "probe_n":len(p["candidates"]),"report_n":len(reports),"error_n":len(errors),
        "reports":reports,"errors":errors,
        "public_service_root":public_root,
        "preferred_metadata_path_names":sorted(preferred_paths),
        "forbidden_sports_path_name_n":len(forbidden_paths),
        "content_root_signal":content_root_signal,
        "article_body_read":False,
        "match_payload_read":False,"standings_payload_read":False,"player_stats_payload_read":False,
        "full_big5_data_ready":False,"referee_oof_allowed":False,
        "result_labels_read":0,"score_values_read":0,"training_performed":False,"scoring_performed":False,
        "formal_v2_changed":False,"current_changed":False,"production_changed":False,
        "candidate_weight":0,"matrix_delta":0,
        "next_step":(
            "IF_METADATA_ROUTE_DISCOVERED_FREEZE_EXACT_CONTENT_ENDPOINT_QUERY_FOR_REFEREE_PUBLICATION_METADATA"
            if classification=="POSITIVE_SIGNAL_SOURCE_FEASIBILITY" else
            "STOP_DAPI_DISCOVERY_ROUTE_AND_CONTINUE_ONLY_WITH_NEW_LEGAL_FREE_SOURCE"
        ),
    }
    (out/"lega_dapi_discovery_receipt.json").write_text(json.dumps(receipt,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(receipt,sort_keys=True)); return receipt

def main():
    a=argparse.ArgumentParser(); a.add_argument("--registry",type=Path,required=True); a.add_argument("--out",type=Path,required=True); a.add_argument("--timeout",type=int,default=20)
    x=a.parse_args(); run(x.registry,x.out,x.timeout)
if __name__=="__main__":main()
