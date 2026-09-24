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

class PullPushFeasibilityError(RuntimeError):
    pass

def req(cond: bool, msg: str) -> None:
    if not cond:
        raise PullPushFeasibilityError(msg)

def parse_z(v: str) -> dt.datetime:
    x=dt.datetime.fromisoformat(v.replace("Z","+00:00"))
    if x.tzinfo is None:
        x=x.replace(tzinfo=UTC)
    return x.astimezone(UTC)

def host(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()

def build_query(
    endpoint: str,
    variant: str,
    target: str,
    lower_epoch: int,
    upper_epoch: int,
    source: dict[str,Any],
    contract: dict[str,Any],
) -> str:
    if variant=="exact_url":
        q=target
    elif variant=="quoted_exact_url":
        q=f'"{target}"'
    else:
        raise PullPushFeasibilityError(f"UNKNOWN_VARIANT:{variant}")
    params=[
        ("q",q),
        ("size",str(source["size"])),
        ("sort",str(contract["sort"])),
        ("sort_type",str(contract["sort_type"])),
        ("after",str(lower_epoch)),
        ("before",str(upper_epoch-1)),
        ("fields",",".join(source["requested_fields"])),
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

def parse_response(raw: bytes, source: dict[str,Any]) -> tuple[list[dict[str,Any]],dict[str,Any]]:
    obj=json.loads(raw.decode("utf-8"))
    req(isinstance(obj,dict),"RESPONSE_SHAPE")
    data=obj.get("data")
    req(isinstance(data,list),"DATA_SHAPE")
    forbidden={"title","selftext","body","author"}
    safe=[]
    for row in data:
        req(isinstance(row,dict),"ROW_SHAPE")
        req(not (forbidden & set(row.keys())),"FORBIDDEN_CONTENT_FIELD_RETURNED")
        safe.append({
            "id":row.get("id"),
            "created_utc":row.get("created_utc"),
            "url":row.get("url"),
            "url_overridden_by_dest":row.get("url_overridden_by_dest"),
            "permalink":row.get("permalink"),
            "subreddit":row.get("subreddit"),
        })
    req(len(safe)<int(source["max_result_cap"]),"RESULT_CAP_REACHED")
    meta=obj.get("metadata")
    return safe,{"metadata":meta if isinstance(meta,dict) else None}

def destination_url(row: dict[str,Any]) -> str | None:
    for k in ("url_overridden_by_dest","url"):
        v=row.get(k)
        if isinstance(v,str) and (v.startswith("https://") or v.startswith("http://")):
            return v
    return None

def created_time(v: Any) -> dt.datetime | None:
    try:
        return dt.datetime.fromtimestamp(float(v),tz=UTC)
    except Exception:
        return None

def evaluate(row: dict[str,Any], target: str, lower: dt.datetime, upper: dt.datetime) -> dict[str,Any]:
    dest=destination_url(row)
    when=created_time(row.get("created_utc"))
    identity_ok=dest is not None and normalize_sky_identity(dest)==normalize_sky_identity(target)
    time_ok=when is not None and lower<=when<upper
    return {
        **row,
        "destination_url":dest,
        "created_utc_iso":when.isoformat().replace("+00:00","Z") if when else None,
        "exact_identity":bool(identity_ok),
        "pit_time_ok":bool(time_ok),
        "witness_pass":bool(identity_ok and time_ok),
    }

def classify(pass_n: int, error_n: int, p: dict[str,Any]) -> tuple[str,str]:
    if pass_n>0:
        return p["decision_contract"]["positive_classification"],p["reasonable_subroutes"]["if_positive"]
    if error_n>0:
        return p["decision_contract"]["fail_classification"],p["reasonable_subroutes"]["if_external_or_contract_error"]
    return p["decision_contract"]["fail_classification"],p["reasonable_subroutes"]["if_complete_zero"]

def run(registry_path: Path, out: Path, token: str) -> dict[str,Any]:
    p=json.loads(registry_path.read_text(encoding="utf-8"))
    req(p["status"]=="DESIGN_LOCKED_ZERO_LABEL_METADATA_ONLY","STATUS")
    req(p["exact_base"]=="a7a827afabdd3b1d1dda76da454319ccc4ffe148","EXACT_BASE")
    h=p["hard_rules"]
    req(h["previous_source_requery_allowed"] is False,"NO_OLD_SOURCE_REQUERY")
    req(h["title_read"] is False and h["selftext_read"] is False and h["comments_read"] is False and h["author_read"] is False,"NO_CONTENT")
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
    variants=p["query_contract"]["variants"]
    req(variants==["exact_url","quoted_exact_url"],"VARIANTS")

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
        lower_epoch=int(lower.timestamp())
        upper_epoch=int(upper.timestamp())
        seen={}
        variant_reports=[]
        for variant in variants:
            vr={"variant":variant,"status":"UNSET","hit_n":0,"error":None}
            q=build_query(source["endpoint"],variant,target,lower_epoch,upper_epoch,source,contract)
            vr["query_url"]=q
            try:
                raw,final,headers=fetch_json(q,source)
                hits,meta=parse_response(raw,source)
                vr.update({
                    "status":"SUCCESS",
                    "final_url":final,
                    "response_sha256":sha256_bytes(raw),
                    "response_bytes":len(raw),
                    "content_type":headers.get("content-type"),
                    "hit_n":len(hits),
                    "response_meta":meta,
                })
                for row in hits:
                    key=str(row.get("id") or "")+"|"+str(destination_url(row) or "")
                    seen[key]=row
            except Exception as exc:
                vr["status"]="ERROR"
                vr["error"]=f"{type(exc).__name__}:{exc}"[:800]
                errors.append({"round":rnd,"variant":variant,"query_url":q,"error":vr["error"]})
            variant_reports.append(vr)

        evaluated=[evaluate(x,target,lower,upper) for x in seen.values()]
        witness=[x for x in evaluated if x["witness_pass"]]
        for x in witness:
            passes.append({"round":rnd,**x})
        reports.append({
            "round":rnd,
            "target_url":target,
            "lower_utc":lower.isoformat().replace("+00:00","Z"),
            "upper_utc":upper.isoformat().replace("+00:00","Z"),
            "variant_reports":variant_reports,
            "unique_hit_n":len(evaluated),
            "hits":evaluated,
            "witness_pass_n":len(witness),
            "witness_hits":witness,
        })

    pass_rounds=sorted(set(int(x["round"]) for x in passes))
    classification,next_step=classify(len(passes),len(errors),p)
    matrix={
        "schema_version":"football3-nova-n10-referee-sky-pullpush-feasibility-matrix-v1",
        "sample_n":len(samples),
        "sample_rounds":samples,
        "query_variants":variants,
        "error_n":len(errors),
        "errors":errors,
        "witness_pass_n":len(passes),
        "witness_pass_rounds":pass_rounds,
        "reports":reports,
        "title_read":False,
        "selftext_read":False,
        "comments_read":False,
        "author_read":False,
    }
    out.mkdir(parents=True,exist_ok=True)
    matrix_bytes=(json.dumps(matrix,indent=2,sort_keys=True,ensure_ascii=False)+"\n").encode("utf-8")
    (out/"sky_pullpush_feasibility_matrix.json").write_bytes(matrix_bytes)
    receipt={
        "schema_version":"football3-nova-n10-referee-sky-pullpush-feasibility-receipt-v1",
        "status":"N10_REFEREE_SKY_PULLPUSH_FEASIBILITY_COMPLETE",
        "classification":classification,
        "exact_base":p["exact_base"],
        "registry_sha256":sha256_bytes(registry_path.read_bytes()),
        "parent_provenance":parent_prov,
        "sample_n":len(samples),
        "sample_rounds":samples,
        "query_variants":variants,
        "error_n":len(errors),
        "errors":errors,
        "witness_pass_n":len(passes),
        "witness_pass_rounds":pass_rounds,
        "matrix_sha256":sha256_bytes(matrix_bytes),
        "title_read":False,
        "selftext_read":False,
        "comments_read":False,
        "author_read":False,
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
    (out/"sky_pullpush_feasibility_receipt.json").write_text(
        json.dumps(receipt,indent=2,sort_keys=True,ensure_ascii=False)+"\n",encoding="utf-8"
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
