#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import html
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from nova_n10_referee_sky_arquivo_feasibility_v1 import acquire_parents
from nova_n10_referee_sky_combined_freeze_v1 import sha256_bytes
from nova_n10_referee_sky_pit_binding_v1 import normalize_sky_identity

UTC=dt.timezone.utc

class ArchiveTodayError(RuntimeError):
    pass

def req(cond: bool, msg: str) -> None:
    if not cond:
        raise ArchiveTodayError(msg)

def parse_z(v: str) -> dt.datetime:
    x=dt.datetime.fromisoformat(v.replace("Z","+00:00"))
    if x.tzinfo is None:
        x=x.replace(tzinfo=UTC)
    return x.astimezone(UTC)

def host(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()

def allowed_host(url: str, allowed: list[str]) -> bool:
    return host(url) in {x.lower() for x in allowed}

def encode_original(url: str) -> str:
    return urllib.parse.quote(url,safe=":/")

def build_surface(template: str, original: str) -> str:
    return template.replace("{original_url}",encode_original(original))

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def nofollow_request(
    url: str,
    *,
    source: dict[str,Any],
    headers: dict[str,str] | None=None,
    read_body: bool,
    max_bytes: int,
) -> dict[str,Any]:
    req(allowed_host(url,source["allowed_hosts"]),"REQUEST_HOST")
    hdr={"User-Agent":source["user_agent"],"Accept":"text/html,application/xhtml+xml,*/*;q=0.1"}
    if headers:
        hdr.update(headers)
    request=urllib.request.Request(url,headers=hdr,method="GET")
    opener=urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        NoRedirect(),
    )
    try:
        response=opener.open(request,timeout=int(source["request_timeout_seconds"]))
        try:
            final=response.geturl()
            req(allowed_host(final,source["allowed_hosts"]),"FINAL_HOST")
            raw=b""
            if read_body:
                raw=response.read(max_bytes+1)
                req(len(raw)<=max_bytes,"RESPONSE_TOO_LARGE")
            return {
                "status":int(getattr(response,"status",200)),
                "final_url":final,
                "headers":{k.lower():v for k,v in response.headers.items()},
                "body":raw,
                "redirected":False,
            }
        finally:
            response.close()
    except urllib.error.HTTPError as exc:
        h={k.lower():v for k,v in exc.headers.items()}
        if 300 <= int(exc.code) < 400:
            location=h.get("location")
            if location:
                absolute=urllib.parse.urljoin(url,location)
                req(allowed_host(absolute,source["allowed_hosts"]),"REDIRECT_HOST")
                location=absolute
            return {
                "status":int(exc.code),
                "final_url":url,
                "headers":h,
                "body":b"",
                "redirected":True,
                "location":location,
            }
        preview=exc.read(512).decode("utf-8","replace")
        raise ArchiveTodayError(f"HTTP_{exc.code}:{preview}") from exc

class HrefCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs:list[str]=[]
    def handle_starttag(self, tag: str, attrs: list[tuple[str,str|None]]) -> None:
        if tag.lower()!="a":
            return
        for k,v in attrs:
            if k.lower()=="href" and isinstance(v,str):
                self.hrefs.append(v)

def repair_original(v: str) -> str:
    s=html.unescape(urllib.parse.unquote(v)).strip()
    s=re.sub(r"^(https?):/([^/])",r"\1://\2",s,flags=re.I)
    return s

def parse_stamp(stamp: str) -> dt.datetime | None:
    for fmt in ("%Y.%m.%d-%H%M%S","%Y%m%d%H%M%S"):
        try:
            return dt.datetime.strptime(stamp,fmt).replace(tzinfo=UTC)
        except ValueError:
            pass
    return None

def parse_capture_url(archive_url: str, target: str, allowed_hosts: list[str]) -> dict[str,Any] | None:
    try:
        p=urllib.parse.urlparse(archive_url)
    except Exception:
        return None
    if (p.hostname or "").lower() not in {x.lower() for x in allowed_hosts}:
        return None
    parts=p.path.lstrip("/").split("/",1)
    if len(parts)!=2:
        return None
    when=parse_stamp(parts[0])
    if when is None:
        return None
    original=repair_original(parts[1])
    if normalize_sky_identity(original)!=normalize_sky_identity(target):
        return None
    return {
        "capture_utc":when.isoformat().replace("+00:00","Z"),
        "archive_url":archive_url,
        "original_url":target,
        "surface":"index",
    }

def captures_from_index(raw: bytes, base_url: str, target: str, allowed_hosts: list[str]) -> list[dict[str,Any]]:
    text=raw.decode("utf-8","replace")
    lowered=text.casefold()
    req("captcha" not in lowered and "verify you are human" not in lowered and "cf-chl-" not in lowered,"ANTI_BOT_PAGE")
    parser=HrefCollector()
    parser.feed(text)
    found={}
    for href in parser.hrefs:
        absolute=urllib.parse.urljoin(base_url,html.unescape(href))
        cap=parse_capture_url(absolute,target,allowed_hosts)
        if cap:
            found[cap["archive_url"]]=cap
    return sorted(found.values(),key=lambda x:(x["capture_utc"],x["archive_url"]))

