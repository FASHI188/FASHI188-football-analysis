#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from nova_n10_referee_aia_gdelt_gkg_daily_v1 import build_daily_url, fetch
from nova_n10_referee_sky_combined_freeze_v1 import (
    download_artifact_zip,
    read_unique_suffix,
    sha256_bytes,
)
from nova_n10_referee_sky_arquivo_feasibility_v1 import acquire_parents
from nova_n10_referee_sky_gdelt_daily_feasibility_v1 import frozen_dates, scan_zip_sky

class SkyGDELTResidualError(RuntimeError):
    pass

def req(cond: bool, msg: str) -> None:
    if not cond:
        raise SkyGDELTResidualError(msg)

def verify_publication_parent(registry: dict[str,Any], token: str) -> dict[str,Any]:
    p=registry["publication_day_parent"]
    raw=download_artifact_zip(registry["repository"],int(p["artifact_id"]),token)
    req(sha256_bytes(raw)==p["artifact_zip_sha256"],"PUBLICATION_PARENT_ARTIFACT_SHA")
    _,receipt_raw,receipt=read_unique_suffix(raw,p["receipt_suffix"])
    _,matrix_raw,matrix=read_unique_suffix(raw,p["matrix_suffix"])
    req(sha256_bytes(matrix_raw)==p["matrix_sha256"],"PUBLICATION_PARENT_MATRIX_SHA")
    req(receipt["status"]=="N10_REFEREE_SKY_GDELT_PUBLICATIONDAY17_COMPLETE","PUBLICATION_PARENT_STATUS")
    req(receipt["classification"]=="STOP_DATA_COVERAGE","PUBLICATION_PARENT_CLASSIFICATION")
    req(receipt["target_rounds"]==p["target_rounds"],"PUBLICATION_PARENT_TARGETS")
    req(int(receipt["successful_round_n"])==int(p["successful_round_n"]),"PUBLICATION_PARENT_SUCCESS_N")
    req(int(receipt["error_round_n"])==int(p["error_round_n"])==0,"PUBLICATION_PARENT_ERROR_N")
    req(int(receipt["positive_round_n"])==int(p["positive_round_n"])==0,"PUBLICATION_PARENT_POSITIVE_N")
    req(int(receipt["zero_round_n"])==int(p["zero_round_n"])==17,"PUBLICATION_PARENT_ZERO_N")
    req(receipt["residual_pit_days_scanned"] is False,"PUBLICATION_PARENT_RESIDUAL_ALREADY_SCANNED")
    req(matrix["target_rounds"]==p["target_rounds"],"PUBLICATION_PARENT_MATRIX_TARGETS")
    return {
        "artifact_zip_sha256":sha256_bytes(raw),
        "receipt_sha256":sha256_bytes(receipt_raw),
        "matrix_sha256":sha256_bytes(matrix_raw),
    }

def residual_dates_for_round(
    pub_utc: str,
    cutoff_utc: str,
    max_full_days: int,
    max_residual_days: int,
) -> tuple[str,list[str]]:
    full=frozen_dates(pub_utc,cutoff_utc,max_full_days)
    req(bool(full),"EMPTY_FULL_WINDOW")
    publication_day=full[0]
    residual=full[1:]
    req(len(residual)<=max_residual_days,f"RESIDUAL_DAY_N:{len(residual)}")
    req(publication_day not in residual,"PUBLICATION_DAY_NOT_EXCLUDED")
    return publication_day,residual

def classify(
    positive_rounds: list[int],
    error_days: list[str],
    registry: dict[str,Any],
) -> tuple[str,str,bool]:
    d=registry["decision_contract"]
    if positive_rounds:
        return d["positive_classification"],registry["reasonable_subroutes"]["if_positive"],False
    if error_days:
        return d["external_block_classification"],registry["reasonable_subroutes"]["if_external_error"],False
    return d["zero_classification"],registry["reasonable_subroutes"]["if_complete_zero"],True

