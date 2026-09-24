#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from nova_n10_referee_sky_combined_freeze_v1 import (
    download_artifact_zip,
    read_unique_suffix,
    sha256_bytes,
)
from nova_n10_referee_sky_commoncrawl_feasibility_v1 import (
    eligible_rows,
    fetch,
    fetch_cdx_query,
    parse_cdxj,
    parse_z,
    prefix_query_url,
    select_collections,
)

class CommonCrawlUnresolvedMatrixError(RuntimeError):
    pass

def req(cond: bool, msg: str) -> None:
    if not cond:
        raise CommonCrawlUnresolvedMatrixError(msg)

def stable_bytes(obj: Any) -> bytes:
    return (json.dumps(obj,indent=2,sort_keys=True,ensure_ascii=False)+"\n").encode("utf-8")

def acquire_json_artifact(
    repository: str,
    artifact_id: int,
    artifact_sha: str,
    suffixes: list[tuple[str,str]],
    token: str,
) -> tuple[dict[str,Any],dict[str,dict[str,Any]]]:
    raw=download_artifact_zip(repository,artifact_id,token)
    zsha=sha256_bytes(raw)
    req(zsha==artifact_sha,f"ARTIFACT_SHA:{artifact_id}")
    out={}
    for suffix,expected_sha in suffixes:
        _,b,obj=read_unique_suffix(raw,suffix)
        actual=sha256_bytes(b)
        req(actual==expected_sha,f"FILE_SHA:{artifact_id}:{suffix}")
        out[suffix]={"sha256":actual,"json":obj}
    return {"artifact_id":artifact_id,"artifact_zip_sha256":zsha},out

def acquire_parents(registry: dict[str,Any], token: str) -> tuple[dict[str,Any],dict[str,Any],dict[str,Any],dict[str,Any]]:
    repo=registry["repository"]

    sp=registry["parent_sample"]
    sample_prov,sample_files=acquire_json_artifact(
        repo,int(sp["artifact_id"]),sp["artifact_zip_sha256"],
        [(sp["matrix_suffix"],sp["matrix_sha256"])],
        token,
    )
    sample_matrix=sample_files[sp["matrix_suffix"]]["json"]
    req(sample_matrix["sample_rounds"]==sp["tested_rounds"],"SAMPLE_ROUNDS")
    req(sample_matrix["positive_rounds"]==sp["positive_rounds"],"SAMPLE_POSITIVE")
    req(int(sample_matrix["query_error_n"])==int(sp["query_error_n"]),"SAMPLE_ERRORS")

    pp=registry["pit_parent"]
    pit_prov,pit_files=acquire_json_artifact(
        repo,int(pp["artifact_id"]),pp["artifact_zip_sha256"],
        [
            (pp["pit_binding_suffix"],pp["pit_binding_sha256"]),
            (pp["fixture_schedule_suffix"],pp["fixture_schedule_sha256"]),
        ],
        token,
    )
    pit=pit_files[pp["pit_binding_suffix"]]["json"]
    fixture=pit_files[pp["fixture_schedule_suffix"]]["json"]
    req(fixture.get("complete") is True and int(fixture.get("fixture_n",0))==380,"FIXTURE_PARENT")
    req(int(pit.get("pit_pass_round_n",0))==int(pp["existing_pit_pass_round_n"]),"PIT_PASS_PARENT")

    sky=registry["sky_parent"]
    sky_prov,sky_files=acquire_json_artifact(
        repo,int(sky["artifact_id"]),sky["artifact_zip_sha256"],
        [(sky["ledger_suffix"],sky["ledger_sha256"])],
        token,
    )
    sky_ledger=sky_files[sky["ledger_suffix"]]["json"]
    req(int(sky_ledger.get("round_n",0))==38 and len(sky_ledger.get("rows",[]))==38,"SKY_LEDGER_38")

    provenance={
        "sample_parent":sample_prov,
        "pit_parent":pit_prov,
        "sky_parent":sky_prov,
        "sample_matrix_sha256":sp["matrix_sha256"],
        "pit_binding_sha256":pp["pit_binding_sha256"],
        "fixture_schedule_sha256":pp["fixture_schedule_sha256"],
        "sky_ledger_sha256":sky["ledger_sha256"],
    }
    return sample_matrix,pit,sky_ledger,provenance

