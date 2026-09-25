#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import hashlib
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
from nova_n10_referee_sky_pit_binding_v1 import normalize_sky_identity

UTC=dt.timezone.utc
JST=dt.timezone(dt.timedelta(hours=9))

class SkyMegalodonError(RuntimeError):
    pass

def req(c: bool,m: str)->None:
    if not c:
        raise SkyMegalodonError(m)

def sha256_bytes(b: bytes)->str:
    return hashlib.sha256(b).hexdigest()

def parse_z(v: str)->dt.datetime:
    x=dt.datetime.fromisoformat(v.replace("Z","+00:00"))
    if x.tzinfo is None:
        x=x.replace(tzinfo=UTC)
    return x.astimezone(UTC)

def host(url: str)->str:
    return (urllib.parse.urlparse(url).hostname or "").lower()

def allowed_host(url: str, hosts: list[str])->bool:
    return host(url) in {x.lower() for x in hosts}

def build_lookup_url(base: str,target: str)->str:
    return base+"?"+urllib.parse.urlencode({"url":target})

def fetch_lookup(url: str,src: dict[str,Any])->tuple[int,bytes,str,dict[str,str]]:
    req(allowed_host(url,src["allowed_hosts"]),"REQUEST_HOST")
    rq=urllib.request.Request(url,headers={
        "User-Agent":src["user_agent"],
        "Accept":"text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
    })
    lim=int(src["max_response_bytes"])
    try:
        with urllib.request.urlopen(
            rq,timeout=int(src["request_timeout_seconds"]),
            context=ssl.create_default_context(),
        ) as r:
            final=r.geturl()
            req(allowed_host(final,src["allowed_hosts"]),"REDIRECT_HOST")
            raw=r.read(lim+1)
            req(len(raw)<=lim,"RESPONSE_TOO_LARGE")
            return int(getattr(r,"status",200)),raw,final,{k.lower():v for k,v in r.headers.items()}
    except urllib.error.HTTPError as e:
        final=e.geturl()
        req(allowed_host(final,src["allowed_hosts"]),"HTTP_ERROR_HOST")
        return int(e.code),b"",final,{k.lower():v for k,v in e.headers.items()}

def anti_bot(raw: bytes)->str|None:
    s=raw.decode("utf-8","replace").casefold()
    for marker in ["captcha","verify you are human","access denied","checking your browser","cf-chl-"]:
        if marker in s:
            return marker
    return None

_ARCHIVE_PATH_RE=re.compile(
    r"^/(?P<year>20\d{2})-(?P<md>\d{4})-(?P<hm>\d{4})-(?P<sec>\d{2})/(?P<original>https?://.+)$",
    re.I,
)

def parse_archive_href(href: str)->dict[str,str]|None:
    p=urllib.parse.urlparse(href)
    if p.hostname not in {"megalodon.jp","www.megalodon.jp"}:
        return None
    m=_ARCHIVE_PATH_RE.match(urllib.parse.unquote(p.path))
    if not m:
        return None
    stamp=f"{m.group('year')}-{m.group('md')[:2]}-{m.group('md')[2:]} {m.group('hm')[:2]}:{m.group('hm')[2:]}:{m.group('sec')}"
    return {
        "archive_href":href,
        "path_timestamp_local_unqualified":stamp,
        "embedded_original":m.group("original"),
    }

class AnchorParser(HTMLParser):
    def __init__(self,base_url: str)->None:
        super().__init__(convert_charrefs=True)
        self.base_url=base_url
        self.current: dict[str,Any]|None=None
        self.anchors: list[dict[str,str]]=[]
    def handle_starttag(self,tag: str,attrs: list[tuple[str,str|None]])->None:
        if tag.lower()!="a":
            return
        d={str(k).lower():(v or "") for k,v in attrs}
        href=d.get("href","").strip()
        if not href:
            return
        self.current={
            "href":urllib.parse.urljoin(self.base_url,href),
            "parts":[],
            "title":d.get("title",""),
            "aria":d.get("aria-label",""),
        }
    def handle_data(self,data: str)->None:
        if self.current is not None:
            self.current["parts"].append(data)
    def handle_endtag(self,tag: str)->None:
        if tag.lower()=="a" and self.current is not None:
            txt=" ".join([
                self.current["title"],self.current["aria"]," ".join(self.current["parts"])
            ])
            txt=re.sub(r"\s+"," ",html.unescape(txt)).strip()
            self.anchors.append({"href":self.current["href"],"text":txt})
            self.current=None

