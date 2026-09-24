#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import ssl
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from nova_n10_referee_sky_arquivo_feasibility_v1 import acquire_parents
from nova_n10_referee_sky_combined_freeze_v1 import sha256_bytes
from nova_n10_referee_sky_pit_binding_v1 import normalize_sky_identity

UTC=dt.timezone.utc

class HNAlgoliaFeasibilityError(RuntimeError):
    pass

def req(cond: bool, msg: str) -> None:
    if not cond:
        raise HNAlgoliaFeasibilityError(msg)

def parse_z(v: str) -> dt.datetime:
    x=dt.datetime.fromisoformat(v.replace("Z","+00:00"))
    if x.tzinfo is None:
        x=x.replace(tzinfo=UTC)
    return x.astimezone(UTC)

def host(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()

def build_query(
    endpoint: str,
    target: str,
    lower_epoch: int,
    upper_epoch: int,
    page: int,
    source: dict[str,Any],
    contract: dict[str,Any],
) -> str:
    params=[
        ("query",target),
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

def fetch_json(url: str, source: dict[str,Any]) -> tuple[bytes,str,dict[str,str]]:
    req(host(url)==source["allowed_host"],"REQUEST_HOST")
    request=urllib.request.Request(
        url,
        headers={
            "User-Agent":source["user_agent"],
            "Accept":"application/json",
        },
    )
    with urllib.request.urlopen(
        request,
        timeout=int(source["request_timeout_seconds"]),
        context=ssl.create_default_context(),
    ) as response:
        final=response.geturl()
        req(host(final)==source["allowed_host"],"REDIRECT_HOST")
        raw=response.read(int(source["max_response_bytes"])+1)
        req(len(raw)<=int(source["max_response_bytes"]),"RESPONSE_TOO_LARGE")
        return raw,final,{k.lower():v for k,v in response.headers.items()}

def parse_page(raw: bytes) -> tuple[list[dict[str,Any]],dict[str,Any]]:
    obj=json.loads(raw.decode("utf-8"))
    req(isinstance(obj,dict),"RESPONSE_SHAPE")
    hits=obj.get("hits")
    req(isinstance(hits,list),"HITS_SHAPE")
    safe=[]
    forbidden={"title","author","story_text","comment_text"}
    for hit in hits:
        req(isinstance(hit,dict),"HIT_SHAPE")
        req(not (forbidden & set(hit.keys())),"UNEXPECTED_TEXT_FIELD_RETURNED")
        safe.append({
            "objectID":hit.get("objectID"),
            "url":hit.get("url"),
            "created_at_i":hit.get("created_at_i"),
        })
    meta={
        "page":obj.get("page"),
        "nbPages":obj.get("nbPages"),
        "nbHits":obj.get("nbHits"),
        "hitsPerPage":obj.get("hitsPerPage"),
    }
    return safe,meta

def created_time(v: Any) -> dt.datetime | None:
    try:
        return dt.datetime.fromtimestamp(int(v),tz=UTC)
    except Exception:
        return None

def evaluate_hit(hit: dict[str,Any], target: str, lower: dt.datetime, upper: dt.datetime) -> dict[str,Any]:
    url=hit.get("url")
    when=created_time(hit.get("created_at_i"))
    identity_ok=isinstance(url,str) and normalize_sky_identity(url)==normalize_sky_identity(target)
    time_ok=when is not None and lower<=when<upper
    return {
        "objectID":hit.get("objectID"),
        "url":url,
        "created_at_i":hit.get("created_at_i"),
        "created_at_utc":when.isoformat().replace("+00:00","Z") if when else None,
        "exact_identity":bool(identity_ok),
        "pit_time_ok":bool(time_ok),
        "witness_pass":bool(identity_ok and time_ok),
    }

def query_target(
    target: str,
    lower: dt.datetime,
    upper: dt.datetime,
    source: dict[str,Any],
    contract: dict[str,Any],
) -> dict[str,Any]:
    lower_epoch=int(lower.timestamp())
    upper_epoch=int(upper.timestamp())
    all_hits=[]
    pages=[]
    first_meta=None
    for page in range(int(source["max_pages"])):
        q=build_query(source["endpoint"],target,lower_epoch,upper_epoch,page,source,contract)
        raw,final,headers=fetch_json(q,source)
        hits,meta=parse_page(raw)
        if first_meta is None:
            first_meta=meta
        pages.append({
            "page":page,
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
            raise HNAlgoliaFeasibilityError("NBPAGES_INVALID")
        req(nb_pages>=0,"NBPAGES_NEGATIVE")
        if page+1>=nb_pages:
            break
    else:
        req(False,"PAGINATION_EXCEEDS_BOUND")
    return {
        "pages":pages,
        "hits":all_hits,
        "first_meta":first_meta,
    }

def classify(pass_n: int, error_n: int, p: dict[str,Any]) -> tuple[str,str]:
    if pass_n>0:
        return p["decision_contract"]["positive_classification"],p["reasonable_subroutes"]["if_positive"]
    if error_n>0:
        return p["decision_contract"]["fail_classification"],p["reasonable_subroutes"]["if_external_error"]
    return p["decision_contract"]["fail_classification"],p["reasonable_subroutes"]["if_complete_zero"]

def run(registry_path: Path, out: Path, token: str) -> dict[str,Any]:
    p=json.loads(registry_path.read_text(encoding="utf-8"))
    req(p["status"]=="DESIGN_LOCKED_ZERO_LABEL_METADATA_ONLY","STATUS")
    req(p["exact_base"]=="5d0a8263f29d571d726e5ee6f831b329d58f69b1","EXACT_BASE")
    h=p["hard_rules"]
    req(h["previous_source_requery_allowed"] is False,"NO_OLD_SOURCE_REQUERY")
    req(h["title_read"] is False and h["author_read"] is False,"NO_TITLE_AUTHOR")
    req(h["story_text_read"] is False and h["comment_text_read"] is False,"NO_TEXT_BODY")
    req(h["result_labels_read"] is False and h["score_values_read"] is False,"ZERO_LABEL")
    req(h["score_or_result_semantic_parse"] is False,"NO_RESULT_PARSE")
    req(h["referee_assignment_body_parsed"] is False,"NO_ASSIGNMENT_BODY")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False,"NO_MODEL")
    req(h["paid_or_secret_source_allowed"] is False,"NO_PAID_SECRET")
    req(h["candidate_weight"]==0 and h["matrix_delta"]==0,"ZERO_WEIGHT")

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
            "status":"UNSET",
            "query_pages":[],
            "hit_n":0,
            "witness_pass_n":0,
            "witness_hits":[],
        }
        try:
            result=query_target(target,lower,upper,source,contract)
            evaluated=[evaluate_hit(x,target,lower,upper) for x in result["hits"]]
            witness=[x for x in evaluated if x["witness_pass"]]
            report.update({
                "status":"SUCCESS",
                "query_pages":result["pages"],
                "hit_n":len(evaluated),
                "hits":evaluated,
                "witness_pass_n":len(witness),
                "witness_hits":witness,
            })
            for x in witness:
                passes.append({"round":rnd,**x})
        except Exception as exc:
            report["status"]="EXTERNAL_OR_CONTRACT_ERROR"
            report["error"]=f"{type(exc).__name__}:{exc}"[:800]
            errors.append({"round":rnd,"error":report["error"]})
        reports.append(report)

    pass_rounds=sorted(set(int(x["round"]) for x in passes))
    classification,next_step=classify(len(passes),len(errors),p)
    matrix={
        "schema_version":"football3-nova-n10-referee-sky-hnalgolia-feasibility-matrix-v1",
        "sample_n":len(samples),
        "sample_rounds":samples,
        "error_n":len(errors),
        "errors":errors,
        "witness_pass_n":len(passes),
        "witness_pass_rounds":pass_rounds,
        "reports":reports,
        "title_read":False,
        "author_read":False,
        "story_text_read":False,
        "comment_text_read":False,
    }
    out.mkdir(parents=True,exist_ok=True)
    matrix_bytes=(json.dumps(matrix,indent=2,sort_keys=True,ensure_ascii=False)+"\n").encode("utf-8")
    (out/"sky_hnalgolia_feasibility_matrix.json").write_bytes(matrix_bytes)

    receipt={
        "schema_version":"football3-nova-n10-referee-sky-hnalgolia-feasibility-receipt-v1",
        "status":"N10_REFEREE_SKY_HNALGOLIA_FEASIBILITY_COMPLETE",
        "classification":classification,
        "exact_base":p["exact_base"],
        "registry_sha256":sha256_bytes(registry_path.read_bytes()),
        "parent_provenance":parent_prov,
        "sample_n":len(samples),
        "sample_rounds":samples,
        "error_n":len(errors),
        "errors":errors,
        "witness_pass_n":len(passes),
        "witness_pass_rounds":pass_rounds,
        "matrix_sha256":sha256_bytes(matrix_bytes),
        "title_read":False,
        "author_read":False,
        "story_text_read":False,
        "comment_text_read":False,
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
    (out/"sky_hnalgolia_feasibility_receipt.json").write_text(
        json.dumps(receipt,indent=2,sort_keys=True,ensure_ascii=False)+"\n",
        encoding="utf-8",
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
