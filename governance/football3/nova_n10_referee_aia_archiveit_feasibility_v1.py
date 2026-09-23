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

class ArchiveItError(RuntimeError):
    pass

def req(c: bool, m: str) -> None:
    if not c:
        raise ArchiveItError(m)

def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def host(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()

def date_bounds(d: str) -> tuple[str,str]:
    start=dt.datetime.strptime(d,"%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    end=start+dt.timedelta(days=8)
    return start.strftime("%Y%m%d%H%M%S"),(end-dt.timedelta(seconds=1)).strftime("%Y%m%d%H%M%S")

def build_query(endpoint: str, original: str, published_date: str) -> str:
    frm,to=date_bounds(published_date)
    qs=urllib.parse.urlencode([
        ("url",original),
        ("from",frm),
        ("to",to),
        ("fl","timestamp,original,statuscode"),
        ("filter","statuscode:200"),
    ])
    return endpoint+"?"+qs

def request_bytes(url: str, timeout: int=25, limit: int=2_000_000) -> tuple[int,bytes,str,dict[str,str]]:
    rq=urllib.request.Request(url,headers={
        "User-Agent":"Football3-Nova-N10-ArchiveItFeasibility/1.0",
        "Accept":"text/plain,*/*;q=0.1",
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

def parse_cdx(raw: bytes) -> list[dict[str,str]]:
    out=[]
    for line in raw.decode("utf-8","replace").splitlines():
        line=line.strip()
        if not line:
            continue
        parts=line.split()
        if len(parts)<3:
            continue
        ts,orig,status=parts[0],parts[1],parts[2]
        if len(ts)==14 and ts.isdigit() and status=="200":
            out.append({"timestamp":ts,"original":orig,"statuscode":status})
    return out

def in_window(ts: str, published_date: str) -> bool:
    frm,to=date_bounds(published_date)
    return frm <= ts <= to

def run(registry: Path,out: Path,timeout: int=25)->dict[str,Any]:
    p=json.loads(registry.read_text(encoding="utf-8"))
    req(p["status"]=="DESIGN_LOCKED_ZERO_LABEL_METADATA_ONLY","STATUS")
    req(p["exact_base"]=="ace6bb4d6db5c332406d759d31a5ac25afe8b132","EXACT_BASE")
    req(p["parent"]["canonical_ledger_sha256"]=="0be7e00db3f370808aa8d7caab69bc24c7d9b117efab3f6dccd2fcfebb885a9b","LEDGER_SHA")

    h=p["hard_rules"]
    req(h["result_labels_read"] is False and h["score_values_read"] is False,"ZERO_LABEL")
    req(h["match_payload_read"] is False and h["standings_payload_read"] is False and h["player_stats_payload_read"] is False,"NO_SPORT_PAYLOAD")
    req(h["archive_content_fetched"] is False and h["article_body_read"] is False,"NO_CONTENT")
    req(h["appointment_names_parsed"] is False,"NO_APPOINTMENT_BODY")
    req(h["collection_id_guessing_allowed"] is False,"NO_COLLECTION_GUESSING")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False,"NO_MODEL")
    req(h["paid_or_secret_source_allowed"] is False,"NO_PAID_SECRET")
    req(h["candidate_weight"]==0 and h["matrix_delta"]==0,"ZERO_WEIGHT")
    req(len(p["samples"])==5,"FROZEN_SAMPLE_N")

    src=p["source"]
    reports=[]
    errors=[]
    positive_rounds=[]
    for sample in p["samples"]:
        q=build_query(src["cdx_endpoint"],sample["url"],sample["published_date"])
        try:
            status,raw,final,headers=request_bytes(q,timeout)
            req(host(final)==src["allowed_host"],"REDIRECT_OUTSIDE_ARCHIVEIT")
            rows=parse_cdx(raw) if 200 <= status < 300 else []
            eligible=[r for r in rows if in_window(r["timestamp"],sample["published_date"]) and r["original"]==sample["url"]]
            reports.append({
                "round":sample["round"],
                "published_date":sample["published_date"],
                "official_url":sample["url"],
                "query_url":q,
                "final_url":final,
                "http_status":status,
                "response_bytes":len(raw),
                "response_sha256":sha256_bytes(raw),
                "content_type":headers.get("content-type"),
                "cdx_row_n":len(rows),
                "eligible_capture_n":len(eligible),
                "eligible_captures":eligible,
            })
            if eligible:
                positive_rounds.append(sample["round"])
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
        "schema_version":"football3-nova-n10-referee-aia-archiveit-feasibility-receipt-v1",
        "status":"N10_REFEREE_AIA_ARCHIVEIT_FEASIBILITY_COMPLETE",
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
        "collection_id_guessing":False,
        "archive_content_fetched":False,
        "article_body_read":False,
        "appointment_names_parsed":False,
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
            "IF_POSITIVE_EXPAND_ARCHIVEIT_CDX_TO_ALL_38_CANONICAL_AIA_ROUNDS_METADATA_ONLY"
            if positive else
            "STOP_ARCHIVEIT_ROUTE_AND_CONTINUE_ONLY_WITH_NEW_LEGAL_FREE_HISTORICAL_OBSERVATION_SOURCE"
        ),
    }
    (out/"aia_archiveit_feasibility_receipt.json").write_text(
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