def parse_anchors(raw: bytes,final_url: str)->list[dict[str,str]]:
    p=AnchorParser(final_url)
    p.feed(raw.decode("utf-8","replace"))
    return p.anchors

_JA_JST_RE=re.compile(
    r"(?P<y>20\d{2})年(?P<m>\d{1,2})月(?P<d>\d{1,2})日\s*"
    r"(?P<h>\d{1,2}):(?P<mi>\d{2})(?::(?P<s>\d{2}))?\s*(?P<z>JST|UTC|GMT)",
    re.I,
)
_ISO_ZONE_RE=re.compile(
    r"(?P<x>20\d{2}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?\s*(?:Z|JST|UTC|GMT|[+-]\d{2}:?\d{2}))",
    re.I,
)

def parse_visible_timestamp(text: str)->list[dict[str,str]]:
    out=[]
    seen=set()
    for m in _JA_JST_RE.finditer(text):
        sec=int(m.group("s") or 0)
        zone=m.group("z").upper()
        tz=JST if zone=="JST" else UTC
        x=dt.datetime(
            int(m.group("y")),int(m.group("m")),int(m.group("d")),
            int(m.group("h")),int(m.group("mi")),sec,tzinfo=tz
        ).astimezone(UTC)
        iso=x.isoformat().replace("+00:00","Z")
        if iso not in seen:
            seen.add(iso); out.append({"visible":m.group(0),"capture_utc":iso,"zone_basis":zone})
    for m in _ISO_ZONE_RE.finditer(text):
        raw=m.group("x").strip()
        z=raw
        if re.search(r"\bJST\b",z,re.I):
            z=re.sub(r"\bJST\b","+09:00",z,flags=re.I)
        elif re.search(r"\b(?:UTC|GMT)\b",z,re.I):
            z=re.sub(r"\b(?:UTC|GMT)\b","+00:00",z,flags=re.I)
        try:
            x=dt.datetime.fromisoformat(z.replace("Z","+00:00"))
            if x.tzinfo is None:
                continue
            iso=x.astimezone(UTC).isoformat().replace("+00:00","Z")
            if iso not in seen:
                seen.add(iso); out.append({"visible":raw,"capture_utc":iso,"zone_basis":"EXPLICIT"})
        except Exception:
            pass
    return out

def audit_anchors(
    anchors: list[dict[str,str]],target: str,lower: str,upper: str,
)->dict[str,Any]:
    lo=parse_z(lower); hi=parse_z(upper)
    req(lo<hi,"PIT_WINDOW")
    candidates=[]; witnesses=[]; insufficient=0
    for a in anchors:
        meta=parse_archive_href(a["href"])
        if meta is None:
            continue
        exact=normalize_sky_identity(meta["embedded_original"])==normalize_sky_identity(target)
        if not exact:
            continue
        timestamps=parse_visible_timestamp(a.get("text",""))
        passed=[]
        for t in timestamps:
            when=parse_z(t["capture_utc"])
            if lo<=when<hi:
                passed.append({**t,"archive_href":meta["archive_href"]})
        if not timestamps:
            insufficient+=1
        row={
            **meta,
            "exact_identity":True,
            "visible_timestamp_n":len(timestamps),
            "visible_timestamps":timestamps,
            "path_timestamp_timezone_proven":False,
            "path_timestamp_counted":False,
            "witness_pass_n":len(passed),
            "witnesses":passed,
        }
        candidates.append(row); witnesses.extend(passed)
    return {
        "exact_archive_candidate_n":len(candidates),
        "metadata_insufficient_candidate_n":insufficient,
        "witness_pass_n":len(witnesses),
        "candidates":candidates,
        "witnesses":witnesses,
    }

