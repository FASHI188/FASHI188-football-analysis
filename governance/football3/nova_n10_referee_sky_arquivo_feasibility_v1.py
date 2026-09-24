#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import ssl
import urllib.error
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

class SkyArquivoError(RuntimeError):
    pass

def req(cond: bool, msg: str) -> None:
    if not cond:
        raise SkyArquivoError(msg)

def parse_z(v: str) -> dt.datetime:
    x=dt.datetime.fromisoformat(v.replace("Z","+00:00"))
    if x.tzinfo is None:
        x=x.replace(tzinfo=UTC)
    return x.astimezone(UTC)

def parse_ts14(v: Any) -> dt.datetime | None:
    s=str(v or "")
    if not re.fullmatch(r"\d{14}",s):
        return None
    try:
        return dt.datetime.strptime(s,"%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except Exception:
        return None

def host_ok(url: str, expected: str) -> bool:
    h=(urllib.parse.urlparse(url).hostname or "").lower()
    e=expected.lower()
    return h==e or h.endswith("."+e)

def normalize_identity(url: str | None) -> tuple[str,str] | None:
    if not isinstance(url,str) or not url.strip():
        return None
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

def request_json(url: str, timeout: int, limit: int, user_agent: str) -> tuple[int,bytes,str,dict[str,str]]:
    request=urllib.request.Request(
        url,
        headers={"User-Agent":user_agent,"Accept":"application/json"},
    )
    try:
        with urllib.request.urlopen(request,timeout=timeout,context=ssl.create_default_context()) as response:
            raw=response.read(limit+1)
            req(len(raw)<=limit,"RESPONSE_TOO_LARGE")
            return int(getattr(response,"status",200)),raw,response.geturl(),{k.lower():v for k,v in response.headers.items()}
    except urllib.error.HTTPError as exc:
        raw=exc.read(limit+1)
        req(len(raw)<=limit,"ERROR_RESPONSE_TOO_LARGE")
        return int(exc.code),raw,exc.geturl(),{k.lower():v for k,v in exc.headers.items()}

def query_bounds(pit_row: dict[str,Any], tolerance_seconds: int) -> tuple[dt.datetime,dt.datetime,str,str]:
    pub=parse_z(pit_row["sky_visible_published_utc"])
    cutoff=parse_z(pit_row["first_fixture_cutoff_utc"])
    req(pub < cutoff,"PUB_NOT_PRE_CUTOFF")
    lower=pub-dt.timedelta(seconds=tolerance_seconds)
    upper=cutoff
    return lower,upper,lower.strftime("%Y%m%d%H%M%S"),(upper-dt.timedelta(seconds=1)).strftime("%Y%m%d%H%M%S")

def build_query(endpoint: str, sky_url: str, pit_row: dict[str,Any], tolerance_seconds: int, max_items: int) -> str:
    _,_,frm,to=query_bounds(pit_row,tolerance_seconds)
    params=[
        ("versionHistory",sky_url),
        ("from",frm),
        ("to",to),
        ("offset","0"),
        ("maxItems",str(max_items)),
    ]
    return endpoint+"?"+urllib.parse.urlencode(params)

def parse_response(raw: bytes) -> tuple[list[dict[str,Any]],dict[str,Any]]:
    obj=json.loads(raw.decode("utf-8"))
    req(isinstance(obj,dict),"RESPONSE_SHAPE")
    items=obj.get("response_items")
    req(isinstance(items,list),"RESPONSE_ITEMS_SHAPE")
    meta={
        "estimated_nr_results":obj.get("estimated_nr_results"),
        "next_page":obj.get("next_page"),
        "previous_page":obj.get("previous_page"),
    }
    return [x for x in items if isinstance(x,dict)],meta

def pagination_incomplete(meta: dict[str,Any], item_n: int) -> bool:
    estimated=meta.get("estimated_nr_results")
    try:
        estimated_n=int(estimated)
    except (TypeError,ValueError):
        estimated_n=None
    # Arquivo may emit a formal next_page even when the search is definitively empty.
    # Empty estimate + empty item list is complete zero evidence, not incomplete pagination.
    if estimated_n==0 and item_n==0:
        return False
    if estimated_n is not None and estimated_n <= item_n:
        return False
    return bool(meta.get("next_page"))

def safe_metadata(row: dict[str,Any]) -> dict[str,Any]:
    return {
        "title":row.get("title") if isinstance(row.get("title"),str) else None,
        "originalURL":row.get("originalURL") if isinstance(row.get("originalURL"),str) else None,
        "tstamp":str(row.get("tstamp")) if row.get("tstamp") is not None else None,
        "mimeType":row.get("mimeType") if isinstance(row.get("mimeType"),str) else None,
        "statusCode":row.get("statusCode"),
        "digest":row.get("digest") if isinstance(row.get("digest"),str) else None,
        "collection":row.get("collection") if isinstance(row.get("collection"),str) else None,
    }

def eligible_items(
    items: list[dict[str,Any]],
    sky_url: str,
    lower: dt.datetime,
    upper: dt.datetime,
) -> list[dict[str,Any]]:
    target=normalize_identity(sky_url)
    req(target is not None,"TARGET_URL")
    out=[]
    for raw in items:
        meta=safe_metadata(raw)
        if normalize_identity(meta["originalURL"])!=target:
            continue
        when=parse_ts14(meta["tstamp"])
        if when is None or not (lower <= when < upper):
            continue
        status=str(meta["statusCode"]) if meta["statusCode"] is not None else ""
        if status!="200":
            continue
        mime=(meta["mimeType"] or "").casefold()
        if "html" not in mime:
            continue
        meta["capture_utc"]=when.isoformat().replace("+00:00","Z")
        out.append(meta)
    return sorted(out,key=lambda x:(x["tstamp"] or "",x["digest"] or ""))

def acquire_parents(registry: dict[str,Any], token: str) -> tuple[dict[str,Any],dict[str,Any],dict[str,Any]]:
    repo=registry["repository"]
    pp=registry["pit_parent"]
    raw=download_artifact_zip(repo,int(pp["artifact_id"]),token)
    req(sha256_bytes(raw)==pp["artifact_zip_sha256"],"PIT_ARTIFACT_SHA")
    _,pit_raw,pit=read_unique_suffix(raw,pp["pit_binding_suffix"])
    _,fixture_raw,fixture=read_unique_suffix(raw,pp["fixture_schedule_suffix"])
    req(sha256_bytes(pit_raw)==pp["pit_binding_sha256"],"PIT_LEDGER_SHA")
    req(sha256_bytes(fixture_raw)==pp["fixture_schedule_sha256"],"FIXTURE_SHA")
    req(fixture.get("complete") is True and int(fixture.get("fixture_n",0))==380,"FIXTURE_PARENT")

    sp=registry["sky_parent"]
    sraw=download_artifact_zip(repo,int(sp["artifact_id"]),token)
    req(sha256_bytes(sraw)==sp["artifact_zip_sha256"],"SKY_ARTIFACT_SHA")
    _,sky_raw,sky=read_unique_suffix(sraw,sp["ledger_suffix"])
    req(sha256_bytes(sky_raw)==sp["ledger_sha256"],"SKY_LEDGER_SHA")
    req(int(sky.get("round_n",0))==38 and len(sky.get("rows",[]))==38,"SKY_LEDGER_38")
    return pit,sky,{
        "pit_artifact_zip_sha256":sha256_bytes(raw),
        "pit_binding_sha256":sha256_bytes(pit_raw),
        "fixture_schedule_sha256":sha256_bytes(fixture_raw),
        "sky_artifact_zip_sha256":sha256_bytes(sraw),
        "sky_ledger_sha256":sha256_bytes(sky_raw),
    }

def decide(positive: list[int], incomplete: list[int], registry: dict[str,Any]) -> tuple[str,str]:
    if positive:
        return registry["decision_contract"]["positive_classification"],registry["reasonable_subroutes"]["if_positive"]
    if incomplete:
        return registry["decision_contract"]["external_block_classification"],registry["reasonable_subroutes"]["if_external_error"]
    return registry["decision_contract"]["zero_classification"],registry["reasonable_subroutes"]["if_zero_no_external_errors"]

def run(registry_path: Path, out: Path, token: str) -> dict[str,Any]:
    registry=json.loads(registry_path.read_text(encoding="utf-8"))
    req(registry["status"]=="DESIGN_LOCKED_ZERO_LABEL_METADATA_ONLY","STATUS")
    req(registry["exact_base"]=="81a959a28657868bbf4aca00cfeffd8b6039fdce","EXACT_BASE")
    h=registry["hard_rules"]
    req(h["wayback_requery_allowed"] is False and h["commoncrawl_requery_allowed"] is False and h["urlscan_requery_allowed"] is False,"NO_OLD_SOURCE_REQUERY")
    req(h["result_labels_read"] is False and h["score_values_read"] is False,"ZERO_LABEL")
    req(h["score_or_result_semantic_parse"] is False,"NO_RESULT_PARSE")
    req(h["referee_assignment_body_parsed"] is False and h["sky_article_body_reacquired"] is False,"NO_BODY")
    req(h["arquivo_archived_page_content_fetched"] is False and h["arquivo_extracted_text_fetched"] is False and h["arquivo_screenshot_fetched"] is False,"NO_ARCHIVE_CONTENT")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False,"NO_MODEL")
    req(h["api_key_or_secret_allowed"] is False and h["paid_or_secret_source_allowed"] is False,"NO_SECRET")
    req(h["candidate_weight"]==0 and h["matrix_delta"]==0,"ZERO_WEIGHT")

    pit,sky,parent_prov=acquire_parents(registry,token)
    pit_rows={int(r["round"]):r for r in pit["rows"]}
    sky_rows={int(r["round"]):r for r in sky["rows"]}
    sample_rounds=[int(x["round"]) for x in registry["samples"]]
    req(sample_rounds==[12,24,34],"FROZEN_SAMPLES")
    for rnd in sample_rounds:
        req(rnd in pit_rows and rnd in sky_rows,f"PARENT_ROW:R{rnd}")
        req(pit_rows[rnd]["binding_status"]=="FAIL",f"SAMPLE_NOT_UNRESOLVED:R{rnd}")

    source=registry["source"]
    tolerance=int(registry["search_contract"]["source_visible_timestamp_early_tolerance_seconds"])
    reports=[]
    errors=[]
    positive=[]
    incomplete=[]

    for rnd in sample_rounds:
        prow=pit_rows[rnd]
        srow=sky_rows[rnd]
        sky_url=srow.get("sky_url")
        req(isinstance(sky_url,str) and sky_url.startswith("https://sport.sky.it/"),f"SKY_URL:R{rnd}")
        lower,upper,_,_=query_bounds(prow,tolerance)
        q=build_query(source["endpoint"],sky_url,prow,tolerance,int(source["max_items"]))
        report={
            "round":rnd,
            "sky_url":sky_url,
            "sky_visible_published_utc":prow["sky_visible_published_utc"],
            "first_fixture_cutoff_utc":prow["first_fixture_cutoff_utc"],
            "query_url":q,
            "http_status":None,
            "response_item_n":0,
            "eligible_capture_n":0,
            "eligible_captures":[],
            "status":"UNSET",
        }
        try:
            status,raw,final,headers=request_json(
                q,int(source["request_timeout_seconds"]),int(source["max_response_bytes"]),source["user_agent"]
            )
            req(host_ok(final,source["allowed_host"]),"REDIRECT_OUTSIDE_ARQUIVO")
            report.update({
                "final_url":final,
                "http_status":status,
                "response_bytes":len(raw),
                "response_sha256":sha256_bytes(raw),
                "content_type":headers.get("content-type"),
            })
            if status!=200:
                report["status"]="EXTERNAL_ERROR"
                report["service_error_preview"]=raw.decode("utf-8","replace")[:500]
                errors.append({"round":rnd,"http_status":status,"query_url":q})
                incomplete.append(rnd)
            else:
                items,meta=parse_response(raw)
                report["response_item_n"]=len(items)
                report["search_meta"]=meta
                if pagination_incomplete(meta,len(items)):
                    report["status"]="INCOMPLETE_PAGINATION"
                    incomplete.append(rnd)
                else:
                    eligible=eligible_items(items,sky_url,lower,upper)
                    report["eligible_capture_n"]=len(eligible)
                    report["eligible_captures"]=eligible
                    if eligible:
                        report["status"]="ELIGIBLE_CAPTURE"
                        positive.append(rnd)
                    else:
                        report["status"]="ZERO_CAPTURE"
        except Exception as exc:
            report["status"]="EXTERNAL_ERROR"
            report["error"]=f"{type(exc).__name__}:{exc}"[:800]
            errors.append({"round":rnd,"query_url":q,"error":report["error"]})
            incomplete.append(rnd)
        reports.append(report)

    positive=sorted(set(positive))
    incomplete=sorted(set(incomplete))
    classification,next_step=decide(positive,incomplete,registry)
    matrix={
        "schema_version":"football3-nova-n10-referee-sky-arquivo-feasibility-matrix-v1",
        "sample_rounds":sample_rounds,
        "sample_n":3,
        "positive_rounds":positive,
        "positive_round_n":len(positive),
        "incomplete_rounds":incomplete,
        "reports":reports,
        "archived_page_content_fetched":False,
        "extracted_text_fetched":False,
        "screenshot_fetched":False,
    }
    out.mkdir(parents=True,exist_ok=True)
    matrix_bytes=(json.dumps(matrix,indent=2,sort_keys=True,ensure_ascii=False)+"\n").encode("utf-8")
    (out/"sky_arquivo_feasibility_matrix.json").write_bytes(matrix_bytes)

    receipt={
        "schema_version":"football3-nova-n10-referee-sky-arquivo-feasibility-receipt-v1",
        "status":"N10_REFEREE_SKY_ARQUIVO_FEASIBILITY_COMPLETE",
        "classification":classification,
        "exact_base":registry["exact_base"],
        "registry_sha256":sha256_bytes(registry_path.read_bytes()),
        "parent_provenance":parent_prov,
        "sample_n":3,
        "sample_rounds":sample_rounds,
        "report_n":len(reports),
        "external_error_n":len(errors),
        "external_errors":errors,
        "positive_sample_n":len(positive),
        "positive_sample_rounds":positive,
        "incomplete_rounds":incomplete,
        "matrix_sha256":sha256_bytes(matrix_bytes),
        "api_key_used":False,
        "archived_page_content_fetched":False,
        "extracted_text_fetched":False,
        "screenshot_fetched":False,
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
    (out/"sky_arquivo_feasibility_receipt.json").write_text(
        json.dumps(receipt,indent=2,sort_keys=True,ensure_ascii=False)+"\n",encoding="utf-8"
    )
    print(json.dumps(receipt,sort_keys=True,ensure_ascii=False))
    return receipt

def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--registry",type=Path,required=True)
    parser.add_argument("--out",type=Path,required=True)
    args=parser.parse_args()
    run(args.registry,args.out,os.environ.get("GITHUB_TOKEN",""))

if __name__=="__main__":
    main()
