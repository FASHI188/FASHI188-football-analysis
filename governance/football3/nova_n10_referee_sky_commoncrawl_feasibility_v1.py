#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import ssl
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from nova_n10_referee_sky_combined_freeze_v1 import (
    download_artifact_zip,
    read_unique_suffix,
    sha256_bytes,
)

UTC=dt.timezone.utc

class SkyCommonCrawlError(RuntimeError):
    pass

def req(cond: bool, msg: str) -> None:
    if not cond:
        raise SkyCommonCrawlError(msg)

def parse_z(v: str) -> dt.datetime:
    x=dt.datetime.fromisoformat(v.replace("Z","+00:00"))
    if x.tzinfo is None:
        x=x.replace(tzinfo=UTC)
    return x.astimezone(UTC)

def parse_cc_iso(v: str) -> dt.datetime:
    x=dt.datetime.fromisoformat(v.replace("Z","+00:00"))
    if x.tzinfo is None:
        x=x.replace(tzinfo=UTC)
    return x.astimezone(UTC)

def parse_ts14(v: str) -> dt.datetime | None:
    if not re.fullmatch(r"\d{14}",v or ""):
        return None
    try:
        return dt.datetime.strptime(v,"%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except Exception:
        return None

def host(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()

def normalize_identity(url: str) -> tuple[str,str] | None:
    try:
        p=urllib.parse.urlparse(url)
    except Exception:
        return None
    h=(p.hostname or "").lower()
    if h.startswith("www."):
        h=h[4:]
    path=urllib.parse.unquote(p.path or "")
    path=re.sub(r"/amp/?$","",path,flags=re.I)
    path=path.rstrip("/") or "/"
    return h,path

def fetch(
    url: str,
    *,
    allowed_host: str,
    timeout: int,
    limit: int,
    user_agent: str,
) -> tuple[bytes,str,dict[str,str]]:
    req(host(url)==allowed_host,f"REQUEST_HOST:{url}")
    request=urllib.request.Request(
        url,
        headers={
            "User-Agent":user_agent,
            "Accept":"application/json,text/plain,*/*;q=0.1",
        },
    )
    with urllib.request.urlopen(request,timeout=timeout,context=ssl.create_default_context()) as r:
        final=r.geturl()
        req(host(final)==allowed_host,f"REDIRECT_HOST:{final}")
        raw=r.read(limit+1)
        req(len(raw)<=limit,"RESPONSE_TOO_LARGE")
        return raw,final,{k.lower():v for k,v in r.headers.items()}

def collection_intersects(c: dict[str,Any], start: dt.datetime, end: dt.datetime) -> bool:
    try:
        a=parse_cc_iso(str(c["from"]))
        b=parse_cc_iso(str(c["to"]))
    except Exception:
        return False
    return a < end and b >= start

def select_collections(
    collinfo: list[dict[str,Any]],
    start: dt.datetime,
    end: dt.datetime,
    allowed_host: str,
) -> list[dict[str,Any]]:
    out=[]
    for c in collinfo:
        if not isinstance(c,dict):
            continue
        if not all(k in c for k in ("id","cdx-api","from","to")):
            continue
        if host(str(c["cdx-api"]))!=allowed_host:
            continue
        if collection_intersects(c,start,end):
            out.append(c)
    return sorted(out,key=lambda x:(str(x["from"]),str(x["id"])))

def prefix_query_url(endpoint: str, sky_url: str) -> str:
    ident=normalize_identity(sky_url)
    req(ident is not None,"SKY_URL_IDENTITY")
    h,path=ident
    target=f"{h}{path}"
    params=[
        ("url",target),
        ("matchType","prefix"),
        ("output","json"),
        ("filter","status:200"),
    ]
    return endpoint+("&" if "?" in endpoint else "?")+urllib.parse.urlencode(params)

def parse_cdxj(raw: bytes) -> list[dict[str,Any]]:
    out=[]
    for line in raw.decode("utf-8","replace").splitlines():
        line=line.strip()
        if not line:
            continue
        try:
            obj=json.loads(line)
        except Exception:
            continue
        if isinstance(obj,dict):
            out.append(obj)
    return out

def eligible_rows(
    rows: list[dict[str,Any]],
    sky_url: str,
    lower: dt.datetime,
    upper: dt.datetime,
) -> list[dict[str,Any]]:
    target=normalize_identity(sky_url)
    out=[]
    for row in rows:
        if str(row.get("status",""))!="200":
            continue
        mime=str(row.get("mime") or row.get("mime-detected") or "").lower()
        if "html" not in mime:
            continue
        u=str(row.get("url",""))
        if normalize_identity(u)!=target:
            continue
        ts=str(row.get("timestamp",""))
        when=parse_ts14(ts)
        if when is None:
            continue
        if not (lower <= when < upper):
            continue
        out.append({
            "timestamp":ts,
            "capture_utc":when.isoformat().replace("+00:00","Z"),
            "url":u,
            "status":"200",
            "mime":row.get("mime"),
            "mime_detected":row.get("mime-detected"),
            "digest":row.get("digest"),
        })
    return sorted(out,key=lambda x:(x["timestamp"],x["url"]))

def acquire_parent(registry: dict[str,Any], token: str) -> tuple[dict[str,Any],dict[str,Any],dict[str,Any]]:
    p=registry["parent"]
    raw=download_artifact_zip(registry["repository"],int(p["artifact_id"]),token)
    req(sha256_bytes(raw)==p["artifact_zip_sha256"],"PARENT_ARTIFACT_SHA")
    _,pit_raw,pit=read_unique_suffix(raw,p["pit_binding_suffix"])
    _,fixture_raw,fixture=read_unique_suffix(raw,p["fixture_schedule_suffix"])
    req(sha256_bytes(pit_raw)==p["pit_binding_sha256"],"PARENT_PIT_SHA")
    req(sha256_bytes(fixture_raw)==p["fixture_schedule_sha256"],"PARENT_FIXTURE_SHA")
    return pit,fixture,{
        "artifact_zip_sha256":sha256_bytes(raw),
        "pit_binding_sha256":sha256_bytes(pit_raw),
        "fixture_schedule_sha256":sha256_bytes(fixture_raw),
    }

def run(registry_path: Path, out: Path, token: str) -> dict[str,Any]:
    registry=json.loads(registry_path.read_text(encoding="utf-8"))
    req(registry["status"]=="DESIGN_LOCKED_ZERO_LABEL_METADATA_ONLY","STATUS")
    req(registry["exact_base"]=="f6e1b332424e0e2203c6943d3446328fe4778f1a","EXACT_BASE")
    hard=registry["hard_rules"]
    req(hard["wayback_requery_allowed"] is False,"NO_WAYBACK_REQUERY")
    req(hard["result_labels_read"] is False and hard["score_values_read"] is False,"ZERO_LABEL")
    req(hard["score_or_result_semantic_parse"] is False,"NO_RESULT_PARSE")
    req(hard["referee_assignment_body_parsed"] is False,"NO_ASSIGNMENT_BODY")
    req(hard["sky_article_body_reacquired"] is False,"NO_SKY_BODY")
    req(hard["commoncrawl_warc_content_fetched"] is False,"NO_WARC")
    req(hard["training_allowed"] is False and hard["scoring_allowed"] is False,"NO_MODEL")
    req(hard["paid_or_secret_source_allowed"] is False,"NO_PAID_SECRET")
    req(hard["candidate_weight"]==0 and hard["matrix_delta"]==0,"ZERO_WEIGHT")

    pit,fixture,parent_prov=acquire_parent(registry,token)
    req(fixture.get("complete") is True and int(fixture.get("fixture_n",0))==380,"FIXTURE_PARENT")
    rows={int(r["round"]):r for r in pit["rows"]}
    sample_rounds=[int(x["round"]) for x in registry["samples"]]
    req(sample_rounds==[8,9,38],"FROZEN_SAMPLES")
    unresolved={int(x) for x in registry["parent"]["unresolved_rounds"]}
    req(set(sample_rounds).issubset(unresolved),"SAMPLES_NOT_UNRESOLVED")
    for rnd in sample_rounds:
        req(rnd in rows,f"PARENT_ROW_MISSING:R{rnd}")
        req(rows[rnd]["binding_status"]=="FAIL",f"SAMPLE_NOT_FAIL:R{rnd}")

    src=registry["source"]
    raw,final,headers=fetch(
        src["collinfo_url"],
        allowed_host=src["allowed_host"],
        timeout=int(src["request_timeout_seconds"]),
        limit=int(src["max_collinfo_bytes"]),
        user_agent=src["user_agent"],
    )
    collinfo=json.loads(raw.decode("utf-8"))
    req(isinstance(collinfo,list),"COLLINFO_SHAPE")

    tolerance=dt.timedelta(seconds=int(registry["witness_contract"]["source_visible_timestamp_early_tolerance_seconds"]))
    sample_reports=[]
    query_errors=[]
    positive_rounds=[]
    query_count=0
    last_query_time=None

    for rnd in sample_rounds:
        prow=rows[rnd]
        sky_pub=parse_z(prow["sky_visible_published_utc"])
        cutoff=parse_z(prow["first_fixture_cutoff_utc"])
        req(sky_pub < cutoff,f"PUB_NOT_PRE_CUTOFF:R{rnd}")
        lower=sky_pub-tolerance
        cols=select_collections(collinfo,sky_pub,cutoff,src["allowed_host"])
        queries=[]
        captures=[]
        for c in cols:
            if last_query_time is not None:
                elapsed=time.monotonic()-last_query_time
                sleep_for=float(src["min_seconds_between_queries"])-elapsed
                if sleep_for>0:
                    time.sleep(sleep_for)
            q=prefix_query_url(str(c["cdx-api"]),prow["sky_url"])
            query_count+=1
            try:
                qraw,qfinal,qheaders=fetch(
                    q,
                    allowed_host=src["allowed_host"],
                    timeout=int(src["request_timeout_seconds"]),
                    limit=int(src["max_query_bytes"]),
                    user_agent=src["user_agent"],
                )
                last_query_time=time.monotonic()
                parsed=parse_cdxj(qraw)
                good=eligible_rows(parsed,prow["sky_url"],lower,cutoff)
                queries.append({
                    "collection_id":c["id"],
                    "collection_from":c["from"],
                    "collection_to":c["to"],
                    "query_url":q,
                    "final_url":qfinal,
                    "response_sha256":sha256_bytes(qraw),
                    "response_bytes":len(qraw),
                    "row_n":len(parsed),
                    "eligible_n":len(good),
                    "error":None,
                })
                captures.extend([{**x,"collection_id":c["id"]} for x in good])
            except Exception as exc:
                last_query_time=time.monotonic()
                err=f"{type(exc).__name__}:{exc}"[:800]
                queries.append({
                    "collection_id":c["id"],
                    "collection_from":c["from"],
                    "collection_to":c["to"],
                    "query_url":q,
                    "row_n":0,
                    "eligible_n":0,
                    "error":err,
                })
                query_errors.append({
                    "round":rnd,
                    "collection_id":c["id"],
                    "query_url":q,
                    "error":err,
                })
        captures=sorted(captures,key=lambda x:(x["timestamp"],x["collection_id"]))
        if captures:
            positive_rounds.append(rnd)
        sample_reports.append({
            "round":rnd,
            "sky_url":prow["sky_url"],
            "sky_visible_published_utc":prow["sky_visible_published_utc"],
            "first_fixture_cutoff_utc":prow["first_fixture_cutoff_utc"],
            "selected_collection_n":len(cols),
            "selected_collection_ids":[c["id"] for c in cols],
            "query_n":len(queries),
            "query_error_n":sum(1 for q in queries if q["error"]),
            "queries":queries,
            "eligible_capture_n":len(captures),
            "eligible_captures":captures,
            "first_eligible_capture":captures[0] if captures else None,
        })

    positive_rounds=sorted(positive_rounds)
    all_query_n=sum(x["query_n"] for x in sample_reports)
    all_error_n=len(query_errors)
    if positive_rounds:
        classification=registry["decision_contract"]["positive_classification"]
        next_step=registry["reasonable_subroutes"]["if_positive"]
    elif all_query_n>0 and all_error_n==all_query_n:
        classification=registry["decision_contract"]["external_block_classification"]
        next_step=registry["reasonable_subroutes"]["if_external_errors"]
    else:
        classification=registry["decision_contract"]["zero_classification"]
        next_step=registry["reasonable_subroutes"]["if_zero_no_external_errors"]

    matrix={
        "schema_version":"football3-nova-n10-referee-sky-commoncrawl-feasibility-matrix-v1",
        "source":"Common Crawl CDXJ metadata",
        "sample_rounds":sample_rounds,
        "sample_n":len(sample_rounds),
        "positive_rounds":positive_rounds,
        "positive_round_n":len(positive_rounds),
        "query_n":all_query_n,
        "query_error_n":all_error_n,
        "reports":sample_reports,
        "collinfo_sha256":sha256_bytes(raw),
        "collinfo_collection_n":len(collinfo),
        "warc_content_fetched":False,
    }
    out.mkdir(parents=True,exist_ok=True)
    matrix_bytes=(json.dumps(matrix,indent=2,sort_keys=True,ensure_ascii=False)+"\n").encode("utf-8")
    (out/"sky_commoncrawl_feasibility_matrix.json").write_bytes(matrix_bytes)

    receipt={
        "schema_version":"football3-nova-n10-referee-sky-commoncrawl-feasibility-receipt-v1",
        "status":"N10_REFEREE_SKY_COMMONCRAWL_FEASIBILITY_COMPLETE",
        "classification":classification,
        "exact_base":registry["exact_base"],
        "registry_sha256":sha256_bytes(registry_path.read_bytes()),
        "parent_provenance":parent_prov,
        "collinfo_sha256":sha256_bytes(raw),
        "collinfo_collection_n":len(collinfo),
        "sample_n":len(sample_rounds),
        "sample_rounds":sample_rounds,
        "query_n":all_query_n,
        "query_error_n":all_error_n,
        "query_errors":query_errors,
        "positive_round_n":len(positive_rounds),
        "positive_rounds":positive_rounds,
        "matrix_sha256":sha256_bytes(matrix_bytes),
        "existing_pit_pass_round_n":registry["parent"]["existing_pit_pass_round_n"],
        "wayback_requery_performed":False,
        "commoncrawl_warc_content_fetched":False,
        "sky_article_body_reacquired":False,
        "referee_assignment_body_parsed":False,
        "match_result_payload_read":False,
        "standings_payload_read":False,
        "player_stats_payload_read":False,
        "result_labels_read":0,
        "score_values_read":0,
        "training_performed":False,
        "scoring_performed":False,
        "formal_v2_changed":False,
        "current_changed":False,
        "production_changed":False,
        "candidate_weight":0,
        "matrix_delta":0,
        "formal_available_at_proven_for_referee_assignments":False,
        "referee_oof_allowed":False,
        "next_step":next_step,
    }
    (out/"sky_commoncrawl_feasibility_receipt.json").write_text(
        json.dumps(receipt,indent=2,sort_keys=True,ensure_ascii=False)+"\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt,sort_keys=True,ensure_ascii=False))
    return receipt

def main() -> None:
    a=argparse.ArgumentParser()
    a.add_argument("--registry",type=Path,required=True)
    a.add_argument("--out",type=Path,required=True)
    args=a.parse_args()
    run(args.registry,args.out,os.environ.get("GITHUB_TOKEN",""))

if __name__=="__main__":
    main()
