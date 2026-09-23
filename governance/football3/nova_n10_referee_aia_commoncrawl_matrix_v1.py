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

class CCMatrixError(RuntimeError):
    pass

def req(c: bool, m: str) -> None:
    if not c:
        raise CCMatrixError(m)

def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def host(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()

def parse_iso(v: str) -> dt.datetime:
    x=dt.datetime.fromisoformat(v.replace("Z","+00:00"))
    if x.tzinfo is None:
        x=x.replace(tzinfo=dt.timezone.utc)
    return x.astimezone(dt.timezone.utc)

def parse_day(v: str) -> dt.datetime:
    return dt.datetime.strptime(v,"%Y-%m-%d").replace(tzinfo=dt.timezone.utc)

def parse_ts14(v: str) -> dt.datetime | None:
    if len(v)!=14 or not v.isdigit():
        return None
    try:
        return dt.datetime.strptime(v,"%Y%m%d%H%M%S").replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None

def window(row: dict[str,Any], days: int) -> tuple[dt.datetime,dt.datetime]:
    start=parse_day(row["published_date"])
    return start,start+dt.timedelta(days=days)

def fetch(url: str, timeout: int=20, limit: int=2_000_000) -> tuple[bytes,str,dict[str,str]]:
    rq=urllib.request.Request(url,headers={
        "User-Agent":"Football3-Nova-N10-AIACommonCrawlMatrix/1.0",
        "Accept":"application/json,text/plain,*/*;q=0.1",
    })
    with urllib.request.urlopen(rq,timeout=timeout,context=ssl.create_default_context()) as r:
        raw=r.read(limit+1)
        req(len(raw)<=limit,"RESPONSE_TOO_LARGE")
        return raw,r.geturl(),{k.lower():v for k,v in r.headers.items()}

def collection_intersects(c: dict[str,Any], start: dt.datetime, end: dt.datetime) -> bool:
    try:
        a=parse_iso(str(c["from"]))
        b=parse_iso(str(c["to"]))
    except Exception:
        return False
    return a < end and b >= start

def select_collections(collinfo: list[dict[str,Any]], start: dt.datetime, end: dt.datetime, allowed_host: str) -> list[dict[str,Any]]:
    out=[]
    for c in collinfo:
        if not isinstance(c,dict) or not all(k in c for k in ("id","cdx-api","from","to")):
            continue
        if host(str(c["cdx-api"]))!=allowed_host:
            continue
        if collection_intersects(c,start,end):
            out.append(c)
    return sorted(out,key=lambda x:str(x["from"]))

def query_url(endpoint: str, target: str) -> str:
    qs=urllib.parse.urlencode([
        ("url",target),
        ("output","json"),
        ("filter","status:200"),
    ])
    return endpoint+("&" if "?" in endpoint else "?")+qs

def parse_cdxj(raw: bytes) -> list[dict[str,Any]]:
    out=[]
    for line in raw.decode("utf-8","replace").splitlines():
        line=line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            x=json.loads(line)
        except Exception:
            continue
        if isinstance(x,dict):
            out.append(x)
    return out

def exact_url_match(a: str, b: str) -> bool:
    return a.rstrip("/")==b.rstrip("/")

def eligible(rows: list[dict[str,Any]], target: str, start: dt.datetime, end: dt.datetime) -> list[dict[str,Any]]:
    out=[]
    for r in rows:
        ts=str(r.get("timestamp",""))
        when=parse_ts14(ts)
        if when is None or not (start <= when < end):
            continue
        if str(r.get("status",""))!="200":
            continue
        u=str(r.get("url",""))
        if not exact_url_match(u,target):
            continue
        out.append({
            "timestamp":ts,
            "url":u,
            "status":"200",
            "digest":r.get("digest"),
            "mime":r.get("mime"),
            "mime_detected":r.get("mime-detected"),
        })
    return sorted(out,key=lambda x:x["timestamp"])

def query_collection(c: dict[str,Any], row: dict[str,Any], start: dt.datetime, end: dt.datetime, timeout: int, allowed_host: str) -> dict[str,Any]:
    u=query_url(str(c["cdx-api"]),row["url"])
    try:
        raw,final,headers=fetch(u,timeout,1_000_000)
        req(host(final)==allowed_host,"REDIRECT_OUTSIDE_COMMONCRAWL")
        parsed=parse_cdxj(raw)
        good=eligible(parsed,row["url"],start,end)
        return {
            "round":row["round"],
            "collection_id":c["id"],
            "collection_from":c["from"],
            "collection_to":c["to"],
            "query_url":u,
            "final_url":final,
            "response_sha256":sha256_bytes(raw),
            "response_bytes":len(raw),
            "content_type":headers.get("content-type"),
            "row_n":len(parsed),
            "eligible_n":len(good),
            "eligible":good,
            "error":None,
        }
    except Exception as e:
        return {
            "round":row["round"],
            "collection_id":c["id"],
            "collection_from":c["from"],
            "collection_to":c["to"],
            "query_url":u,
            "row_n":0,
            "eligible_n":0,
            "eligible":[],
            "error":f"{type(e).__name__}:{e}"[:500],
        }

def run(registry: Path, ledger_path: Path, out: Path, timeout: int=20) -> dict[str,Any]:
    p=json.loads(registry.read_text(encoding="utf-8"))
    ledger=json.loads(ledger_path.read_text(encoding="utf-8"))

    req(p["status"]=="DESIGN_LOCKED_ZERO_LABEL_METADATA_ONLY","STATUS")
    req(p["exact_base"]=="2000c097f866e78379ab0e8c1e29e50ee452bebf","EXACT_BASE")
    req(sha256_bytes(ledger_path.read_bytes())==p["parent"]["canonical_ledger_sha256"],"LEDGER_SHA")
    req(ledger["canonical_round_n"]==38 and [x["round"] for x in ledger["rows"]]==list(range(1,39)),"LEDGER_38")

    h=p["hard_rules"]
    req(h["result_labels_read"] is False and h["score_values_read"] is False,"ZERO_LABEL")
    req(h["match_payload_read"] is False and h["standings_payload_read"] is False and h["player_stats_payload_read"] is False,"NO_SPORT_PAYLOAD")
    req(h["warc_content_fetched"] is False and h["article_body_read"] is False,"NO_CONTENT")
    req(h["appointment_names_parsed"] is False,"NO_APPOINTMENT_BODY")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False,"NO_MODEL")
    req(h["paid_or_secret_source_allowed"] is False,"NO_PAID_SECRET")
    req(h["blind_collection_generation_allowed"] is False,"NO_BLIND_COLLECTIONS")
    req(h["candidate_weight"]==0 and h["matrix_delta"]==0,"ZERO_WEIGHT")

    src=p["source"]
    raw,final,headers=fetch(src["collinfo_url"],timeout,2_000_000)
    req(host(final)==src["allowed_host"],"COLLINFO_REDIRECT")
    collinfo=json.loads(raw.decode("utf-8"))
    req(isinstance(collinfo,list),"COLLINFO_SHAPE")
    days=int(p["witness_contract"]["window_days"])

    round_meta=[]
    tasks=[]
    for row in ledger["rows"]:
        start,end=window(row,days)
        cols=select_collections(collinfo,start,end,src["allowed_host"])
        round_meta.append({
            "round":row["round"],
            "published_date":row["published_date"],
            "official_url":row["url"],
            "window_start_utc":start.isoformat(),
            "window_end_exclusive_utc":end.isoformat(),
            "selected_collection_ids":[c["id"] for c in cols],
        })
        for c in cols:
            tasks.append((c,row,start,end))

    query_reports=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futs=[
            ex.submit(query_collection,c,row,start,end,timeout,src["allowed_host"])
            for c,row,start,end in tasks
        ]
        for fut in concurrent.futures.as_completed(futs):
            query_reports.append(fut.result())
    query_reports=sorted(query_reports,key=lambda x:(x["round"],str(x["collection_id"])))

    by_round={r["round"]:[] for r in ledger["rows"]}
    for q in query_reports:
        by_round[q["round"]].extend(
            [{**x,"collection_id":q["collection_id"]} for x in q["eligible"]]
        )

    round_reports=[]
    witnessed=[]
    for meta in round_meta:
        caps=sorted(by_round[meta["round"]],key=lambda x:(x["timestamp"],x["collection_id"]))
        rr={**meta,"eligible_capture_n":len(caps),"eligible_captures":caps}
        round_reports.append(rr)
        if caps:
            witnessed.append(meta["round"])

    gaps=[r for r in range(1,39) if r not in witnessed]
    selected_collection_set=sorted({cid for m in round_meta for cid in m["selected_collection_ids"]})
    error_n=sum(1 for q in query_reports if q.get("error"))
    classification=(
        p["decision_contract"]["positive_classification"]
        if witnessed else p["decision_contract"]["fail_classification"]
    )

    matrix={
        "schema_version":"football3-nova-n10-referee-aia-commoncrawl-matrix-v1",
        "competition":"Serie_A",
        "season":"2022/23",
        "canonical_ledger_sha256":p["parent"]["canonical_ledger_sha256"],
        "window_days":days,
        "round_n":38,
        "witness_round_n":len(witnessed),
        "witness_rounds":witnessed,
        "gap_rounds":gaps,
        "coverage":len(witnessed)/38.0,
        "selected_collection_ids":selected_collection_set,
        "rounds":round_reports,
    }
    matrix_raw=json.dumps(matrix,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
    matrix["matrix_sha256"]=sha256_bytes(matrix_raw)

    out.mkdir(parents=True,exist_ok=True)
    (out/"aia_commoncrawl_matrix.json").write_text(
        json.dumps(matrix,indent=2,sort_keys=True,ensure_ascii=False)+"\n",encoding="utf-8"
    )
    receipt={
        "schema_version":"football3-nova-n10-referee-aia-commoncrawl-matrix-receipt-v1",
        "status":"N10_REFEREE_AIA_COMMONCRAWL_MATRIX_COMPLETE",
        "classification":classification,
        "exact_base":p["exact_base"],
        "registry_sha256":sha256_bytes(registry.read_bytes()),
        "collinfo_sha256":sha256_bytes(raw),
        "collinfo_collection_n":len(collinfo),
        "selected_collection_n":len(selected_collection_set),
        "selected_collection_ids":selected_collection_set,
        "query_n":len(query_reports),
        "query_error_n":error_n,
        "round_n":38,
        "archive_witness_round_n":len(witnessed),
        "archive_witness_rounds":witnessed,
        "gap_rounds":gaps,
        "archive_coverage":len(witnessed)/38.0,
        "matrix_sha256":matrix["matrix_sha256"],
        "warc_content_fetched":False,
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
            "PRESERVE_COMMONCRAWL_COVERAGE_SIGNAL_AND_COMBINE_WITH_OTHER_INDEPENDENT_WITNESSES_BEFORE_FIXTURE_PIT_BINDING"
            if witnessed else
            "STOP_COMMONCRAWL_SEASON_MATRIX_AND_CONTINUE_ONLY_WITH_NEW_LEGAL_FREE_HISTORICAL_OBSERVATION_SOURCE"
        ),
    }
    (out/"aia_commoncrawl_matrix_receipt.json").write_text(
        json.dumps(receipt,indent=2,sort_keys=True,ensure_ascii=False)+"\n",encoding="utf-8"
    )
    print(json.dumps(receipt,sort_keys=True,ensure_ascii=False))
    return receipt

def main():
    a=argparse.ArgumentParser()
    a.add_argument("--registry",type=Path,required=True)
    a.add_argument("--ledger",type=Path,required=True)
    a.add_argument("--out",type=Path,required=True)
    a.add_argument("--timeout",type=int,default=20)
    x=a.parse_args()
    run(x.registry,x.ledger,x.out,x.timeout)

if __name__=="__main__":
    main()
