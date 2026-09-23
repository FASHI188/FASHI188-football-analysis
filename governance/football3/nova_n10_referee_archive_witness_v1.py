#!/usr/bin/env python3
from __future__ import annotations
import argparse, datetime as dt, hashlib, json, ssl, urllib.parse, urllib.request
from pathlib import Path
from typing import Any

class WitnessError(RuntimeError): pass

def req(c: bool, m: str) -> None:
    if not c: raise WitnessError(m)

def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def parse_cutoff(v: str) -> dt.datetime:
    x=dt.datetime.fromisoformat(v.replace("Z","+00:00"))
    req(x.tzinfo is not None,"CUTOFF_TZ")
    return x.astimezone(dt.timezone.utc)

def parse_capture(v: str) -> dt.datetime:
    return dt.datetime.strptime(v,"%Y%m%d%H%M%S").replace(tzinfo=dt.timezone.utc)

def safe_capture(timestamp: str, cutoff: str) -> bool:
    return parse_capture(timestamp) < parse_cutoff(cutoff)

def cdx_to_value(cutoff: str) -> str:
    return (parse_cutoff(cutoff)-dt.timedelta(seconds=1)).strftime("%Y%m%d%H%M%S")

def official_domain_ok(url: str, suffix: str) -> bool:
    h=(urllib.parse.urlparse(url).hostname or "").lower()
    s=suffix.lower()
    return h==s or h.endswith("."+s)

def archive_host_ok(url: str, allowed: list[str]) -> bool:
    h=(urllib.parse.urlparse(url).hostname or "").lower()
    return h in {x.lower() for x in allowed}

def fetch(url: str, timeout: int=25, limit: int=2_000_000) -> tuple[bytes,str,dict[str,str]]:
    rq=urllib.request.Request(url,headers={
        "User-Agent":"Football3-Nova-N10-ArchiveWitness/1.1",
        "Accept":"application/json,text/plain,text/html,*/*",
    })
    with urllib.request.urlopen(rq,timeout=timeout,context=ssl.create_default_context()) as r:
        data=r.read(limit+1)
        if len(data)>limit: raise WitnessError("RESPONSE_TOO_LARGE")
        return data,r.geturl(),{k.lower():v for k,v in r.headers.items()}

def build_cdx_url(endpoint: str, original: str, cutoff: str) -> str:
    q=[
        ("url",original),("output","json"),
        ("fl","timestamp,original,statuscode,digest,mimetype"),
        ("filter","statuscode:200"),("to",cdx_to_value(cutoff)),
        ("collapse","digest"),("limit","50"),
    ]
    return endpoint+"?"+urllib.parse.urlencode(q)

def build_arquivo_url(endpoint: str, original: str, cutoff: str) -> str:
    q=[
        ("url",original),("output","json"),
        ("fl","url,timestamp,status,digest,mime"),
        ("filter","status:200"),("to",cdx_to_value(cutoff)),("limit","50"),
    ]
    return endpoint+"?"+urllib.parse.urlencode(q)

def _normalize_record(r: dict[str,Any]) -> dict[str,str]:
    return {
        "timestamp":str(r.get("timestamp","")),
        "original":str(r.get("original") or r.get("url") or ""),
        "statuscode":str(r.get("statuscode") or r.get("status") or ""),
        "digest":str(r.get("digest","")),
        "mimetype":str(r.get("mimetype") or r.get("mime") or ""),
    }

def parse_cdx(raw: bytes) -> list[dict[str,str]]:
    x=json.loads(raw.decode("utf-8"))
    if not x: return []
    req(isinstance(x,list) and isinstance(x[0],list),"CDX_SHAPE")
    header=x[0]
    out=[]
    for row in x[1:]:
        if not isinstance(row,list) or len(row)!=len(header): continue
        out.append(_normalize_record({str(k):v for k,v in zip(header,row)}))
    return out

def parse_arquivo_cdx(raw: bytes) -> list[dict[str,str]]:
    text=raw.decode("utf-8").strip()
    if not text: return []
    try:
        x=json.loads(text)
        if isinstance(x,list):
            if x and isinstance(x[0],list):
                header=x[0]
                return [_normalize_record({str(k):v for k,v in zip(header,row)}) for row in x[1:] if isinstance(row,list) and len(row)==len(header)]
            return [_normalize_record(r) for r in x if isinstance(r,dict)]
        if isinstance(x,dict):
            rows=x.get("results") or x.get("items")
            if isinstance(rows,list):
                return [_normalize_record(r) for r in rows if isinstance(r,dict)]
            return [_normalize_record(x)]
    except json.JSONDecodeError:
        pass
    out=[]
    for line in text.splitlines():
        line=line.strip()
        if not line: continue
        try:
            r=json.loads(line)
            if isinstance(r,dict): out.append(_normalize_record(r))
        except json.JSONDecodeError:
            continue
    return out

