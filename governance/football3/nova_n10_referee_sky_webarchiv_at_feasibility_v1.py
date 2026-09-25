#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from nova_n10_referee_sky_arquivo_feasibility_v1 import acquire_parents
from nova_n10_referee_sky_pit_binding_v1 import normalize_sky_identity

UTC=dt.timezone.utc

class SkyWebarchivATError(RuntimeError):
    pass

def req(c: bool,m: str)->None:
    if not c:
        raise SkyWebarchivATError(m)

def sha256_bytes(b: bytes)->str:
    return hashlib.sha256(b).hexdigest()

def parse_z(v: str)->dt.datetime:
    x=dt.datetime.fromisoformat(v.replace("Z","+00:00"))
    if x.tzinfo is None:
        x=x.replace(tzinfo=UTC)
    return x.astimezone(UTC)

def host(url: str)->str:
    return (urllib.parse.urlparse(url).hostname or "").lower()

def http_normalized_target(url: str)->str:
    p=urllib.parse.urlparse(url)
    req((p.hostname or "").lower().endswith("sky.it"),"TARGET_HOST")
    path=p.path or "/"
    q=("?"+p.query) if p.query else ""
    return "http://"+p.netloc+path+q

def bounds(lower: str,upper: str)->tuple[str,str]:
    lo=parse_z(lower); hi=parse_z(upper)
    req(lo < hi,"INVALID_PIT_WINDOW")
    return lo.strftime("%Y%m%d%H%M%S"),(hi-dt.timedelta(seconds=1)).strftime("%Y%m%d%H%M%S")

def build_query(endpoint: str,target: str,lower: str,upper: str,limit: int)->str:
    frm,to=bounds(lower,upper)
    params=[
        ("url",http_normalized_target(target)),
        ("limit",str(limit)),
        ("from",frm),
        ("to",to),
    ]
    return endpoint+"?"+urllib.parse.urlencode(params)

def fetch(url: str,src: dict[str,Any])->tuple[int,bytes,str,dict[str,str]]:
    req(host(url)==src["allowed_host"],"REQUEST_HOST")
    request=urllib.request.Request(url,headers={
        "User-Agent":src["user_agent"],
        "Accept":"text/plain,*/*;q=0.1",
    })
    lim=int(src["max_response_bytes"])
    try:
        with urllib.request.urlopen(
            request,timeout=int(src["request_timeout_seconds"]),
            context=ssl.create_default_context(),
        ) as r:
            raw=r.read(lim+1)
            req(len(raw)<=lim,"RESPONSE_TOO_LARGE")
            final=r.geturl()
            req(host(final)==src["allowed_host"],"REDIRECT_HOST")
            return int(getattr(r,"status",200)),raw,final,{k.lower():v for k,v in r.headers.items()}
    except urllib.error.HTTPError as e:
        final=e.geturl()
        req(host(final)==src["allowed_host"],"ERROR_REDIRECT_HOST")
        return int(e.code),b"",final,{k.lower():v for k,v in e.headers.items()}

def parse_cdxj(raw: bytes)->list[dict[str,Any]]:
    out=[]
    for line in raw.decode("utf-8","replace").splitlines():
        s=line.strip()
        if not s:
            continue
        parts=s.split(None,2)
        req(len(parts)==3,"MALFORMED_CDXJ_LINE")
        ts=parts[1]
        req(len(ts)==14 and ts.isdigit(),"MALFORMED_TIMESTAMP")
        try:
            obj=json.loads(parts[2])
        except Exception as exc:
            raise SkyWebarchivATError("MALFORMED_CDXJ_JSON") from exc
        req(isinstance(obj,dict),"CDXJ_JSON_NOT_OBJECT")
        url=obj.get("url")
        req(isinstance(url,str) and url.strip(),"CDXJ_URL_MISSING")
        status=obj.get("status")
        try:
            status_i=int(status) if status is not None else None
        except Exception:
            status_i=None
        out.append({
            "timestamp":ts,
            "url":url,
            "status":status_i,
            "mime":obj.get("mime") if isinstance(obj.get("mime"),str) else None,
            "digest":obj.get("digest") if isinstance(obj.get("digest"),str) else None,
            "length":obj.get("length") if isinstance(obj.get("length"),str) else None,
        })
    return out

