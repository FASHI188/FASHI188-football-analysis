#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from nova_n10_referee_sky_arquivo_feasibility_v1 import acquire_parents
from nova_n10_referee_sky_pit_binding_v1 import normalize_sky_identity

UTC=dt.timezone.utc

class SkyArchiveItError(RuntimeError):
    pass

def req(c: bool, m: str) -> None:
    if not c:
        raise SkyArchiveItError(m)

def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def parse_z(v: str) -> dt.datetime:
    x=dt.datetime.fromisoformat(v.replace("Z","+00:00"))
    if x.tzinfo is None:
        x=x.replace(tzinfo=UTC)
    return x.astimezone(UTC)

def host(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()

def query_bounds(lower_utc: str, upper_utc: str) -> tuple[str,str]:
    lo=parse_z(lower_utc)
    hi=parse_z(upper_utc)
    req(lo < hi,"INVALID_PIT_WINDOW")
    return lo.strftime("%Y%m%d%H%M%S"),(hi-dt.timedelta(seconds=1)).strftime("%Y%m%d%H%M%S")

def build_query(endpoint: str, original: str, lower_utc: str, upper_utc: str) -> str:
    frm,to=query_bounds(lower_utc,upper_utc)
    qs=urllib.parse.urlencode([
        ("url",original),
        ("from",frm),
        ("to",to),
        ("fl","timestamp,original,statuscode"),
        ("filter","statuscode:200"),
    ])
    return endpoint+"?"+qs

def request_bytes(
    url: str,
    source: dict[str,Any],
) -> tuple[int,bytes,str,dict[str,str]]:
    req(host(url)==source["allowed_host"],"REQUEST_HOST")
    rq=urllib.request.Request(url,headers={
        "User-Agent":source["user_agent"],
        "Accept":"text/plain,*/*;q=0.1",
    })
    limit=int(source["max_response_bytes"])
    try:
        with urllib.request.urlopen(
            rq,
            timeout=int(source["request_timeout_seconds"]),
            context=ssl.create_default_context(),
        ) as r:
            data=r.read(limit+1)
            req(len(data)<=limit,"RESPONSE_TOO_LARGE")
            final=r.geturl()
            req(host(final)==source["allowed_host"],"REDIRECT_OUTSIDE_ARCHIVEIT")
            return int(getattr(r,"status",200)),data,final,{k.lower():v for k,v in r.headers.items()}
    except urllib.error.HTTPError as e:
        data=e.read(limit+1)
        req(len(data)<=limit,"ERROR_RESPONSE_TOO_LARGE")
        final=e.geturl()
        req(host(final)==source["allowed_host"],"ERROR_REDIRECT_OUTSIDE_ARCHIVEIT")
        return int(e.code),data,final,{k.lower():v for k,v in e.headers.items()}

def parse_cdx(raw: bytes) -> list[dict[str,str]]:
    out=[]
    for line in raw.decode("utf-8","replace").splitlines():
        line=line.strip()
        if not line:
            continue
        parts=line.split()
        if len(parts)<3:
            continue
        ts,orig,status=parts[0],parts[1],parts[2]
        if len(ts)==14 and ts.isdigit() and status=="200":
            out.append({"timestamp":ts,"original":orig,"statuscode":status})
    return out

def ts14_to_dt(ts: str) -> dt.datetime:
    req(len(ts)==14 and ts.isdigit(),"TIMESTAMP_SHAPE")
    return dt.datetime.strptime(ts,"%Y%m%d%H%M%S").replace(tzinfo=UTC)

def evaluate_row(
    row: dict[str,str],
    target: str,
    lower_utc: str,
    upper_utc: str,
) -> dict[str,Any]:
    when=ts14_to_dt(row["timestamp"])
    lo=parse_z(lower_utc)
    hi=parse_z(upper_utc)
    identity_ok=normalize_sky_identity(row["original"])==normalize_sky_identity(target)
    time_ok=lo <= when < hi
    return {
        "timestamp":row["timestamp"],
        "capture_utc":when.isoformat().replace("+00:00","Z"),
        "original":row["original"],
        "statuscode":row["statuscode"],
        "exact_identity":bool(identity_ok),
        "pit_time_ok":bool(time_ok),
        "witness_pass":bool(identity_ok and time_ok),
    }

def classify(pass_n: int,error_n: int,p: dict[str,Any]) -> tuple[str,str]:
    if pass_n>0:
        return p["decision_contract"]["positive_classification"],p["reasonable_subroutes"]["if_positive"]
    if error_n>0:
        return p["decision_contract"]["fail_classification"],p["reasonable_subroutes"]["if_external_error"]
    return p["decision_contract"]["fail_classification"],p["reasonable_subroutes"]["if_complete_zero"]

def run(registry: Path,out: Path,token: str)->dict[str,Any]:
    p=json.loads(registry.read_text(encoding="utf-8"))
    req(p["status"]=="DESIGN_LOCKED_ZERO_LABEL_METADATA_ONLY","STATUS")
    req(p["exact_base"]=="1c67b272dbc2b2f9f65e9dc334fd8c1b00b419dd","EXACT_BASE")
    req(p["archiveit_precedent"]["target_family"]=="AIA","PRECEDENT_SCOPE")
    req(p["archiveit_precedent"]["http_200_n"]==5,"PRECEDENT_HTTP")
    req(p["archiveit_precedent"]["eligible_capture_n"]==0,"PRECEDENT_CAPTURE")

    h=p["hard_rules"]
    req(h["previous_source_requery_allowed"] is False,"NO_PREVIOUS_SOURCE_REQUERY")
    req(h["aia_target_requery_allowed"] is False,"NO_AIA_TARGET_REQUERY")
    req(h["collection_id_guessing_allowed"] is False,"NO_COLLECTION_GUESSING")
    req(h["archive_content_fetched"] is False and h["article_body_read"] is False,"NO_ARCHIVE_BODY")
    req(h["referee_assignment_body_parsed"] is False,"NO_ASSIGNMENT_BODY")
    req(h["result_labels_read"] is False and h["score_values_read"] is False,"ZERO_LABEL")
    req(h["score_or_result_semantic_parse"] is False,"NO_RESULT_PARSE")
    req(h["match_result_payload_read"] is False and h["standings_payload_read"] is False and h["player_stats_payload_read"] is False,"NO_SPORT_PAYLOAD")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False,"NO_MODEL")
    req(h["paid_or_secret_source_allowed"] is False,"NO_PAID_SECRET")
    req(h["candidate_weight"]==0 and h["matrix_delta"]==0,"ZERO_WEIGHT")

    pit,sky,parent_prov=acquire_parents(p,token)
    pit_rows={int(x["round"]):x for x in pit["rows"]}
    sky_rows={int(x["round"]):x for x in sky["rows"]}
    samples=[int(x["round"]) for x in p["samples"]]
    req(samples==[8,24,34],"FROZEN_SAMPLES")

    src=p["source"]
    reports=[]
    errors=[]
    witnesses=[]
    for rnd in samples:
        prow=pit_rows[rnd]
        srow=sky_rows[rnd]
        req(prow["binding_status"]=="FAIL",f"SAMPLE_ALREADY_PASS:R{rnd}")
        target=srow.get("sky_url")
        req(isinstance(target,str) and target.startswith("https://sport.sky.it/"),f"SKY_URL:R{rnd}")
        lower=prow["sky_visible_published_utc"]
        upper=prow["first_fixture_cutoff_utc"]
        req(parse_z(lower)<parse_z(upper),f"PIT_WINDOW:R{rnd}")
        q=build_query(src["cdx_endpoint"],target,lower,upper)
        report={
            "round":rnd,
            "target_url":target,
            "lower_utc":lower,
            "upper_utc":upper,
            "query_url":q,
            "status":"UNSET",
            "http_status":None,
            "cdx_row_n":0,
            "witness_pass_n":0,
            "witness_rows":[],
        }
        try:
            status,raw,final,headers=request_bytes(q,src)
            rows=parse_cdx(raw) if 200 <= status < 300 else []
            evaluated=[evaluate_row(x,target,lower,upper) for x in rows]
            passed=[x for x in evaluated if x["witness_pass"]]
            report.update({
                "status":"SUCCESS" if 200 <= status < 300 else "HTTP_ERROR",
                "http_status":status,
                "final_url":final,
                "response_bytes":len(raw),
                "response_sha256":sha256_bytes(raw),
                "content_type":headers.get("content-type"),
                "cdx_row_n":len(rows),
                "evaluated_rows":evaluated,
                "witness_pass_n":len(passed),
                "witness_rows":passed,
            })
            for x in passed:
                witnesses.append({"round":rnd,**x})
            if not (200 <= status < 300):
                err={"round":rnd,"error":f"HTTP_{status}"}
                report["error"]=err["error"]
                errors.append(err)
        except Exception as exc:
            report["status"]="EXTERNAL_OR_CONTRACT_ERROR"
            report["error"]=f"{type(exc).__name__}:{exc}"[:800]
            errors.append({"round":rnd,"error":report["error"]})
        reports.append(report)

    pass_rounds=sorted(set(int(x["round"]) for x in witnesses))
    classification,next_step=classify(len(witnesses),len(errors),p)
    matrix={
        "schema_version":"football3-nova-n10-referee-sky-archiveit-feasibility-matrix-v1",
        "sample_n":len(samples),
        "sample_rounds":samples,
        "error_n":len(errors),
        "errors":errors,
        "witness_pass_n":len(witnesses),
        "witness_pass_rounds":pass_rounds,
        "reports":reports,
        "collection_id_guessing":False,
        "archive_content_fetched":False,
        "article_body_read":False,
        "referee_assignment_body_parsed":False,
    }
    out.mkdir(parents=True,exist_ok=True)
    matrix_bytes=(json.dumps(matrix,indent=2,sort_keys=True,ensure_ascii=False)+"\n").encode("utf-8")
    (out/"sky_archiveit_feasibility_matrix.json").write_bytes(matrix_bytes)
    receipt={
        "schema_version":"football3-nova-n10-referee-sky-archiveit-feasibility-receipt-v1",
        "status":"N10_REFEREE_SKY_ARCHIVEIT_FEASIBILITY_COMPLETE",
        "classification":classification,
        "exact_base":p["exact_base"],
        "registry_sha256":sha256_bytes(registry.read_bytes()),
        "parent_provenance":parent_prov,
        "sample_n":len(samples),
        "sample_rounds":samples,
        "error_n":len(errors),
        "errors":errors,
        "witness_pass_n":len(witnesses),
        "witness_pass_rounds":pass_rounds,
        "matrix_sha256":sha256_bytes(matrix_bytes),
        "collection_id_guessing":False,
        "archive_content_fetched":False,
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
        "formal_available_at_proven":False,
        "fixture_level_binding_complete":False,
        "referee_oof_allowed":False,
        "next_step":next_step,
    }
    (out/"sky_archiveit_feasibility_receipt.json").write_text(
        json.dumps(receipt,indent=2,sort_keys=True,ensure_ascii=False)+"\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt,sort_keys=True,ensure_ascii=False))
    return receipt

def main()->None:
    a=argparse.ArgumentParser()
    a.add_argument("--registry",type=Path,required=True)
    a.add_argument("--out",type=Path,required=True)
    x=a.parse_args()
    import os
    run(x.registry,x.out,os.environ.get("GITHUB_TOKEN",""))

if __name__=="__main__":
    main()
