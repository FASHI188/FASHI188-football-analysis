#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

from nova_n10_referee_aia_gdelt_gkg_daily_v1 import (
    build_daily_url,
    fetch,
    sha256_bytes,
)
from nova_n10_referee_sky_arquivo_feasibility_v1 import acquire_parents
from nova_n10_referee_sky_gdelt_daily_feasibility_v1 import scan_zip_sky

UTC=dt.timezone.utc

class SkyGDELTPublicationDayError(RuntimeError):
    pass

def req(cond: bool, msg: str) -> None:
    if not cond:
        raise SkyGDELTPublicationDayError(msg)

def parse_z(v: str) -> dt.datetime:
    x=dt.datetime.fromisoformat(v.replace("Z","+00:00"))
    if x.tzinfo is None:
        x=x.replace(tzinfo=UTC)
    return x.astimezone(UTC)

def publication_day(pub_utc: str, cutoff_utc: str) -> str:
    pub=parse_z(pub_utc)
    cutoff=parse_z(cutoff_utc)
    req(pub < cutoff,"PUB_NOT_PRE_CUTOFF")
    return pub.date().isoformat()

def classify(audits: list[dict[str,Any]], registry: dict[str,Any]) -> tuple[str,str]:
    d=registry["decision_contract"]
    positive=[x["round"] for x in audits if x["positive_day"]]
    errors=sum(1 for x in audits if x["error"] is not None)
    if positive:
        return d["positive_classification"],registry["reasonable_subroutes"]["if_positive"]
    if errors:
        return d["external_block_classification"],registry["reasonable_subroutes"]["if_external_error"]
    return d["zero_classification"],registry["reasonable_subroutes"]["if_complete_zero"]

