#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import urllib.parse
from pathlib import Path
from typing import Any

from nova_n10_referee_sky_arquivo_feasibility_v1 import acquire_parents
from nova_n10_referee_sky_combined_freeze_v1 import sha256_bytes
from nova_n10_referee_sky_hnalgolia_feasibility_v1 import (
    evaluate_hit,
    fetch_json,
    parse_page,
    parse_z,
)
from nova_n10_referee_sky_pit_binding_v1 import normalize_sky_identity

class HNAlgoliaTokenDiscoveryError(RuntimeError):
    pass

def req(cond: bool, msg: str) -> None:
    if not cond:
        raise HNAlgoliaTokenDiscoveryError(msg)

def build_variant_query(variant: str, target: str) -> str:
    p=urllib.parse.urlparse(target)
    host=(p.hostname or "").lower()
    path=urllib.parse.unquote(p.path or "").strip("/")
    slug=path.split("/")[-1] if path else ""
    req(host=="sport.sky.it","TARGET_HOST")
    req(bool(slug),"TARGET_SLUG")
    if variant=="host_path_tokens":
        # Punctuation-separated URL pieces converted to stable search tokens.
        tokens=[host]+[x for x in path.replace("-"," ").split("/") if x]
        return " ".join(tokens)
    if variant=="slug_only":
        return slug.replace("-"," ")
    raise HNAlgoliaTokenDiscoveryError(f"UNKNOWN_VARIANT:{variant}")

def build_query(
    endpoint: str,
    variant: str,
    target: str,
    lower_epoch: int,
    upper_epoch: int,
    page: int,
    source: dict[str,Any],
    contract: dict[str,Any],
) -> str:
    qtext=build_variant_query(variant,target)
    params=[
        ("query",qtext),
        ("tags",str(contract["tags"])),
        ("restrictSearchableAttributes",str(contract["restrict_searchable_attributes"])),
        ("numericFilters",f"created_at_i>={lower_epoch},created_at_i<{upper_epoch}"),
        ("attributesToRetrieve",",".join(source["attributes_to_retrieve"])),
        ("attributesToHighlight",""),
        ("attributesToSnippet",""),
        ("hitsPerPage",str(source["hits_per_page"])),
        ("page",str(page)),
    ]
    return endpoint+"?"+urllib.parse.urlencode(params)

def query_variant(
    variant: str,
    target: str,
    lower: dt.datetime,
    upper: dt.datetime,
    source: dict[str,Any],
    contract: dict[str,Any],
) -> dict[str,Any]:
    lower_epoch=int(lower.timestamp())
    upper_epoch=int(upper.timestamp())
    pages=[]
    all_hits=[]
    for page in range(int(source["max_pages"])):
        q=build_query(source["endpoint"],variant,target,lower_epoch,upper_epoch,page,source,contract)
        raw,final,headers=fetch_json(q,source)
        hits,meta=parse_page(raw)
        pages.append({
            "page":page,
            "query_variant":variant,
            "query_text":build_variant_query(variant,target),
            "query_url":q,
            "final_url":final,
            "response_sha256":sha256_bytes(raw),
            "response_bytes":len(raw),
            "content_type":headers.get("content-type"),
            "hit_n":len(hits),
            "meta":meta,
        })
        all_hits.extend(hits)
        try:
            nb_pages=int(meta["nbPages"])
        except Exception:
            raise HNAlgoliaTokenDiscoveryError("NBPAGES_INVALID")
        req(nb_pages>=0,"NBPAGES_NEGATIVE")
        if page+1>=nb_pages:
            break
    else:
        req(False,"PAGINATION_EXCEEDS_BOUND")
    return {"pages":pages,"hits":all_hits}

def classify(pass_n: int, error_n: int, p: dict[str,Any]) -> tuple[str,str]:
    if pass_n>0:
        return p["decision_contract"]["positive_classification"],p["reasonable_subroutes"]["if_positive"]
    if error_n>0:
        return p["decision_contract"]["fail_classification"],p["reasonable_subroutes"]["if_external_error"]
    return p["decision_contract"]["fail_classification"],p["reasonable_subroutes"]["if_complete_zero"]