def round_decision(
    selected_collection_n: int,
    query_reports: list[dict[str,Any]],
    captures: list[dict[str,Any]],
) -> str:
    if selected_collection_n==0:
        return "NO_COLLECTION_OVERLAP"
    if any(q.get("error") for q in query_reports):
        return "EXTERNAL_ERROR"
    if captures:
        return "ELIGIBLE_CAPTURE"
    return "ZERO_CAPTURE"

def run(registry_path: Path, out: Path, token: str) -> dict[str,Any]:
    registry=json.loads(registry_path.read_text(encoding="utf-8"))
    req(registry["status"]=="DESIGN_LOCKED_ZERO_LABEL_METADATA_ONLY","STATUS")
    req(registry["exact_base"]=="f8ea3a61d357b4d66fc19f0761e8432073801aab","EXACT_BASE")
    targets=[int(x) for x in registry["target_rounds"]]
    req(targets==[11,12,14,15,16,17,19,20,21,22,23,24,25,27,28,32,34],"FROZEN_TARGETS")
    req(len(targets)==17,"TARGET_N")
    tested={int(x) for x in registry["already_tested_commoncrawl_rounds"]}
    req(tested=={8,9,38},"TESTED_SET")
    req(not (tested & set(targets)),"REQUERY_TESTED_ROUND")

    h=registry["hard_rules"]
    req(h["wayback_requery_allowed"] is False,"NO_WAYBACK")
    req(h["commoncrawl_requery_already_tested_rounds"] is False,"NO_CC_REQUERY")
    req(h["result_labels_read"] is False and h["score_values_read"] is False,"ZERO_LABEL")
    req(h["score_or_result_semantic_parse"] is False,"NO_RESULT_PARSE")
    req(h["referee_assignment_body_parsed"] is False,"NO_ASSIGNMENT")
    req(h["sky_article_body_reacquired"] is False,"NO_SKY_BODY")
    req(h["commoncrawl_warc_content_fetched"] is False,"NO_WARC")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False,"NO_MODEL")
    req(h["paid_or_secret_source_allowed"] is False,"NO_PAID_SECRET")
    req(h["candidate_weight"]==0 and h["matrix_delta"]==0,"ZERO_WEIGHT")

    sample_matrix,pit,sky_ledger,parent_prov=acquire_parents(registry,token)
    pit_rows={int(r["round"]):r for r in pit["rows"]}
    sky_rows={int(r["round"]):r for r in sky_ledger["rows"]}
    req(sorted(pit_rows)==list(range(1,39)),"PIT_ROWS_38")
    req(sorted(sky_rows)==list(range(1,39)),"SKY_ROWS_38")
    unresolved={int(x) for x in registry["all_unresolved_rounds"]}
    req(set(targets)|tested==unresolved,"UNRESOLVED_PARTITION")
    for rnd in targets:
        req(pit_rows[rnd]["binding_status"]=="FAIL",f"TARGET_NOT_FAIL:R{rnd}")

    src=registry["source"]
    collinfo_raw,collinfo_final,collinfo_headers=fetch(
        src["collinfo_url"],
        allowed_host=src["allowed_host"],
        timeout=int(src["request_timeout_seconds"]),
        limit=int(src["max_collinfo_bytes"]),
        user_agent=src["user_agent"],
    )
    collinfo=json.loads(collinfo_raw.decode("utf-8"))
    req(isinstance(collinfo,list),"COLLINFO_SHAPE")

    tolerance_seconds=int(registry["witness_contract"]["source_visible_timestamp_early_tolerance_seconds"])
    import datetime as dt
    tolerance=dt.timedelta(seconds=tolerance_seconds)

    reports=[]
    all_query_errors=[]
    eligible_rounds=[]
    zero_rounds=[]
    no_overlap_rounds=[]
    external_error_rounds=[]
    query_n=0
    last_query_time=None

    for rnd in targets:
        prow=pit_rows[rnd]
        srow=sky_rows[rnd]
        sky_url=srow.get("sky_url")
        req(isinstance(sky_url,str) and sky_url.startswith("https://sport.sky.it/"),f"SKY_URL:R{rnd}")
        sky_pub=parse_z(prow["sky_visible_published_utc"])
        cutoff=parse_z(prow["first_fixture_cutoff_utc"])
        req(sky_pub < cutoff,f"PUB_CUTOFF:R{rnd}")
        lower=sky_pub-tolerance
        cols=select_collections(collinfo,sky_pub,cutoff,src["allowed_host"])

        query_reports=[]
        captures=[]
        for collection in cols:
            if last_query_time is not None:
                elapsed=time.monotonic()-last_query_time
                wait=float(src["min_seconds_between_queries"])-elapsed
                if wait>0:
                    time.sleep(wait)
            q=prefix_query_url(str(collection["cdx-api"]),sky_url)
            query_n+=1
            try:
                raw,final,headers,http_status=fetch_cdx_query(
                    q,
                    allowed_host=src["allowed_host"],
                    timeout=int(src["request_timeout_seconds"]),
                    limit=int(src["max_query_bytes"]),
                    user_agent=src["user_agent"],
                )
                last_query_time=time.monotonic()
                parsed=[] if http_status==404 else parse_cdxj(raw)
                good=eligible_rows(parsed,sky_url,lower,cutoff)
                qr={
                    "round":rnd,
                    "collection_id":collection["id"],
                    "collection_from":collection["from"],
                    "collection_to":collection["to"],
                    "query_url":q,
                    "final_url":final,
                    "http_status":http_status,
                    "not_found_zero_evidence":http_status==404,
                    "response_sha256":sha256_bytes(raw),
                    "response_bytes":len(raw),
                    "row_n":len(parsed),
                    "eligible_n":len(good),
                    "error":None,
                }
                query_reports.append(qr)
                captures.extend([{**x,"collection_id":collection["id"]} for x in good])
            except Exception as exc:
                last_query_time=time.monotonic()
                err=f"{type(exc).__name__}:{exc}"[:800]
                qr={
                    "round":rnd,
                    "collection_id":collection["id"],
                    "collection_from":collection["from"],
                    "collection_to":collection["to"],
                    "query_url":q,
                    "row_n":0,
                    "eligible_n":0,
                    "error":err,
                }
                query_reports.append(qr)
                all_query_errors.append({
                    "round":rnd,
                    "collection_id":collection["id"],
                    "query_url":q,
                    "error":err,
                })

        captures=sorted(captures,key=lambda x:(x["timestamp"],x["collection_id"]))
        status=round_decision(len(cols),query_reports,captures)
        if status=="ELIGIBLE_CAPTURE":
            eligible_rounds.append(rnd)
        elif status=="ZERO_CAPTURE":
            zero_rounds.append(rnd)
        elif status=="NO_COLLECTION_OVERLAP":
            no_overlap_rounds.append(rnd)
        elif status=="EXTERNAL_ERROR":
            external_error_rounds.append(rnd)
        else:
            raise CommonCrawlUnresolvedMatrixError(f"UNKNOWN_STATUS:R{rnd}:{status}")

        reports.append({
            "round":rnd,
            "sky_url":sky_url,
            "sky_visible_published_utc":prow["sky_visible_published_utc"],
            "first_fixture_cutoff_utc":prow["first_fixture_cutoff_utc"],
            "selected_collection_n":len(cols),
            "selected_collection_ids":[c["id"] for c in cols],
            "query_n":len(query_reports),
            "query_error_n":sum(1 for q in query_reports if q.get("error")),
            "queries":query_reports,
            "eligible_capture_n":len(captures),
            "eligible_captures":captures,
            "first_eligible_capture":captures[0] if captures else None,
            "status":status,
        })

    eligible_rounds=sorted(eligible_rounds)
    zero_rounds=sorted(zero_rounds)
    no_overlap_rounds=sorted(no_overlap_rounds)
    external_error_rounds=sorted(external_error_rounds)
    adjudicated_rounds=sorted(eligible_rounds+zero_rounds+no_overlap_rounds)
    matrix_complete=(len(adjudicated_rounds)==17 and not external_error_rounds)

    if eligible_rounds:
        classification=registry["decision_contract"]["positive_classification"]
        if external_error_rounds:
            next_step="PRESERVE_POSITIVE_COMMONCRAWL_WITNESSES; PAUSE_EXTERNAL_ERROR_ROUNDS; DO_NOT_REQUERY_ADJUDICATED_ROUNDS"
        else:
            next_step=registry["close_contract"]["if_any_positive"]
    elif external_error_rounds:
        classification=registry["decision_contract"]["external_block_classification"]
        next_step=registry["close_contract"]["if_any_external_error"]
    else:
        classification=registry["decision_contract"]["zero_classification"]
        next_step=registry["close_contract"]["if_zero_no_errors"]

    commoncrawl_route_closed=(matrix_complete and not eligible_rounds)
    matrix={
        "schema_version":"football3-nova-n10-referee-sky-commoncrawl-unresolved-matrix-v1",
        "status":"N10_REFEREE_SKY_COMMONCRAWL_UNRESOLVED_MATRIX_COMPLETE",
        "classification":classification,
        "target_round_n":17,
        "target_rounds":targets,
        "already_tested_rounds":sorted(tested),
        "parent_sample_positive_rounds":sample_matrix["positive_rounds"],
        "parent_sample_query_error_n":sample_matrix["query_error_n"],
        "query_n":query_n,
        "query_error_n":len(all_query_errors),
        "eligible_capture_round_n":len(eligible_rounds),
        "eligible_capture_rounds":eligible_rounds,
        "zero_capture_rounds":zero_rounds,
        "no_collection_overlap_rounds":no_overlap_rounds,
        "external_error_rounds":external_error_rounds,
        "adjudicated_round_n":len(adjudicated_rounds),
        "matrix_complete":matrix_complete,
        "commoncrawl_route_closed":commoncrawl_route_closed,
        "collinfo_sha256":sha256_bytes(collinfo_raw),
        "collinfo_collection_n":len(collinfo),
        "reports":reports,
        "query_errors":all_query_errors,
        "warc_content_fetched":False,
    }
    out.mkdir(parents=True,exist_ok=True)
    matrix_bytes=stable_bytes(matrix)
    (out/"sky_commoncrawl_unresolved_matrix.json").write_bytes(matrix_bytes)

    receipt={
        "schema_version":"football3-nova-n10-referee-sky-commoncrawl-unresolved-matrix-receipt-v1",
        "status":"N10_REFEREE_SKY_COMMONCRAWL_UNRESOLVED_MATRIX_COMPLETE",
        "classification":classification,
        "exact_base":registry["exact_base"],
        "registry_sha256":sha256_bytes(registry_path.read_bytes()),
        "parent_provenance":parent_prov,
        "collinfo_sha256":sha256_bytes(collinfo_raw),
        "collinfo_collection_n":len(collinfo),
        "target_round_n":17,
        "target_rounds":targets,
        "already_tested_rounds":sorted(tested),
        "requery_already_tested_rounds":False,
        "query_n":query_n,
        "query_error_n":len(all_query_errors),
        "query_errors":all_query_errors,
        "eligible_capture_round_n":len(eligible_rounds),
        "eligible_capture_rounds":eligible_rounds,
        "zero_capture_rounds":zero_rounds,
        "no_collection_overlap_rounds":no_overlap_rounds,
        "external_error_rounds":external_error_rounds,
        "adjudicated_round_n":len(adjudicated_rounds),
        "matrix_complete":matrix_complete,
        "commoncrawl_route_closed":commoncrawl_route_closed,
        "matrix_sha256":sha256_bytes(matrix_bytes),
        "existing_pit_pass_round_n":registry["pit_parent"]["existing_pit_pass_round_n"],
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
    (out/"sky_commoncrawl_unresolved_matrix_receipt.json").write_bytes(stable_bytes(receipt))
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