def parse_http_datetime(v: str | None) -> dt.datetime | None:
    if not v:
        return None
    try:
        x=email.utils.parsedate_to_datetime(v)
        if x.tzinfo is None:
            x=x.replace(tzinfo=UTC)
        return x.astimezone(UTC)
    except Exception:
        return None

def original_from_link_header(v: str | None) -> str | None:
    if not v:
        return None
    for m in re.finditer(r"<([^>]+)>\s*;\s*([^,]+)",v):
        uri=m.group(1)
        params=m.group(2).casefold()
        if re.search(r'rel\s*=\s*"?original"?',params):
            return uri
    return None

def timegate_capture(
    response: dict[str,Any],
    *,
    target: str,
    allowed_hosts: list[str],
) -> dict[str,Any] | None:
    h=response["headers"]
    when=parse_http_datetime(h.get("memento-datetime"))
    location=response.get("location") or h.get("content-location")
    original=original_from_link_header(h.get("link"))
    if when is None or not isinstance(location,str) or not allowed_host(location,allowed_hosts):
        return None
    if not isinstance(original,str) or normalize_sky_identity(original)!=normalize_sky_identity(target):
        return None
    return {
        "capture_utc":when.isoformat().replace("+00:00","Z"),
        "archive_url":location,
        "original_url":target,
        "surface":"timegate",
    }

def pit_filter(captures: list[dict[str,Any]], lower: dt.datetime, upper: dt.datetime) -> list[dict[str,Any]]:
    out=[]
    for cap in captures:
        when=parse_z(cap["capture_utc"])
        if lower <= when < upper:
            out.append(cap)
    return sorted(out,key=lambda x:(x["capture_utc"],x["archive_url"]))

def audit_round(rnd: int, prow: dict[str,Any], target: str, p: dict[str,Any]) -> dict[str,Any]:
    source=p["source"]
    lower=parse_z(prow["sky_visible_published_utc"])-dt.timedelta(seconds=300)
    upper=parse_z(prow["first_fixture_cutoff_utc"])
    req(lower < upper,"PIT_WINDOW")
    history_url=build_surface(source["exact_history_template"],target)
    report={
        "round":rnd,
        "target_url":target,
        "lower_utc":lower.isoformat().replace("+00:00","Z"),
        "upper_utc":upper.isoformat().replace("+00:00","Z"),
        "history_url":history_url,
        "index_status":None,
        "index_capture_n":0,
        "timegate_attempted":False,
        "all_capture_n":0,
        "pit_capture_n":0,
        "pit_captures":[],
        "error":None,
    }
    captures=[]
    try:
        idx=nofollow_request(
            history_url,
            source=source,
            read_body=True,
            max_bytes=int(source["max_index_bytes"]),
        )
        report["index_status"]=idx["status"]
        report["index_response_sha256"]=sha256_bytes(idx["body"]) if idx["body"] else None
        report["index_response_bytes"]=len(idx["body"])
        report["index_redirect_location"]=idx.get("location")
        if idx["status"]==200:
            caps=captures_from_index(idx["body"],history_url,target,source["allowed_hosts"])
            report["index_capture_n"]=len(caps)
            captures.extend(caps)
        elif 300 <= idx["status"] < 400:
            loc=idx.get("location")
            if isinstance(loc,str):
                cap=parse_capture_url(loc,target,source["allowed_hosts"])
                if cap:
                    cap["surface"]="index_redirect"
                    captures.append(cap)
        else:
            raise ArchiveTodayError(f"INDEX_STATUS:{idx['status']}")

        # One bounded no-follow metadata probe. It never retrieves a snapshot body.
        timegate_url=build_surface(source["timegate_template"],target)
        desired=upper-dt.timedelta(seconds=1)
        report["timegate_attempted"]=True
        report["timegate_url"]=timegate_url
        tg=nofollow_request(
            timegate_url,
            source=source,
            headers={"Accept-Datetime":email.utils.format_datetime(desired,usegmt=True)},
            read_body=False,
            max_bytes=0,
        )
        report["timegate_status"]=tg["status"]
        report["timegate_redirect_location"]=tg.get("location")
        report["timegate_memento_datetime"]=tg["headers"].get("memento-datetime")
        report["timegate_link_sha256"]=sha256_bytes((tg["headers"].get("link") or "").encode())
        tcap=timegate_capture(tg,target=target,allowed_hosts=source["allowed_hosts"])
        if tcap:
            captures.append(tcap)

        dedup={}
        for cap in captures:
            dedup[(cap["capture_utc"],cap["archive_url"])]=cap
        all_caps=sorted(dedup.values(),key=lambda x:(x["capture_utc"],x["archive_url"]))
        pit=pit_filter(all_caps,lower,upper)
        report["all_capture_n"]=len(all_caps)
        report["all_captures"]=all_caps
        report["pit_capture_n"]=len(pit)
        report["pit_captures"]=pit
        report["status"]="ELIGIBLE_CAPTURE" if pit else "ZERO_ELIGIBLE_CAPTURE"
    except Exception as exc:
        report["status"]="EXTERNAL_OR_CONTRACT_ERROR"
        report["error"]=f"{type(exc).__name__}:{exc}"[:800]
    return report