def run(registry_path: Path, out: Path, token: str) -> dict[str,Any]:
    p=json.loads(registry_path.read_text(encoding="utf-8"))
    req(p["status"]=="DESIGN_LOCKED_ZERO_LABEL_METADATA_ONLY","STATUS")
    req(p["exact_base"]=="ab817a9c05d41b0d97418aa6a40189753bb34d2a","EXACT_BASE")

    h=p["hard_rules"]
    req(h["prior_source_requery_allowed"] is False,"NO_PRIOR_SOURCE_REQUERY")
    req(h["prior_sample_round_requery_allowed"] is False,"NO_PRIOR_SAMPLE_REQUERY")
    req(h["result_labels_read"] is False and h["score_values_read"] is False,"ZERO_LABEL")
    req(h["score_or_result_semantic_parse"] is False,"NO_RESULT_PARSE")
    req(h["article_body_read"] is False and h["referee_assignment_body_parsed"] is False,"NO_BODY")
    req(h["full_gkg_row_persisted"] is False,"NO_FULL_ROW")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False,"NO_MODEL")
    req(h["paid_or_secret_source_allowed"] is False,"NO_PAID_SECRET")
    req(h["candidate_weight"]==0 and h["matrix_delta"]==0,"ZERO_WEIGHT")

    all_unresolved=[int(x) for x in p["unresolved_rounds_all"]]
    prior_sample=[int(x) for x in p["sample_rounds_already_scanned"]]
    target=[int(x) for x in p["target_rounds"]]
    req(all_unresolved==[8,9,11,12,14,15,16,17,19,20,21,22,23,24,25,27,28,32,34,38],"UNRESOLVED_CONTRACT")
    req(prior_sample==[9,24,38],"PRIOR_SAMPLE_CONTRACT")
    req(target==[8,11,12,14,15,16,17,19,20,21,22,23,25,27,28,32,34],"TARGET_CONTRACT")
    req(sorted(set(all_unresolved)-set(prior_sample))==target,"TARGET_SET_DIFFERENCE")
    req(not (set(target)&set(prior_sample)),"PRIOR_SAMPLE_REQUERY")

    pit,sky,parent_prov=acquire_parents(p,token)
    pit_rows={int(x["round"]):x for x in pit["rows"]}
    sky_rows={int(x["round"]):x for x in sky["rows"]}

    src=p["source"]
    day_rows=[]
    for rnd in target:
        req(rnd in pit_rows and rnd in sky_rows,f"PARENT_ROW:R{rnd}")
        prow=pit_rows[rnd]
        req(prow["binding_status"]=="FAIL",f"ROUND_ALREADY_PIT_PASS:R{rnd}")
        sky_url=sky_rows[rnd].get("sky_url")
        req(isinstance(sky_url,str) and sky_url.startswith("https://sport.sky.it/"),f"SKY_URL:R{rnd}")
        day=publication_day(prow["sky_visible_published_utc"],prow["first_fixture_cutoff_utc"])
        day_rows.append((rnd,day,prow,sky_url))

    unique_days=sorted({d for _,d,_,_ in day_rows})
    req(len(day_rows)==int(p["publication_day_contract"]["expected_round_n"]),"ROUND_N")
    req(len(unique_days)==int(p["publication_day_contract"]["expected_unique_day_n"]),"UNIQUE_DAY_N")

    audits=[]
    for rnd,day,prow,sky_url in day_rows:
        source_url=build_daily_url(src["daily_url_template"],day)
        report={
            "round":rnd,
            "publication_day_utc":day,
            "sky_url":sky_url,
            "sky_visible_published_utc":prow["sky_visible_published_utc"],
            "first_fixture_cutoff_utc":prow["first_fixture_cutoff_utc"],
            "source_url":source_url,
            "successful":False,
            "error":None,
            "match_n":0,
            "matches":[],
            "positive_day":False,
        }
        try:
            raw,final,headers=fetch(
                source_url,
                timeout=int(src["request_timeout_seconds"]),
                limit=int(src["max_zip_bytes"]),
                allowed_host=src["allowed_host"],
            )
            scan=scan_zip_sky(raw,sky_url,src["expected_zip_member_suffix"])
            report.update({
                "successful":True,
                "final_url":final,
                "zip_sha256":sha256_bytes(raw),
                "zip_bytes":len(raw),
                "content_type":headers.get("content-type"),
                "member_reports":scan["member_reports"],
                "match_n":scan["match_n"],
                "matches":scan["matches"],
                "positive_day":scan["match_n"]>0,
            })
        except Exception as exc:
            report["error"]=f"{type(exc).__name__}:{exc}"[:800]
        audits.append(report)

    positive_rounds=sorted(x["round"] for x in audits if x["positive_day"])
    error_rounds=sorted(x["round"] for x in audits if x["error"] is not None)
    successful_rounds=sorted(x["round"] for x in audits if x["successful"])
    zero_rounds=sorted(x["round"] for x in audits if x["successful"] and not x["positive_day"])
    classification,next_step=classify(audits,p)

    matrix={
        "schema_version":"football3-nova-n10-referee-sky-gdelt-publicationday17-matrix-v1",
        "target_rounds":target,
        "target_round_n":len(target),
        "unique_publication_day_n":len(unique_days),
        "successful_rounds":successful_rounds,
        "successful_round_n":len(successful_rounds),
        "error_rounds":error_rounds,
        "error_round_n":len(error_rounds),
        "positive_rounds":positive_rounds,
        "positive_round_n":len(positive_rounds),
        "zero_rounds":zero_rounds,
        "zero_round_n":len(zero_rounds),
        "audits":audits,
        "publication_day_only":True,
        "residual_pit_days_scanned":False,
        "day_level_observation_only":True,
        "exact_observation_time_proven":False,
        "full_gkg_rows_persisted":False,
        "matching_full_lines_persisted":False,
    }
    out.mkdir(parents=True,exist_ok=True)
    matrix_bytes=(json.dumps(matrix,indent=2,sort_keys=True,ensure_ascii=False)+"\n").encode("utf-8")
    (out/"sky_gdelt_publicationday17_matrix.json").write_bytes(matrix_bytes)

    receipt={
        "schema_version":"football3-nova-n10-referee-sky-gdelt-publicationday17-receipt-v1",
        "status":"N10_REFEREE_SKY_GDELT_PUBLICATIONDAY17_COMPLETE",
        "classification":classification,
        "exact_base":p["exact_base"],
        "registry_sha256":sha256_bytes(registry_path.read_bytes()),
        "parent_provenance":parent_prov,
        "previous_sample_artifact_sha256":p["previous_sample"]["artifact_zip_sha256"],
        "previous_sample_matrix_sha256":p["previous_sample"]["matrix_sha256"],
        "prior_sample_rounds":prior_sample,
        "target_rounds":target,
        "target_round_n":len(target),
        "unique_publication_day_n":len(unique_days),
        "successful_round_n":len(successful_rounds),
        "successful_rounds":successful_rounds,
        "error_round_n":len(error_rounds),
        "error_rounds":error_rounds,
        "positive_round_n":len(positive_rounds),
        "positive_rounds":positive_rounds,
        "zero_round_n":len(zero_rounds),
        "zero_rounds":zero_rounds,
        "matrix_sha256":sha256_bytes(matrix_bytes),
        "prior_source_requery_performed":False,
        "prior_sample_round_requery_performed":False,
        "publication_day_only":True,
        "residual_pit_days_scanned":False,
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
        "next_step":next_step,
    }
    (out/"sky_gdelt_publicationday17_receipt.json").write_text(
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
