#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.parse
from pathlib import Path
from typing import Any

from nova_n10_referee_sky_combined_freeze_v1 import (
    download_artifact_zip,
    read_unique_suffix,
    sha256_bytes,
)
from nova_n10_referee_sky_arquivo_feasibility_v1 import (
    acquire_parents,
    eligible_items,
    host_ok,
    parse_response,
    query_bounds,
    request_json,
)

class SkyArquivoMatrixError(RuntimeError):
    pass

def req(cond: bool, msg: str) -> None:
    if not cond:
        raise SkyArquivoMatrixError(msg)

def as_int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None

def build_page_query(
    endpoint: str,
    sky_url: str,
    pit_row: dict[str,Any],
    tolerance_seconds: int,
    offset: int,
    max_items: int,
) -> str:
    _,_,frm,to=query_bounds(pit_row,tolerance_seconds)
    params=[
        ("versionHistory",sky_url),
        ("from",frm),
        ("to",to),
        ("offset",str(offset)),
        ("maxItems",str(max_items)),
    ]
    return endpoint+"?"+urllib.parse.urlencode(params)

def page_complete(
    *,
    estimated: int | None,
    cumulative_item_n: int,
    page_item_n: int,
    next_page: Any,
) -> tuple[bool,bool,str]:
    # complete, incomplete, reason
    if estimated==0 and cumulative_item_n==0:
        return True,False,"COMPLETE_ZERO_ESTIMATE"
    if estimated is not None and cumulative_item_n>=estimated:
        return True,False,"COMPLETE_ESTIMATE_SATISFIED"
    if page_item_n==0:
        if estimated is not None and estimated>cumulative_item_n:
            return False,True,"INCOMPLETE_EMPTY_BEFORE_ESTIMATE"
        return True,False,"COMPLETE_EMPTY_PAGE"
    if not next_page:
        if estimated is not None and estimated>cumulative_item_n:
            return False,True,"INCOMPLETE_NO_NEXT_BEFORE_ESTIMATE"
        return True,False,"COMPLETE_NO_NEXT_PAGE"
    return False,False,"CONTINUE"

def acquire_sample_parent(registry: dict[str,Any], token: str) -> tuple[dict[str,Any],dict[str,Any],dict[str,str]]:
    p=registry["sample_parent"]
    raw=download_artifact_zip(registry["repository"],int(p["artifact_id"]),token)
    req(sha256_bytes(raw)==p["artifact_zip_sha256"],"SAMPLE_ARTIFACT_SHA")
    _,receipt_raw,receipt=read_unique_suffix(raw,p["receipt_suffix"])
    _,matrix_raw,matrix=read_unique_suffix(raw,p["matrix_suffix"])
    req(sha256_bytes(receipt_raw)==p["receipt_sha256"],"SAMPLE_RECEIPT_SHA")
    req(sha256_bytes(matrix_raw)==p["matrix_sha256"],"SAMPLE_MATRIX_SHA")
    req(receipt["classification"]==p["expected_classification"],"SAMPLE_CLASSIFICATION")
    req(receipt["incomplete_rounds"]==p["expected_incomplete_rounds"],"SAMPLE_INCOMPLETE")
    req(matrix["sample_rounds"]==p["frozen_zero_rounds"],"SAMPLE_ROUNDS")
    req(matrix["positive_round_n"]==0,"SAMPLE_POSITIVE")
    req(matrix["incomplete_rounds"]==[],"SAMPLE_MATRIX_INCOMPLETE")
    by_round={int(x["round"]):x for x in matrix["reports"]}
    req(sorted(by_round)==p["frozen_zero_rounds"],"SAMPLE_REPORT_ROUNDS")
    for rnd in p["frozen_zero_rounds"]:
        r=by_round[rnd]
        req(r["status"]=="ZERO_CAPTURE",f"SAMPLE_NOT_ZERO:R{rnd}")
        req(int(r["response_item_n"])==0,f"SAMPLE_ITEM_N:R{rnd}")
        req(int(r["eligible_capture_n"])==0,f"SAMPLE_ELIGIBLE_N:R{rnd}")
    return receipt,matrix,{
        "artifact_zip_sha256":sha256_bytes(raw),
        "receipt_sha256":sha256_bytes(receipt_raw),
        "matrix_sha256":sha256_bytes(matrix_raw),
    }