def eligible(rows: list[dict[str,str]], sample: dict[str,Any]) -> list[dict[str,str]]:
    out=[]
    for r in rows:
        ts=r.get("timestamp","")
        orig=r.get("original","")
        if len(ts)!=14 or not ts.isdigit(): continue
        if r.get("statuscode")!="200": continue
        if not official_domain_ok(orig,sample["official_domain"]): continue
        if not safe_capture(ts,sample["safe_cutoff_utc"]): continue
        out.append(r)
    return sorted(out,key=lambda r:r["timestamp"])

def snapshot_url(prefix: str, timestamp: str, original: str) -> str:
    return f"{prefix}{timestamp}id_/{original}"

def _query_wayback(reg: dict[str,Any], sample: dict[str,Any], timeout: int) -> tuple[list[dict[str,str]],list[dict[str,Any]],list[dict[str,Any]]]:
    endpoint=reg["archive"]["cdx_endpoint"]
    candidates=[]; receipts=[]; errors=[]
    for variant in sample["url_variants"]:
        u=build_cdx_url(endpoint,variant,sample["safe_cutoff_utc"])
        try:
            raw,final,headers=fetch(u,timeout,1_000_000)
            req(archive_host_ok(final,reg["archive"]["allowed_archive_hosts"]),"CDX_REDIRECT_OUTSIDE_ARCHIVE")
            rows=parse_cdx(raw); good=eligible(rows,sample)
            for r in good: r["_provider"]="Internet Archive Wayback CDX"
            candidates.extend(good)
            receipts.append({"provider":"Internet Archive Wayback CDX","query_url":u,"final_url":final,
                             "response_sha256":sha256_bytes(raw),"row_n":len(rows),"eligible_n":len(good),
                             "content_type":headers.get("content-type")})
        except Exception as e:
            errors.append({"provider":"Internet Archive Wayback CDX","variant":variant,"stage":"cdx",
                           "error":f"{type(e).__name__}:{e}"[:400]})
    return candidates,receipts,errors

def _query_arquivo(reg: dict[str,Any], sample: dict[str,Any], timeout: int) -> tuple[list[dict[str,str]],list[dict[str,Any]],list[dict[str,Any]]]:
    cfg=reg["archive"]["fallbacks"][0]
    candidates=[]; receipts=[]; errors=[]
    for variant in sample["url_variants"]:
        u=build_arquivo_url(cfg["cdx_endpoint"],variant,sample["safe_cutoff_utc"])
        try:
            raw,final,headers=fetch(u,timeout,1_000_000)
            req(archive_host_ok(final,cfg["allowed_archive_hosts"]),"ARQUIVO_REDIRECT_OUTSIDE_ARCHIVE")
            rows=parse_arquivo_cdx(raw); good=eligible(rows,sample)
            for r in good: r["_provider"]=cfg["provider"]
            candidates.extend(good)
            receipts.append({"provider":cfg["provider"],"query_url":u,"final_url":final,
                             "response_sha256":sha256_bytes(raw),"row_n":len(rows),"eligible_n":len(good),
                             "content_type":headers.get("content-type")})
        except Exception as e:
            errors.append({"provider":cfg["provider"],"variant":variant,"stage":"cdx",
                           "error":f"{type(e).__name__}:{e}"[:400]})
    return candidates,receipts,errors

def audit_sample(reg: dict[str,Any], sample: dict[str,Any], timeout: int) -> dict[str,Any]:
    candidates,receipts,errors=_query_wayback(reg,sample,timeout)
    providers_attempted=["Internet Archive Wayback CDX"]
    if not candidates:
        c2,r2,e2=_query_arquivo(reg,sample,timeout)
        candidates.extend(c2); receipts.extend(r2); errors.extend(e2)
        providers_attempted.append(reg["archive"]["fallbacks"][0]["provider"])

    uniq={}
    for r in candidates:
        uniq[(r.get("timestamp"),r.get("original"),r.get("digest"),r.get("_provider"))]=r
    candidates=sorted(uniq.values(),key=lambda r:r["timestamp"])
    selected=candidates[0] if candidates else None
    snapshot_receipt=None
    if selected:
        req(safe_capture(selected["timestamp"],sample["safe_cutoff_utc"]),"UNSAFE_SELECTED_CAPTURE")
        if selected.get("_provider")=="Internet Archive Wayback CDX":
            su=snapshot_url(reg["archive"]["snapshot_prefix"],selected["timestamp"],selected["original"])
            try:
                raw,final,headers=fetch(su,timeout,2_000_000)
                req(archive_host_ok(final,reg["archive"]["allowed_archive_hosts"]),"SNAPSHOT_REDIRECT_OUTSIDE_ARCHIVE")
                snapshot_receipt={
                    "provider":selected["_provider"],"requested_url":su,"final_url":final,
                    "bytes_hashed":len(raw),"content_sha256":sha256_bytes(raw),"content_parsed":False,
                    "capture_timestamp":selected["timestamp"],"capture_before_safe_cutoff":True,
                    "content_type":headers.get("content-type"),
                }
            except Exception as e:
                snapshot_receipt={
                    "provider":selected["_provider"],"requested_url":su,"fetch_status":"UNAVAILABLE",
                    "error":f"{type(e).__name__}:{e}"[:400],"content_parsed":False,
                    "capture_timestamp":selected["timestamp"],"capture_before_safe_cutoff":True,
                }
        else:
            snapshot_receipt={
                "provider":selected["_provider"],"fetch_status":"NOT_ATTEMPTED_METADATA_WITNESS_ONLY",
                "content_parsed":False,"capture_timestamp":selected["timestamp"],
                "capture_before_safe_cutoff":True,
            }

    return {
        "competition":sample["competition"],"source_id":sample["source_id"],
        "official_url":sample["official_url"],"safe_cutoff_utc":sample["safe_cutoff_utc"],
        "cutoff_basis":sample["cutoff_basis"],"providers_attempted":providers_attempted,
        "archive_query_receipts":receipts,"external_errors":errors,
        "eligible_capture_n":len(candidates),"selected_capture":selected,
        "safe_capture_found":selected is not None,"snapshot_receipt":snapshot_receipt,
        "result_labels_read":0,"score_values_read":0,
    }