def run(registry_path: Path, out: Path, token: str) -> dict[str,Any]:
    p=json.loads(registry_path.read_text(encoding="utf-8"))
    req(p["status"]=="DESIGN_LOCKED_ZERO_LABEL_METADATA_ONLY","STATUS")
    req(p["exact_base"]=="c9623a41399965a2699c141d9f20c34d52afab62","EXACT_BASE")

    h=p["hard_rules"]
    req(h["prior_source_requery_allowed"] is False,"NO_PRIOR_SOURCE_REQUERY")
    req(h["publication_day_requery_allowed"] is False,"NO_PUBLICATION_DAY_REQUERY")
    req(h["prior_sample_round_requery_allowed"] is False,"NO_PRIOR_SAMPLE_REQUERY")
    req(h["result_labels_read"] is False and h["score_values_read"] is False,"ZERO_LABEL")
    req(h["score_or_result_semantic_parse"] is False,"NO_RESULT_PARSE")
    req(h["article_body_read"] is False and h["referee_assignment_body_parsed"] is False,"NO_BODY")
    req(h["full_gkg_row_persisted"] is False,"NO_FULL_ROW")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False,"NO_MODEL")
    req(h["paid_or_secret_source_allowed"] is False,"NO_PAID_SECRET")
    req(h["candidate_weight"]==0 and h["matrix_delta"]==0,"ZERO_WEIGHT")

    publication_parent=verify_publication_parent(p,token)
    pit,sky,parent_prov=acquire_parents(p,token)
    pit_rows={int(x["round"]):x for x in pit["rows"]}
    sky_rows={int(x["round"]):x for x in sky["rows"]}

    target=[int(x) for x in p["target_rounds"]]
    excluded=[int(x) for x in p["excluded_prior_sample_rounds"]]
    req(target==[8,11,12,14,15,16,17,19,20,21,22,23,25,27,28,32,34],"TARGET_CONTRACT")
    req(excluded==[9,24,38],"EXCLUDED_SAMPLE_CONTRACT")
    req(not (set(target)&set(excluded)),"TARGET_SAMPLE_OVERLAP")

    max_full=int(p["residual_window_contract"]["max_full_window_days_per_round"])
    max_residual=int(p["residual_window_contract"]["max_residual_days_per_round"])
    round_plan=[]
    all_publication_days=set()
    all_residual_days=set()

    for rnd in target:
        req(rnd in pit_rows and rnd in sky_rows,f"PARENT_ROW:R{rnd}")
        prow=pit_rows[rnd]
        req(prow["binding_status"]=="FAIL",f"ROUND_ALREADY_PIT_PASS:R{rnd}")
        sky_url=sky_rows[rnd].get("sky_url")
        req(isinstance(sky_url,str) and sky_url.startswith("https://sport.sky.it/"),f"SKY_URL:R{rnd}")
        pub_day,residual=residual_dates_for_round(
            prow["sky_visible_published_utc"],
            prow["first_fixture_cutoff_utc"],
            max_full,
            max_residual,
        )
        all_publication_days.add(pub_day)
        all_residual_days.update(residual)
        round_plan.append({
            "round":rnd,
            "sky_url":sky_url,
            "sky_visible_published_utc":prow["sky_visible_published_utc"],
            "first_fixture_cutoff_utc":prow["first_fixture_cutoff_utc"],
            "publication_day":pub_day,
            "residual_days":residual,
        })

    req(not (all_publication_days & all_residual_days),"PUBLICATION_DAY_REQUERY_SET")
    src=p["source"]
    cache: dict[str,dict[str,Any]]={}
    for day in sorted(all_residual_days):
        source_url=build_daily_url(src["daily_url_template"],day)
        try:
            raw,final,headers=fetch(
                source_url,
                timeout=int(src["request_timeout_seconds"]),
                limit=int(src["max_zip_bytes"]),
                allowed_host=src["allowed_host"],
            )
            cache[day]={
                "date":day,
                "source_url":source_url,
                "successful":True,
                "error":None,
                "raw":raw,
                "final_url":final,
                "zip_sha256":sha256_bytes(raw),
                "zip_bytes":len(raw),
                "content_type":headers.get("content-type"),
            }
        except Exception as exc:
            cache[day]={
                "date":day,
                "source_url":source_url,
                "successful":False,
                "error":f"{type(exc).__name__}:{exc}"[:800],
            }

    audits=[]
    positive_rounds=[]
    positive_round_days=[]
    error_rounds=set()
    error_days=sorted(day for day,x in cache.items() if not x["successful"])
    for plan in round_plan:
        day_reports=[]
        for day in plan["residual_days"]:
            source=cache[day]
            if not source["successful"]:
                error_rounds.add(plan["round"])
                day_reports.append({
                    "date":day,
                    "source_url":source["source_url"],
                    "successful":False,
                    "error":source["error"],
                    "match_n":0,
                    "matches":[],
                })
                continue
            scan=scan_zip_sky(source["raw"],plan["sky_url"],src["expected_zip_member_suffix"])
            day_reports.append({
                "date":day,
                "source_url":source["source_url"],
                "successful":True,
                "error":None,
                "final_url":source["final_url"],
                "zip_sha256":source["zip_sha256"],
                "zip_bytes":source["zip_bytes"],
                "content_type":source["content_type"],
                "member_reports":scan["member_reports"],
                "match_n":scan["match_n"],
                "matches":scan["matches"],
            })
            if scan["match_n"]>0:
                positive_round_days.append({"round":plan["round"],"date":day,"match_n":scan["match_n"]})
        pos=any(x["match_n"]>0 for x in day_reports)
        if pos:
            positive_rounds.append(plan["round"])
        audits.append({
            **plan,
            "residual_day_n":len(plan["residual_days"]),
            "successful_day_n":sum(1 for x in day_reports if x["successful"]),
            "error_day_n":sum(1 for x in day_reports if not x["successful"]),
            "positive_day_n":sum(1 for x in day_reports if x["match_n"]>0),
            "day_reports":day_reports,
        })

    positive_rounds=sorted(set(positive_rounds))
    error_rounds=sorted(error_rounds)
    total_round_day_pair_n=sum(x["residual_day_n"] for x in audits)
    successful_round_day_pair_n=sum(x["successful_day_n"] for x in audits)
    classification,next_step,gdelt_closed=classify(positive_rounds,error_days,p)

    source_day_reports=[
        {k:v for k,v in cache[day].items() if k!="raw"}
        for day in sorted(cache)
    ]
    matrix={
        "schema_version":"football3-nova-n10-referee-sky-gdelt-residual-pit-matrix-v1",
        "target_rounds":target,
        "excluded_prior_sample_rounds":excluded,
        "target_round_n":len(target),
        "round_plan":round_plan,
        "unique_publication_day_n":len(all_publication_days),
        "unique_residual_day_n":len(all_residual_days),
        "total_round_day_pair_n":total_round_day_pair_n,
        "successful_round_day_pair_n":successful_round_day_pair_n,
        "external_error_day_n":len(error_days),
        "external_error_days":error_days,
        "error_rounds":error_rounds,
        "positive_round_n":len(positive_rounds),
        "positive_rounds":positive_rounds,
        "positive_round_days":positive_round_days,
        "source_day_reports":source_day_reports,
        "audits":audits,
        "publication_days_requeried":False,
        "prior_sample_rounds_requeried":False,
        "day_level_observation_only":True,
        "exact_observation_time_proven":False,
        "full_gkg_rows_persisted":False,
        "matching_full_lines_persisted":False,
        "gdelt_for_sky_closed":gdelt_closed,
    }
    out.mkdir(parents=True,exist_ok=True)
    matrix_bytes=(json.dumps(matrix,indent=2,sort_keys=True,ensure_ascii=False)+"\n").encode("utf-8")
    (out/"sky_gdelt_residual_pit_matrix.json").write_bytes(matrix_bytes)

    receipt={
        "schema_version":"football3-nova-n10-referee-sky-gdelt-residual-pit-receipt-v1",
        "status":"N10_REFEREE_SKY_GDELT_RESIDUAL_PIT_COMPLETE",
        "classification":classification,
        "exact_base":p["exact_base"],
        "registry_sha256":sha256_bytes(registry_path.read_bytes()),
        "publication_day_parent_provenance":publication_parent,
        "parent_provenance":parent_prov,
        "target_round_n":len(target),
        "target_rounds":target,
        "excluded_prior_sample_rounds":excluded,
        "unique_publication_day_n":len(all_publication_days),
        "unique_residual_day_n":len(all_residual_days),
        "total_round_day_pair_n":total_round_day_pair_n,
        "successful_round_day_pair_n":successful_round_day_pair_n,
        "external_error_day_n":len(error_days),
        "external_error_days":error_days,
        "error_rounds":error_rounds,
        "positive_round_n":len(positive_rounds),
        "positive_rounds":positive_rounds,
        "positive_round_days":positive_round_days,
        "matrix_sha256":sha256_bytes(matrix_bytes),
        "publication_day_requery_performed":False,
        "prior_sample_round_requery_performed":False,
        "full_gkg_rows_persisted":False,
        "matching_full_lines_persisted":False,
        "article_body_read":False,
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
        "day_level_observation_only":True,
        "exact_observation_time_proven":False,
        "formal_available_at_proven":False,
        "fixture_level_binding_complete":False,
        "referee_oof_allowed":False,
        "gdelt_for_sky_closed":gdelt_closed,
        "next_step":next_step,
    }
    (out/"sky_gdelt_residual_pit_receipt.json").write_text(
        json.dumps(receipt,indent=2,sort_keys=True,ensure_ascii=False)+"\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt,sort_keys=True,ensure_ascii=False))
    return receipt

def main() -> None:
    a=argparse.ArgumentParser()
    a.add_argument("--registry",type=Path,required=True)
    a.add_argument("--out",type=Path,required=True)
    x=a.parse_args()
    run(x.registry,x.out,os.environ.get("GITHUB_TOKEN",""))

if __name__=="__main__":
    main()
