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

class RedditFeasibilityError(RuntimeError):
    pass

def req(cond: bool, msg: str) -> None:
    if not cond:
        raise RedditFeasibilityError(msg)

def parse_z(v: str) -> dt.datetime:
    x=dt.datetime.fromisoformat(v.replace("Z","+00:00"))
    if x.tzinfo is None:
        x=x.replace(tzinfo=UTC)
    return x.astimezone(UTC)

def host(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()

def build_query(endpoint: str, variant: str, target: str, params: dict[str,str]) -> str:
    if variant=="url_exact_operator":
        q=f"url:{target}"
    elif variant=="quoted_exact_url":
        q=f'"{target}"'
    else:
        raise RedditFeasibilityError(f"UNKNOWN_VARIANT:{variant}")
    values=[("q",q)]+[(str(k),str(v)) for k,v in params.items()]
    return endpoint+"?"+urllib.parse.urlencode(values)

def fetch_json(url: str, p: dict[str,Any]) -> tuple[bytes,str,dict[str,str]]:
    req(host(url) in p["allowed_hosts"],"REQUEST_HOST")
    rq=urllib.request.Request(
        url,
        headers={"User-Agent":p["user_agent"],"Accept":"application/json"},
    )
    with urllib.request.urlopen(rq,timeout=int(p["request_timeout_seconds"]),context=ssl.create_default_context()) as r:
        final=r.geturl()
        req(host(final) in p["allowed_hosts"],"REDIRECT_HOST")
        raw=r.read(int(p["max_response_bytes"])+1)
        req(len(raw)<=int(p["max_response_bytes"]),"RESPONSE_TOO_LARGE")
        return raw,final,{k.lower():v for k,v in r.headers.items()}

def safe_posts(raw: bytes) -> list[dict[str,Any]]:
    obj=json.loads(raw.decode("utf-8"))
    children=((obj.get("data") or {}).get("children") or []) if isinstance(obj,dict) else []
    out=[]
    for child in children:
        d=(child.get("data") or {}) if isinstance(child,dict) else {}
        out.append({
            "id":d.get("id"),
            "created_utc":d.get("created_utc"),
            "subreddit":d.get("subreddit"),
            "permalink":d.get("permalink"),
            "url":d.get("url"),
            "url_overridden_by_dest":d.get("url_overridden_by_dest"),
        })
    return out

def post_destination(post: dict[str,Any]) -> str | None:
    for k in ("url_overridden_by_dest","url"):
        v=post.get(k)
        if isinstance(v,str) and (v.startswith("https://") or v.startswith("http://")):
            return v
    return None

def created_time(v: Any) -> dt.datetime | None:
    try:
        x=float(v)
        return dt.datetime.fromtimestamp(x,tz=UTC)
    except Exception:
        return None

def evaluate_post(post: dict[str,Any], target: str, lower: dt.datetime, upper: dt.datetime) -> dict[str,Any]:
    dest=post_destination(post)
    when=created_time(post.get("created_utc"))
    identity_ok=dest is not None and normalize_sky_identity(dest)==normalize_sky_identity(target)
    time_ok=when is not None and lower<=when<upper
    return {
        **post,
        "destination_url":dest,
        "created_utc_iso":when.isoformat().replace("+00:00","Z") if when else None,
        "exact_identity":identity_ok,
        "pit_time_ok":time_ok,
        "witness_pass":bool(identity_ok and time_ok),
    }

def classify(pass_n: int, query_error_n: int, p: dict[str,Any]) -> tuple[str,str]:
    if pass_n>0:
        return p["decision_contract"]["positive_classification"],p["reasonable_subroutes"]["if_positive"]
    if query_error_n>0:
        return p["decision_contract"]["fail_classification"],p["reasonable_subroutes"]["if_external_error"]
    return p["decision_contract"]["fail_classification"],p["reasonable_subroutes"]["if_complete_zero"]

def run(registry_path: Path, out: Path, token: str) -> dict[str,Any]:
    p=json.loads(registry_path.read_text(encoding="utf-8"))
    req(p["status"]=="DESIGN_LOCKED_ZERO_LABEL_METADATA_ONLY","STATUS")
    req(p["exact_base"]=="ff9374580450b26c632a7e14e9057a361f76b6f7","EXACT_BASE")
    h=p["hard_rules"]
    req(h["previous_source_requery_allowed"] is False,"NO_OLD_SOURCE_REQUERY")
    req(h["selftext_read"] is False and h["comments_read"] is False and h["post_body_read"] is False,"NO_POST_BODY")
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
    src=p["source"]
    variants=p["query_contract"]["variants"]

    reports=[]
    query_errors=[]
    pass_posts=[]
    for rnd in samples:
        prow=pit_rows[rnd]
        srow=sky_rows[rnd]
        req(prow["binding_status"]=="FAIL",f"SAMPLE_ALREADY_PASS:R{rnd}")
        target=srow.get("sky_url")
        req(isinstance(target,str) and target.startswith("https://sport.sky.it/"),f"SKY_URL:R{rnd}")
        lower=parse_z(prow["sky_visible_published_utc"])-dt.timedelta(seconds=300)
        upper=parse_z(prow["first_fixture_cutoff_utc"])
        report={"round":rnd,"target_url":target,"variant_reports":[],"witness_pass_n":0,"witness_posts":[]}
        seen={}
        for variant in variants:
            vr={"variant":variant,"endpoint_attempts":[],"successful_endpoint":None,"post_n":0}
            posts=None
            for endpoint in src["endpoints"]:
                q=build_query(endpoint,variant,target,src["query_params"])
                try:
                    raw,final,headers=fetch_json(q,src)
                    parsed=safe_posts(raw)
                    vr["endpoint_attempts"].append({
                        "endpoint":endpoint,"query_url":q,"status":"SUCCESS","final_url":final,
                        "response_sha256":sha256_bytes(raw),"response_bytes":len(raw),
                        "content_type":headers.get("content-type"),"post_n":len(parsed),
                    })
                    vr["successful_endpoint"]=endpoint
                    vr["post_n"]=len(parsed)
                    posts=parsed
                    break
                except Exception as exc:
                    vr["endpoint_attempts"].append({
                        "endpoint":endpoint,"query_url":q,"status":"ERROR",
                        "error":f"{type(exc).__name__}:{exc}"[:800],
                    })
            if posts is None:
                query_errors.append({"round":rnd,"variant":variant,"attempts":vr["endpoint_attempts"]})
            else:
                for post in posts:
                    key=str(post.get("id") or "")+"|"+str(post_destination(post) or "")
                    seen[key]=post
            report["variant_reports"].append(vr)

        evaluated=[]
        for post in seen.values():
            ev=evaluate_post(post,target,lower,upper)
            evaluated.append(ev)
            if ev["witness_pass"]:
                pass_posts.append({"round":rnd,**ev})
        report["unique_post_n"]=len(evaluated)
        report["posts"]=evaluated
        report["witness_posts"]=[x for x in evaluated if x["witness_pass"]]
        report["witness_pass_n"]=len(report["witness_posts"])
        reports.append(report)

    classification,next_step=classify(len(pass_posts),len(query_errors),p)
    pass_rounds=sorted(set(int(x["round"]) for x in pass_posts))
    matrix={
        "schema_version":"football3-nova-n10-referee-sky-reddit-feasibility-matrix-v1",
        "sample_rounds":samples,"sample_n":3,"query_error_n":len(query_errors),
        "query_errors":query_errors,"witness_pass_n":len(pass_posts),"witness_pass_rounds":pass_rounds,
        "reports":reports,"selftext_read":False,"comments_read":False,"post_body_read":False,
    }
    out.mkdir(parents=True,exist_ok=True)
    matrix_bytes=(json.dumps(matrix,indent=2,sort_keys=True,ensure_ascii=False)+"\n").encode("utf-8")
    (out/"sky_reddit_feasibility_matrix.json").write_bytes(matrix_bytes)
    receipt={
        "schema_version":"football3-nova-n10-referee-sky-reddit-feasibility-receipt-v1",
        "status":"N10_REFEREE_SKY_REDDIT_FEASIBILITY_COMPLETE","classification":classification,
        "exact_base":p["exact_base"],"registry_sha256":sha256_bytes(registry_path.read_bytes()),
        "parent_provenance":parent_prov,"sample_n":3,"sample_rounds":samples,
        "query_error_n":len(query_errors),"query_errors":query_errors,
        "witness_pass_n":len(pass_posts),"witness_pass_rounds":pass_rounds,
        "matrix_sha256":sha256_bytes(matrix_bytes),"selftext_read":False,"comments_read":False,
        "post_body_read":False,"referee_assignment_body_parsed":False,
        "match_result_payload_read":False,"standings_payload_read":False,"player_stats_payload_read":False,
        "result_labels_read":0,"score_values_read":0,"training_performed":False,"scoring_performed":False,
        "formal_v2_changed":False,"current_changed":False,"production_changed":False,
        "candidate_weight":0,"matrix_delta":0,"formal_available_at_proven":False,
        "fixture_level_binding_complete":False,"referee_oof_allowed":False,"next_step":next_step,
    }
    (out/"sky_reddit_feasibility_receipt.json").write_text(
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