def ts_dt(ts: str)->dt.datetime:
    return dt.datetime.strptime(ts,"%Y%m%d%H%M%S").replace(tzinfo=UTC)

def evaluate(row: dict[str,Any],target: str,lower: str,upper: str,accepted_status: int)->dict[str,Any]:
    when=ts_dt(row["timestamp"])
    lo=parse_z(lower); hi=parse_z(upper)
    identity=normalize_sky_identity(row["url"])==normalize_sky_identity(target)
    pit=lo <= when < hi
    status_ok=row.get("status")==accepted_status
    return {
        "timestamp":row["timestamp"],
        "capture_utc":when.isoformat().replace("+00:00","Z"),
        "url":row["url"],
        "status":row.get("status"),
        "mime":row.get("mime"),
        "digest":row.get("digest"),
        "length":row.get("length"),
        "exact_identity":bool(identity),
        "pit_time_ok":bool(pit),
        "status_ok":bool(status_ok),
        "witness_pass":bool(identity and pit and status_ok),
    }

def classify(pass_n: int,error_n: int,p: dict[str,Any])->tuple[str,str]:
    if pass_n>0:
        return p["decision_contract"]["positive_classification"],p["reasonable_subroutes"]["if_positive"]
    if error_n>0:
        return p["decision_contract"]["fail_classification"],p["reasonable_subroutes"]["if_external_error"]
    return p["decision_contract"]["fail_classification"],p["reasonable_subroutes"]["if_complete_zero"]