def query_round(
    rnd: int,
    pit_row: dict[str,Any],
    sky_url: str,
    registry: dict[str,Any],
    last_request_at: float | None,
) -> tuple[dict[str,Any],float | None]:
    src=registry["source"]
    sc=registry["search_contract"]
    tolerance=int(sc["source_visible_timestamp_early_tolerance_seconds"])
    lower,upper,_,_=query_bounds(pit_row,tolerance)
    max_items=int(src["max_items_per_page"])
    offsets=[int(x) for x in sc["pagination_offsets"]]
    req(len(offsets)==int(src["max_pages"]),"OFFSET_PAGE_CAP")
    all_items=[]
    page_reports=[]
    estimated=None
    incomplete=False
    external_error=None

    for i,offset in enumerate(offsets):
        if last_request_at is not None:
            sleep_for=float(src["min_seconds_between_requests"])-(time.monotonic()-last_request_at)
            if sleep_for>0:
                time.sleep(sleep_for)
        q=build_page_query(src["endpoint"],sky_url,pit_row,tolerance,offset,max_items)
        try:
            status,raw,final,headers=request_json(
                q,int(src["request_timeout_seconds"]),int(src["max_response_bytes"]),src["user_agent"]
            )
            last_request_at=time.monotonic()
            req(host_ok(final,src["allowed_host"]),"REDIRECT_OUTSIDE_ARQUIVO")
            page={
                "offset":offset,
                "query_url":q,
                "final_url":final,
                "http_status":status,
                "response_bytes":len(raw),
                "response_sha256":sha256_bytes(raw),
                "content_type":headers.get("content-type"),
                "response_item_n":0,
                "estimated_nr_results":None,
                "next_page_present":False,
                "status":"UNSET",
            }
            if status!=200:
                page["status"]="EXTERNAL_ERROR"
                page["service_error_preview"]=raw.decode("utf-8","replace")[:300]
                page_reports.append(page)
                external_error=f"HTTP_{status}"
                incomplete=True
                break

            items,meta=parse_response(raw)
            page["response_item_n"]=len(items)
            page["estimated_nr_results"]=meta.get("estimated_nr_results")
            page["next_page_present"]=bool(meta.get("next_page"))
            all_items.extend(items)
            if estimated is None:
                estimated=as_int(meta.get("estimated_nr_results"))
            complete,bad,reason=page_complete(
                estimated=estimated,
                cumulative_item_n=len(all_items),
                page_item_n=len(items),
                next_page=meta.get("next_page"),
            )
            page["status"]=reason
            page_reports.append(page)
            if bad:
                incomplete=True
                break
            if complete:
                break
            if i==len(offsets)-1:
                incomplete=True
                page_reports[-1]["status"]="INCOMPLETE_PAGE_CAP"
        except Exception as exc:
            last_request_at=time.monotonic()
            external_error=f"{type(exc).__name__}:{exc}"[:500]
            page_reports.append({
                "offset":offset,
                "query_url":q,
                "status":"EXTERNAL_ERROR",
                "error":external_error,
            })
            incomplete=True
            break

    eligible=eligible_items(all_items,sky_url,lower,upper) if not external_error else []
    if eligible:
        status="ELIGIBLE_CAPTURE"
    elif incomplete:
        status="INCOMPLETE"
    else:
        status="ZERO_CAPTURE"

    report={
        "round":rnd,
        "sky_url":sky_url,
        "sky_visible_published_utc":pit_row["sky_visible_published_utc"],
        "first_fixture_cutoff_utc":pit_row["first_fixture_cutoff_utc"],
        "page_n":len(page_reports),
        "pages":page_reports,
        "estimated_nr_results":estimated,
        "cumulative_response_item_n":len(all_items),
        "eligible_capture_n":len(eligible),
        "eligible_captures":eligible,
        "first_eligible_capture":eligible[0] if eligible else None,
        "external_error":external_error,
        "incomplete":incomplete,
        "status":status,
    }
    return report,last_request_at