def classify(reports: list[dict[str,Any]], p: dict[str,Any]) -> tuple[str,str]:
    passes=sum(int(x["pit_capture_n"]) for x in reports)
    errors=sum(1 for x in reports if x.get("error"))
    if passes:
        return p["decision_contract"]["positive_classification"],p["reasonable_subroutes"]["if_positive"]
    if errors:
        return p["decision_contract"]["fail_classification"],p["reasonable_subroutes"]["if_external_or_contract_error"]
    return p["decision_contract"]["fail_classification"],p["reasonable_subroutes"]["if_complete_zero"]

def run(registry_path: Path, out: Path, token: str) -> dict[str,Any]:
    p=json.loads(registry_path.read_text(encoding="utf-8"))
    req(p["status"]=="DESIGN_LOCKED_ZERO_LABEL_METADATA_ONLY","STATUS")
    req(p["exact_base"]=="32ced8e95ae0c1a52581921c5bfd10899eb06e88","EXACT_BASE")
    h=p["hard_rules"]
    req(h["previous_source_requery_allowed"] is False,"NO_OLD_SOURCE_REQUERY")
    req(h["snapshot_body_read"] is False and h["snapshot_creation_allowed"] is False,"NO_SNAPSHOT")
    req(h["index_body_persisted"] is False,"NO_INDEX_BODY_PERSIST")
    req(h["result_labels_read"] is False and h["score_values_read"] is False,"ZERO_LABEL")
    req(h["score_or_result_semantic_parse"] is False,"NO_RESULT_PARSE")
    req(h["referee_assignment_body_parsed"] is False,"NO_ASSIGNMENT_BODY")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False,"NO_MODEL")
    req(h["paid_or_secret_source_allowed"] is False,"NO_PAID_SECRET")
    req(h["candidate_weight"]==0 and h["matrix_delta"]==0,"ZERO_WEIGHT")
    req(p["source"]["follow_snapshot_redirects"] is False,"NO_REDIRECT_REPLAY")
    req(p["metadata_contract"]["timegate_requires_original_link_exact"] is True,"TIMEGATE_IDENTITY")

    pit,sky,parent_prov=acquire_parents(p,token)
    pit_rows={int(x["round"]):x for x in pit["rows"]}
    sky_rows={int(x["round"]):x for x in sky["rows"]}
    samples=[int(x["round"]) for x in p["samples"]]
    req(samples==[8,24,34],"FROZEN_SAMPLES")

    reports=[]
    for rnd in samples:
        prow=pit_rows[rnd]
        srow=sky_rows[rnd]
        req(prow["binding_status"]=="FAIL",f"SAMPLE_ALREADY_PASS:R{rnd}")
        target=srow.get("sky_url")
        req(isinstance(target,str) and target.startswith("https://sport.sky.it/"),f"SKY_URL:R{rnd}")
        reports.append(audit_round(rnd,prow,target,p))

    errors=[{"round":x["round"],"error":x["error"]} for x in reports if x.get("error")]
    positive_rounds=sorted(x["round"] for x in reports if x["pit_capture_n"]>0)
    pit_capture_n=sum(x["pit_capture_n"] for x in reports)
    classification,next_step=classify(reports,p)

    matrix={
        "schema_version":"football3-nova-n10-referee-sky-archivetoday-feasibility-matrix-v1",
        "sample_n":len(samples),
        "sample_rounds":samples,
        "error_n":len(errors),
        "errors":errors,
        "positive_round_n":len(positive_rounds),
        "positive_rounds":positive_rounds,
        "pit_capture_n":pit_capture_n,
        "reports":reports,
        "snapshot_body_read":False,
        "snapshot_creation_performed":False,
        "index_body_persisted":False,
    }
    out.mkdir(parents=True,exist_ok=True)
    matrix_bytes=(json.dumps(matrix,indent=2,sort_keys=True,ensure_ascii=False)+"\n").encode("utf-8")
    (out/"sky_archivetoday_feasibility_matrix.json").write_bytes(matrix_bytes)
    receipt={
        "schema_version":"football3-nova-n10-referee-sky-archivetoday-feasibility-receipt-v1",
        "status":"N10_REFEREE_SKY_ARCHIVETODAY_FEASIBILITY_COMPLETE",
        "classification":classification,
        "exact_base":p["exact_base"],
        "registry_sha256":sha256_bytes(registry_path.read_bytes()),
        "parent_provenance":parent_prov,
        "sample_n":len(samples),
        "sample_rounds":samples,
        "error_n":len(errors),
        "errors":errors,
        "positive_round_n":len(positive_rounds),
        "positive_rounds":positive_rounds,
        "pit_capture_n":pit_capture_n,
        "matrix_sha256":sha256_bytes(matrix_bytes),
        "snapshot_body_read":False,
        "snapshot_creation_performed":False,
        "index_body_persisted":False,
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
    (out/"sky_archivetoday_feasibility_receipt.json").write_text(
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