def run(registry: Path,out: Path,token: str)->dict[str,Any]:
    p=json.loads(registry.read_text(encoding="utf-8"))
    req(p["status"]=="DESIGN_LOCKED_ZERO_LABEL_METADATA_ONLY","STATUS")
    req(p["exact_base"]=="2ff541961c2b8e25145aaf0f0c0d1f707a7239b4","EXACT_BASE")
    pc=p["provider_contract_source"]
    req(pc["repository"]=="agntn/archives","PROVIDER_CONTRACT_REPO")
    req(pc["commit"]=="37d69db28665afe70d1bacf7e704343fe0cf1b6a","PROVIDER_CONTRACT_COMMIT")
    req(pc["git_blob_sha"]=="507dba38cb1cb0d1e8fd8d1ebcc7d9a0e4034dd0","PROVIDER_CONTRACT_BLOB")
    req(pc["endpoint"]=="https://webarchiv.onb.ac.at/web/cdx","PROVIDER_ENDPOINT")
    h=p["hard_rules"]
    req(h["previous_source_requery_allowed"] is False,"NO_OLD_SOURCE")
    req(h["wildcard_query_allowed"] is False,"NO_WILDCARD")
    req(h["replay_fetch_allowed"] is False,"NO_REPLAY")
    req(h["response_body_persisted"] is False,"NO_RAW_PERSIST")
    req(h["article_body_read"] is False and h["referee_assignment_body_parsed"] is False,"NO_BODY")
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

    src=p["source"]; qc=p["query_contract"]
    reports=[]; errors=[]; witnesses=[]
    for rnd in samples:
        prow=pit_rows[rnd]; srow=sky_rows[rnd]
        req(prow["binding_status"]=="FAIL",f"SAMPLE_ALREADY_PASS:R{rnd}")
        target=srow.get("sky_url")
        req(isinstance(target,str) and target.startswith("https://sport.sky.it/"),f"SKY_URL:R{rnd}")
        lower=prow["sky_visible_published_utc"]; upper=prow["first_fixture_cutoff_utc"]
        q=build_query(src["endpoint"],target,lower,upper,int(src["limit"]))
        report={
            "round":rnd,"target_url":target,"query_target":http_normalized_target(target),
            "lower_utc":lower,"upper_utc":upper,"query_url":q,
            "status":"UNSET","http_status":None,"cdxj_row_n":0,"witness_pass_n":0,
            "response_persisted":False,"replay_body_read":False,
        }
        try:
            status,raw,final,headers=fetch(q,src)
            report.update({"http_status":status,"final_url":final,"content_type":headers.get("content-type")})
            if status==404:
                report.update({"status":"CLEAN_ZERO_404","response_bytes":0,"response_sha256":sha256_bytes(b"")})
            elif 200 <= status < 300:
                rows=parse_cdxj(raw)
                evaluated=[evaluate(x,target,lower,upper,int(qc["accepted_status"])) for x in rows]
                passed=[x for x in evaluated if x["witness_pass"]]
                report.update({
                    "status":"SUCCESS","response_bytes":len(raw),"response_sha256":sha256_bytes(raw),
                    "cdxj_row_n":len(rows),"evaluated_rows":evaluated,
                    "witness_pass_n":len(passed),"witness_rows":passed,
                })
                for x in passed:
                    witnesses.append({"round":rnd,**x})
            else:
                report["status"]="HTTP_ERROR"; report["error"]=f"HTTP_{status}"
                errors.append({"round":rnd,"error":report["error"]})
        except Exception as exc:
            report["status"]="EXTERNAL_OR_CONTRACT_ERROR"
            report["error"]=f"{type(exc).__name__}:{exc}"[:800]
            errors.append({"round":rnd,"error":report["error"]})
        reports.append(report)

    pass_rounds=sorted({int(x["round"]) for x in witnesses})
    classification,next_step=classify(len(witnesses),len(errors),p)
    matrix={
        "schema_version":"football3-nova-n10-referee-sky-webarchiv-at-feasibility-matrix-v1",
        "sample_rounds":samples,"error_n":len(errors),"errors":errors,
        "witness_pass_n":len(witnesses),"witness_pass_rounds":pass_rounds,
        "reports":reports,"response_body_persisted":False,"replay_body_read":False,
    }
    out.mkdir(parents=True,exist_ok=True)
    mb=(json.dumps(matrix,indent=2,sort_keys=True,ensure_ascii=False)+"\n").encode("utf-8")
    (out/"sky_webarchiv_at_feasibility_matrix.json").write_bytes(mb)
    receipt={
        "schema_version":"football3-nova-n10-referee-sky-webarchiv-at-feasibility-receipt-v1",
        "status":"N10_REFEREE_SKY_WEBARCHIV_AT_FEASIBILITY_COMPLETE",
        "classification":classification,"exact_base":p["exact_base"],
        "registry_sha256":sha256_bytes(registry.read_bytes()),
        "provider_contract_commit":pc["commit"],"provider_contract_blob_sha":pc["git_blob_sha"],
        "parent_provenance":parent_prov,"sample_rounds":samples,
        "error_n":len(errors),"errors":errors,"witness_pass_n":len(witnesses),
        "witness_pass_rounds":pass_rounds,"matrix_sha256":sha256_bytes(mb),
        "http_normalized_query_target":True,"wildcard_query_used":False,
        "response_body_persisted":False,"replay_fetch_performed":False,"replay_body_read":False,
        "article_body_read":False,"referee_assignment_body_parsed":False,
        "match_result_payload_read":False,"standings_payload_read":False,"player_stats_payload_read":False,
        "result_labels_read":0,"score_values_read":0,"training_performed":False,"scoring_performed":False,
        "formal_v2_changed":False,"current_changed":False,"production_changed":False,
        "candidate_weight":0,"matrix_delta":0,"formal_available_at_proven":False,
        "fixture_level_binding_complete":False,"referee_oof_allowed":False,"next_step":next_step,
    }
    (out/"sky_webarchiv_at_feasibility_receipt.json").write_text(
        json.dumps(receipt,indent=2,sort_keys=True,ensure_ascii=False)+"\n",encoding="utf-8"
    )
    print(json.dumps(receipt,sort_keys=True,ensure_ascii=False))
    return receipt

def main()->None:
    a=argparse.ArgumentParser()
    a.add_argument("--registry",type=Path,required=True)
    a.add_argument("--out",type=Path,required=True)
    x=a.parse_args()
    run(x.registry,x.out,os.environ.get("GITHUB_TOKEN",""))

if __name__=="__main__":
    main()