def classify(
    *,
    positive_rounds: list[int],
    incomplete_rounds: list[int],
    registry: dict[str,Any],
) -> tuple[str,bool,str]:
    d=registry["decision_contract"]
    if positive_rounds:
        return d["positive_classification"],False,d["next_if_positive"]
    if incomplete_rounds:
        return d["incomplete_classification"],False,d["next_if_incomplete"]
    return d["zero_closed_classification"],True,d["next_if_closed_zero"]

def run(registry_path: Path, out: Path, token: str) -> dict[str,Any]:
    registry=json.loads(registry_path.read_text(encoding="utf-8"))
    req(registry["status"]=="DESIGN_LOCKED_ZERO_LABEL_METADATA_ONLY","STATUS")
    req(registry["exact_base"]=="f2575df36a7b2c4b2a6d5e94f480955a65cd67df","EXACT_BASE")
    h=registry["hard_rules"]
    req(h["frozen_zero_rounds_requeried"] is False,"NO_ZERO_REQUERY")
    req(h["wayback_requery_allowed"] is False and h["commoncrawl_requery_allowed"] is False and h["urlscan_requery_allowed"] is False,"NO_OLD_SOURCE_REQUERY")
    req(h["result_labels_read"] is False and h["score_values_read"] is False,"ZERO_LABEL")
    req(h["score_or_result_semantic_parse"] is False,"NO_RESULT_PARSE")
    req(h["referee_assignment_body_parsed"] is False and h["sky_article_body_reacquired"] is False,"NO_BODY")
    req(h["arquivo_archived_page_content_fetched"] is False and h["arquivo_extracted_text_fetched"] is False and h["arquivo_screenshot_fetched"] is False,"NO_ARCHIVE_CONTENT")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False,"NO_MODEL")
    req(h["api_key_or_secret_allowed"] is False and h["paid_or_secret_source_allowed"] is False,"NO_SECRET")
    req(h["candidate_weight"]==0 and h["matrix_delta"]==0,"ZERO_WEIGHT")

    sample_receipt,sample_matrix,sample_prov=acquire_sample_parent(registry,token)
    pit,sky,parent_prov=acquire_parents(registry,token)
    pit_rows={int(r["round"]):r for r in pit["rows"]}
    sky_rows={int(r["round"]):r for r in sky["rows"]}

    uc=registry["unresolved_contract"]
    query_rounds=[int(x) for x in uc["query_rounds"]]
    frozen_zero=[int(x) for x in uc["frozen_zero_rounds"]]
    all_unresolved=[int(x) for x in uc["all_unresolved_rounds"]]
    req(len(query_rounds)==17 and len(frozen_zero)==3 and len(all_unresolved)==20,"ROUND_COUNTS")
    req(sorted(set(query_rounds+frozen_zero))==sorted(all_unresolved),"ROUND_PARTITION")
    req(set(query_rounds).isdisjoint(frozen_zero),"NO_REQUERY_PARTITION")

    new_reports=[]
    last_request_at=None
    for rnd in query_rounds:
        req(rnd in pit_rows and rnd in sky_rows,f"PARENT_ROW:R{rnd}")
        req(pit_rows[rnd]["binding_status"]=="FAIL",f"ROUND_ALREADY_PASS:R{rnd}")
        sky_url=sky_rows[rnd].get("sky_url")
        req(isinstance(sky_url,str) and sky_url.startswith("https://sport.sky.it/"),f"SKY_URL:R{rnd}")
        report,last_request_at=query_round(rnd,pit_rows[rnd],sky_url,registry,last_request_at)
        new_reports.append(report)

    sample_reports={int(x["round"]):x for x in sample_matrix["reports"]}
    combined_reports=[]
    for rnd in all_unresolved:
        if rnd in sample_reports:
            s=sample_reports[rnd]
            combined_reports.append({
                "round":rnd,
                "source_layer":"FROZEN_SAMPLE_PR482",
                "status":s["status"],
                "sky_url":s["sky_url"],
                "sky_visible_published_utc":s["sky_visible_published_utc"],
                "first_fixture_cutoff_utc":s["first_fixture_cutoff_utc"],
                "eligible_capture_n":s["eligible_capture_n"],
                "eligible_captures":s["eligible_captures"],
                "incomplete":False,
                "external_error":None,
                "response_item_n":s["response_item_n"],
                "response_sha256":s["response_sha256"],
            })
        else:
            r=next(x for x in new_reports if x["round"]==rnd)
            combined_reports.append({**r,"source_layer":"NEW_QUERY_PR"})
    req([x["round"] for x in combined_reports]==all_unresolved,"COMBINED_ORDER")

    positive=sorted(x["round"] for x in combined_reports if int(x.get("eligible_capture_n",0))>0)
    incomplete=sorted(x["round"] for x in combined_reports if bool(x.get("incomplete")) or x.get("external_error"))
    classification,source_closed,next_step=classify(
        positive_rounds=positive,
        incomplete_rounds=incomplete,
        registry=registry,
    )

    matrix={
        "schema_version":"football3-nova-n10-referee-sky-arquivo-unresolved-matrix-v1",
        "source_family":"ARQUIVO_PT_URL_HISTORY_SKY",
        "all_unresolved_rounds":all_unresolved,
        "frozen_zero_rounds":frozen_zero,
        "new_query_rounds":query_rounds,
        "total_round_n":20,
        "positive_rounds":positive,
        "positive_round_n":len(positive),
        "incomplete_rounds":incomplete,
        "incomplete_round_n":len(incomplete),
        "complete_zero_rounds":sorted(x["round"] for x in combined_reports if x["status"]=="ZERO_CAPTURE"),
        "source_family_closed":source_closed,
        "reports":combined_reports,
        "archived_page_content_fetched":False,
        "extracted_text_fetched":False,
        "screenshot_fetched":False,
    }
    out.mkdir(parents=True,exist_ok=True)
    matrix_bytes=(json.dumps(matrix,indent=2,sort_keys=True,ensure_ascii=False)+"\n").encode("utf-8")
    (out/"sky_arquivo_unresolved_matrix.json").write_bytes(matrix_bytes)

    receipt={
        "schema_version":"football3-nova-n10-referee-sky-arquivo-unresolved-matrix-receipt-v1",
        "status":"N10_REFEREE_SKY_ARQUIVO_UNRESOLVED_MATRIX_COMPLETE",
        "classification":classification,
        "exact_base":registry["exact_base"],
        "registry_sha256":sha256_bytes(registry_path.read_bytes()),
        "sample_parent_provenance":sample_prov,
        "parent_provenance":parent_prov,
        "frozen_zero_rounds":frozen_zero,
        "frozen_zero_round_n":3,
        "new_query_rounds":query_rounds,
        "new_query_round_n":17,
        "total_unresolved_round_n":20,
        "positive_rounds":positive,
        "positive_round_n":len(positive),
        "incomplete_rounds":incomplete,
        "incomplete_round_n":len(incomplete),
        "complete_zero_round_n":len(matrix["complete_zero_rounds"]),
        "complete_zero_rounds":matrix["complete_zero_rounds"],
        "source_family_closed":source_closed,
        "matrix_sha256":sha256_bytes(matrix_bytes),
        "frozen_zero_rounds_requeried":False,
        "wayback_requery_performed":False,
        "commoncrawl_requery_performed":False,
        "urlscan_requery_performed":False,
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
    (out/"sky_arquivo_unresolved_matrix_receipt.json").write_text(
        json.dumps(receipt,indent=2,sort_keys=True,ensure_ascii=False)+"\n",encoding="utf-8"
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