def run(registry: Path, out: Path, timeout: int=25) -> dict[str,Any]:
    p=json.loads(registry.read_text(encoding="utf-8"))
    req(p["status"]=="DESIGN_LOCKED_ZERO_LABEL","STATUS")
    req(p["exact_base"]=="5cd614134246c8d6c756e6fd170986b700d1d09c","EXACT_BASE")
    h=p["hard_rules"]
    req(h["result_labels_read"] is False and h["score_values_read"] is False,"ZERO_LABEL")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False,"NO_MODEL")
    req(h["paid_or_secret_source_allowed"] is False,"NO_PAID_SECRET")
    req(h["archive_snapshot_fetch_requires_capture_before_safe_cutoff"] is True,"PRE_CUTOFF_GATE")
    req(h["capture_at_or_after_safe_cutoff_forbidden"] is True,"POST_CUTOFF_FORBIDDEN")
    req(h["live_official_article_body_fetch_forbidden"] is True,"NO_LIVE_BODY")
    req(h["candidate_weight"]==0 and h["matrix_delta"]==0,"ZERO_WEIGHT")
    req(len(p["samples"])==3,"SAMPLE_N")
    req(p["success_contract"]["reasonable_subroutes"]==["WAYBACK_CDX","ARQUIVO_PT_CDX"],"SUBROUTES")

    out.mkdir(parents=True,exist_ok=True)
    reports=[audit_sample(p,s,timeout) for s in p["samples"]]
    pass_n=sum(1 for r in reports if r["safe_capture_found"])
    all_pass=pass_n==len(reports)
    classification="DATA_COVERAGE_FEASIBILITY_PASS" if all_pass else "STOP_DATA_COVERAGE"
    receipt={
        "schema_version":"football3-nova-n10-referee-archive-witness-receipt-v1.1",
        "status":"N10_REFEREE_ARCHIVE_WITNESS_AUDIT_COMPLETE","classification":classification,
        "exact_base":p["exact_base"],"registry_sha256":sha256_bytes(registry.read_bytes()),
        "archive_providers":["Internet Archive Wayback CDX",p["archive"]["fallbacks"][0]["provider"]],
        "sample_n":len(reports),"safe_capture_pass_n":pass_n,"safe_capture_all_samples":all_pass,
        "reports":reports,"full_big5_data_ready":False,"referee_oof_allowed":False,
        "result_labels_read":0,"score_values_read":0,"training_performed":False,"scoring_performed":False,
        "formal_v2_changed":False,"current_changed":False,"production_changed":False,
        "candidate_weight":0,"matrix_delta":0,
        "next_step":(
            "EXPAND_ARCHIVE_WITNESS_TO_TARGET_SEASON_OFFICIAL_APPOINTMENT_URLS_AND_BUILD_FIXTURE_BINDING"
            if all_pass else
            "STOP_DATA_COVERAGE_AFTER_WAYBACK_AND_ARQUIVO_PT; CONTINUE_ONLY_WITH_NEW_LEGAL_FREE_ARCHIVE_SOURCE; DO_NOT_START_REFEREE_OOF"
        ),
    }
    (out/"archive_witness_receipt.json").write_text(json.dumps(receipt,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(receipt,sort_keys=True))
    return receipt

def main() -> None:
    a=argparse.ArgumentParser()
    a.add_argument("--registry",type=Path,required=True)
    a.add_argument("--out",type=Path,required=True)
    a.add_argument("--timeout",type=int,default=25)
    x=a.parse_args(); run(x.registry,x.out,x.timeout)

if __name__=="__main__": main()