def classify(witness_n: int,error_n: int,insufficient_n: int,p: dict[str,Any])->tuple[str,str]:
    if witness_n>0:
        return p["decision_contract"]["positive_classification"],p["reasonable_subroutes"]["if_positive"]
    if error_n>0:
        return p["decision_contract"]["fail_classification"],p["reasonable_subroutes"]["if_external_error"]
    if insufficient_n>0:
        return p["decision_contract"]["fail_classification"],p["reasonable_subroutes"]["if_metadata_insufficient"]
    return p["decision_contract"]["fail_classification"],p["reasonable_subroutes"]["if_clean_zero"]

def run(registry: Path,out: Path,token: str)->dict[str,Any]:
    p=json.loads(registry.read_text(encoding="utf-8"))
    req(p["status"]=="DESIGN_LOCKED_ZERO_LABEL_METADATA_ONLY","STATUS")
    req(p["exact_base"]=="d44de65d73e42000ebf5d1b941a0625ec6e6c1fd","EXACT_BASE")
    sources=p["lookup_contract_sources"]
    req(sources[0]["git_blob_sha"]=="21d02d5025c2351fe4dfeb473399125cc5e774c8","LOOKUP_CONTRACT_BLOB")
    req(sources[1]["git_blob_sha"]=="3543138aacf595cc0059ba6c0245fb798360be67","CREATE_SEPARATION_BLOB")
    tc=p["timestamp_contract"]
    req(tc["path_timestamp_timezone_proven"] is False,"PATH_TZ_UNPROVEN")
    req(tc["path_timestamp_alone_may_count"] is False,"PATH_TIME_NO_COUNT")
    h=p["hard_rules"]
    req(h["previous_source_requery_allowed"] is False,"NO_OLD_REQUERY")
    req(h["form_submission_allowed"] is False and h["archive_creation_allowed"] is False,"NO_CREATE")
    req(h["archive_page_fetch_allowed"] is False and h["link_follow_allowed"] is False,"NO_ARCHIVE_BODY")
    req(h["lookup_html_persisted"] is False,"NO_HTML_PERSIST")
    req(h["infer_archive_path_timezone_allowed"] is False,"NO_TZ_INFERENCE")
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
    src=p["source"]
    reports=[]; errors=[]; witnesses=[]; insufficient_rounds=[]
    for rnd in samples:
        prow=pit_rows[rnd]; srow=sky_rows[rnd]
        req(prow["binding_status"]=="FAIL",f"SAMPLE_ALREADY_PASS:R{rnd}")
        target=srow.get("sky_url")
        req(isinstance(target,str) and target.startswith("https://sport.sky.it/"),f"SKY_URL:R{rnd}")
        lower=prow["sky_visible_published_utc"]; upper=prow["first_fixture_cutoff_utc"]
        q=build_lookup_url(src["lookup_base"],target)
        report={
            "round":rnd,"target_url":target,"lower_utc":lower,"upper_utc":upper,
            "query_url":q,"status":"UNSET","http_status":None,
            "request_method":"GET","form_submitted":False,"archive_creation_performed":False,
            "archive_link_followed":False,"archive_page_fetch_performed":False,
            "lookup_html_persisted":False,"witness_pass_n":0,
        }
        try:
            status,raw,final,headers=fetch_lookup(q,src)
            report.update({"http_status":status,"final_url":final,"content_type":headers.get("content-type")})
            if not (200<=status<300):
                report["status"]="HTTP_ERROR"; report["error"]=f"HTTP_{status}"
                errors.append({"round":rnd,"error":report["error"]})
            else:
                marker=anti_bot(raw)
                if marker:
                    report["status"]="EXTERNAL_ANTI_BOT_BLOCK"; report["error"]=f"ANTI_BOT:{marker}"
                    errors.append({"round":rnd,"error":report["error"]})
                else:
                    anchors=parse_anchors(raw,final)
                    audit=audit_anchors(anchors,target,lower,upper)
                    report.update({
                        "status":"SUCCESS","response_bytes":len(raw),"response_sha256":sha256_bytes(raw),
                        "anchor_n":len(anchors),**audit,"witness_pass_n":audit["witness_pass_n"],
                    })
                    if audit["metadata_insufficient_candidate_n"]>0:
                        insufficient_rounds.append(rnd)
                    for w in audit["witnesses"]:
                        witnesses.append({"round":rnd,**w})
        except Exception as exc:
            report["status"]="EXTERNAL_OR_CONTRACT_ERROR"
            report["error"]=f"{type(exc).__name__}:{exc}"[:800]
            errors.append({"round":rnd,"error":report["error"]})
        reports.append(report)
    insufficient_rounds=sorted(set(insufficient_rounds))
    positive_rounds=sorted({int(x["round"]) for x in witnesses})
    classification,next_step=classify(len(witnesses),len(errors),len(insufficient_rounds),p)
    matrix={
        "schema_version":"football3-nova-n10-referee-sky-megalodon-feasibility-matrix-v1",
        "sample_rounds":samples,"query_n":len(reports),"error_n":len(errors),"errors":errors,
        "metadata_insufficient_rounds":insufficient_rounds,
        "metadata_insufficient_round_n":len(insufficient_rounds),
        "witness_pass_n":len(witnesses),"positive_rounds":positive_rounds,
        "reports":reports,
        "path_timestamp_timezone_proven":False,"path_timestamp_counted":False,
        "form_submitted":False,"archive_creation_performed":False,
        "archive_link_followed":False,"archive_page_fetch_performed":False,
        "lookup_html_persisted":False,
    }
    out.mkdir(parents=True,exist_ok=True)
    mb=(json.dumps(matrix,indent=2,sort_keys=True,ensure_ascii=False)+"\n").encode("utf-8")
    (out/"sky_megalodon_feasibility_matrix.json").write_bytes(mb)
    receipt={
        "schema_version":"football3-nova-n10-referee-sky-megalodon-feasibility-receipt-v1",
        "status":"N10_REFEREE_SKY_MEGALODON_FEASIBILITY_COMPLETE",
        "classification":classification,"exact_base":p["exact_base"],
        "registry_sha256":sha256_bytes(registry.read_bytes()),
        "lookup_contract_blob_sha":sources[0]["git_blob_sha"],
        "create_separation_blob_sha":sources[1]["git_blob_sha"],
        "parent_provenance":parent_prov,"sample_rounds":samples,"query_n":len(reports),
        "error_n":len(errors),"errors":errors,
        "metadata_insufficient_rounds":insufficient_rounds,
        "metadata_insufficient_round_n":len(insufficient_rounds),
        "witness_pass_n":len(witnesses),"positive_rounds":positive_rounds,
        "matrix_sha256":sha256_bytes(mb),"path_timestamp_timezone_proven":False,
        "path_timestamp_counted":False,"previous_source_requery_n":0,
        "request_method":"GET","form_submitted":False,"archive_creation_performed":False,
        "archive_link_followed":False,"archive_page_fetch_performed":False,
        "lookup_html_persisted":False,"article_body_read":False,"referee_assignment_body_parsed":False,
        "match_result_payload_read":False,"standings_payload_read":False,"player_stats_payload_read":False,
        "result_labels_read":0,"score_values_read":0,"training_performed":False,"scoring_performed":False,
        "formal_v2_changed":False,"current_changed":False,"production_changed":False,
        "candidate_weight":0,"matrix_delta":0,"formal_available_at_proven":False,
        "fixture_level_binding_complete":False,"referee_oof_allowed":False,"next_step":next_step,
    }
    (out/"sky_megalodon_feasibility_receipt.json").write_text(
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