def run(registry_path: Path, out: Path, token: str) -> dict[str,Any]:
    p=json.loads(registry_path.read_text(encoding="utf-8"))
    req(p["status"]=="DESIGN_LOCKED_ZERO_LABEL_METADATA_ONLY","STATUS")
    req(p["exact_base"]=="60025cca45ca91f9bcd764a2a971716db8ee9fdf","EXACT_BASE")
    h=p["hard_rules"]
    req(h["previous_source_requery_allowed"] is False,"NO_OLD_SOURCE_REQUERY")
    req(h["third_query_variant_allowed"] is False,"NO_THIRD_VARIANT")
    req(h["title_read"] is False and h["author_read"] is False,"NO_TITLE_AUTHOR")
    req(h["story_text_read"] is False and h["comment_text_read"] is False,"NO_TEXT_BODY")
    req(h["result_labels_read"] is False and h["score_values_read"] is False,"ZERO_LABEL")
    req(h["score_or_result_semantic_parse"] is False,"NO_RESULT_PARSE")
    req(h["referee_assignment_body_parsed"] is False,"NO_ASSIGNMENT_BODY")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False,"NO_MODEL")
    req(h["paid_or_secret_source_allowed"] is False,"NO_PAID_SECRET")
    req(h["candidate_weight"]==0 and h["matrix_delta"]==0,"ZERO_WEIGHT")
    req(p["query_contract"]["variants"]==["host_path_tokens","slug_only"],"VARIANTS")
    req(int(p["query_contract"]["max_discovery_batches"])==1,"MAX_BATCH")

    pit,sky,parent_prov=acquire_parents(p,token)
    pit_rows={int(x["round"]):x for x in pit["rows"]}
    sky_rows={int(x["round"]):x for x in sky["rows"]}
    samples=[int(x["round"]) for x in p["samples"]]
    req(samples==[8,24,34],"FROZEN_SAMPLES")

    source=p["source"]
    contract=p["query_contract"]
    reports=[]
    errors=[]
    passes=[]

    for rnd in samples:
        prow=pit_rows[rnd]
        srow=sky_rows[rnd]
        req(prow["binding_status"]=="FAIL",f"SAMPLE_ALREADY_PASS:R{rnd}")
        target=srow.get("sky_url")
        req(isinstance(target,str) and target.startswith("https://sport.sky.it/"),f"SKY_URL:R{rnd}")
        lower=parse_z(prow["sky_visible_published_utc"])-dt.timedelta(seconds=300)
        upper=parse_z(prow["first_fixture_cutoff_utc"])
        report={
            "round":rnd,
            "target_url":target,
            "lower_utc":lower.isoformat().replace("+00:00","Z"),
            "upper_utc":upper.isoformat().replace("+00:00","Z"),
            "variant_reports":[],
            "witness_pass_n":0,
            "witness_hits":[],
        }
        dedup={}
        for variant in contract["variants"]:
            vr={"variant":variant,"status":"UNSET","query_pages":[],"hit_n":0,"error":None}
            try:
                result=query_variant(variant,target,lower,upper,source,contract)
                vr["status"]="SUCCESS"
                vr["query_pages"]=result["pages"]
                vr["hit_n"]=len(result["hits"])
                for hit in result["hits"]:
                    key=str(hit.get("objectID") or "")+"|"+str(hit.get("url") or "")
                    dedup[key]=hit
            except Exception as exc:
                vr["status"]="ERROR"
                vr["error"]=f"{type(exc).__name__}:{exc}"[:800]
                errors.append({"round":rnd,"variant":variant,"error":vr["error"]})
            report["variant_reports"].append(vr)

        evaluated=[evaluate_hit(x,target,lower,upper) for x in dedup.values()]
        witness=[x for x in evaluated if x["witness_pass"]]
        report["unique_hit_n"]=len(evaluated)
        report["hits"]=evaluated
        report["witness_pass_n"]=len(witness)
        report["witness_hits"]=witness
        for x in witness:
            passes.append({"round":rnd,**x})
        reports.append(report)

    pass_rounds=sorted(set(int(x["round"]) for x in passes))
    classification,next_step=classify(len(passes),len(errors),p)
    matrix={
        "schema_version":"football3-nova-n10-referee-sky-hnalgolia-token-discovery-matrix-v1",
        "sample_n":len(samples),
        "sample_rounds":samples,
        "query_variants":contract["variants"],
        "error_n":len(errors),
        "errors":errors,
        "witness_pass_n":len(passes),
        "witness_pass_rounds":pass_rounds,
        "reports":reports,
        "title_read":False,
        "author_read":False,
        "story_text_read":False,
        "comment_text_read":False,
        "third_query_variant_used":False,
    }
    out.mkdir(parents=True,exist_ok=True)
    matrix_bytes=(json.dumps(matrix,indent=2,sort_keys=True,ensure_ascii=False)+"\n").encode("utf-8")
    (out/"sky_hnalgolia_token_discovery_matrix.json").write_bytes(matrix_bytes)

    receipt={
        "schema_version":"football3-nova-n10-referee-sky-hnalgolia-token-discovery-receipt-v1",
        "status":"N10_REFEREE_SKY_HNALGOLIA_TOKEN_DISCOVERY_COMPLETE",
        "classification":classification,
        "exact_base":p["exact_base"],
        "registry_sha256":sha256_bytes(registry_path.read_bytes()),
        "parent_provenance":parent_prov,
        "previous_exact_artifact_sha256":p["previous_exact_route"]["artifact_zip_sha256"],
        "previous_exact_matrix_sha256":p["previous_exact_route"]["matrix_sha256"],
        "sample_n":len(samples),
        "sample_rounds":samples,
        "query_variants":contract["variants"],
        "error_n":len(errors),
        "errors":errors,
        "witness_pass_n":len(passes),
        "witness_pass_rounds":pass_rounds,
        "matrix_sha256":sha256_bytes(matrix_bytes),
        "title_read":False,
        "author_read":False,
        "story_text_read":False,
        "comment_text_read":False,
        "third_query_variant_used":False,
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
        "formal_available_at_proven":False,
        "fixture_level_binding_complete":False,
        "referee_oof_allowed":False,
        "next_step":next_step,
    }
    (out/"sky_hnalgolia_token_discovery_receipt.json").write_text(
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
