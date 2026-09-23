#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import io
import json
import os
import re
import ssl
import time
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from nova_n10_referee_sky_combined_freeze_v1 import (
    download_artifact_zip,
    read_unique_suffix,
    sha256_bytes,
)

ROME=ZoneInfo("Europe/Rome")
UTC=dt.timezone.utc

class SkyPitBindingError(RuntimeError):
    pass

def req(cond: bool, msg: str) -> None:
    if not cond:
        raise SkyPitBindingError(msg)

def norm(s: str) -> str:
    return re.sub(r"\s+"," ",html.unescape(s or "")).strip()

def host_ok(url: str, expected: str) -> bool:
    h=(urllib.parse.urlparse(url).hostname or "").lower()
    e=expected.lower()
    return h==e or h.endswith("."+e)

def stable_bytes(obj: Any) -> bytes:
    return (json.dumps(obj,indent=2,sort_keys=True,ensure_ascii=False)+"\n").encode("utf-8")

def fetch_bytes(
    url: str,
    *,
    allowed_host: str,
    timeout: int,
    limit: int,
    accept: str="text/html,application/xhtml+xml",
) -> tuple[bytes,str,dict[str,str]]:
    req(host_ok(url,allowed_host),f"REQUEST_OUTSIDE_ALLOWED_HOST:{url}")
    request=urllib.request.Request(
        url,
        headers={
            "User-Agent":"Football3-Nova-N10-SkyPitBinding/1.0",
            "Accept":accept,
            "Accept-Language":"it-IT,it;q=0.9,en;q=0.5",
        },
    )
    with urllib.request.urlopen(request,timeout=timeout,context=ssl.create_default_context()) as response:
        final=response.geturl()
        req(host_ok(final,allowed_host),f"REDIRECT_OUTSIDE_ALLOWED_HOST:{final}")
        raw=response.read(limit+1)
        req(len(raw)<=limit,f"RESPONSE_TOO_LARGE:{url}")
        return raw,final,{k.lower():v for k,v in response.headers.items()}

class PageTextParser(HTMLParser):
    BLOCK={
        "html","head","body","main","article","section","div","p","br","li","ul","ol",
        "h1","h2","h3","h4","table","tr","td","th","header","footer","aside"
    }
    IGNORE={"script","style","noscript","template","svg"}
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url=base_url
        self.ignore_depth=0
        self.in_title=False
        self.title_parts: list[str]=[]
        self.title_candidates: list[str]=[]
        self.text_parts: list[str]=[]
        self.current_anchor: dict[str,Any] | None=None
        self.anchors: list[dict[str,str]]=[]
    def handle_starttag(self, tag: str, attrs: list[tuple[str,str|None]]) -> None:
        tag=tag.lower()
        if tag in self.IGNORE:
            self.ignore_depth+=1
            return
        d={str(k).lower():(v or "") for k,v in attrs}
        if tag=="title":
            self.in_title=True
            self.title_parts=[]
        if tag=="meta":
            key=(d.get("property") or d.get("name") or "").casefold()
            if key in {"og:title","twitter:title"}:
                val=norm(d.get("content",""))
                if val:
                    self.title_candidates.append(val)
        if tag=="a":
            href=d.get("href","").strip()
            if href:
                self.current_anchor={
                    "href":urllib.parse.urljoin(self.base_url,href),
                    "parts":[],
                    "attr_text":" ".join(x for x in [d.get("title",""),d.get("aria-label","")] if x),
                }
        if tag in self.BLOCK:
            self.text_parts.append("\n")
    def handle_endtag(self, tag: str) -> None:
        tag=tag.lower()
        if tag in self.IGNORE and self.ignore_depth>0:
            self.ignore_depth-=1
            return
        if tag=="title":
            self.in_title=False
            val=norm(" ".join(self.title_parts))
            if val:
                self.title_candidates.append(val)
        if tag=="a" and self.current_anchor is not None:
            txt=norm(self.current_anchor["attr_text"]+" "+" ".join(self.current_anchor["parts"]))
            self.anchors.append({"href":self.current_anchor["href"],"text":txt})
            self.current_anchor=None
        if tag in self.BLOCK:
            self.text_parts.append("\n")
    def handle_data(self, data: str) -> None:
        if self.ignore_depth>0:
            return
        if self.in_title:
            self.title_parts.append(data)
        piece=html.unescape(data or "")
        if piece.strip():
            self.text_parts.append(piece)
            self.text_parts.append(" ")
        if self.current_anchor is not None:
            self.current_anchor["parts"].append(data)
    def visible_text(self) -> str:
        txt="".join(self.text_parts)
        txt=re.sub(r"[ \t\r\f\v]+"," ",txt)
        txt=re.sub(r"\n\s*\n+","\n",txt)
        return txt.strip()

def parse_page(raw: bytes, final_url: str) -> PageTextParser:
    p=PageTextParser(final_url)
    p.feed(raw.decode("utf-8","replace"))
    return p

def title_pass(candidates: list[str], terms: list[str]) -> bool:
    folded=[norm(x).casefold() for x in candidates]
    wanted=[norm(x).casefold() for x in terms]
    return any(all(t in c for t in wanted) for c in folded)

def resolve_schedule_source(source: dict[str,Any], contract: dict[str,Any]) -> dict[str,Any]:
    timeout=int(contract["request_timeout_seconds"])
    limit=int(contract["max_response_bytes"])
    host=source["allowed_host"]
    discovery=None
    if source["mode"]=="DIRECT":
        url=source["url"]
    elif source["mode"] in {"DISCOVER_EXACT_TITLE_FROM_INDEX","DISCOVER_TITLE_TERMS_FROM_INDEX"}:
        raw,final,headers=fetch_bytes(
            source["index_url"],allowed_host=host,timeout=timeout,limit=limit
        )
        parsed=parse_page(raw,final)
        if source["mode"]=="DISCOVER_EXACT_TITLE_FROM_INDEX":
            exact=norm(source["exact_title"])
            matches=[
                a for a in parsed.anchors
                if norm(a["text"])==exact and host_ok(a["href"],host)
            ]
            discovery_identity={"exact_title":exact}
        else:
            terms=[norm(x).casefold() for x in source["title_terms"]]
            matches=[
                a for a in parsed.anchors
                if host_ok(a["href"],host)
                and all(t in norm(a["text"]).casefold() for t in terms)
            ]
            discovery_identity={"title_terms":source["title_terms"]}
        uniq={a["href"]:a for a in matches}
        req(len(uniq)==1,f"INDEX_TITLE_MATCH_NOT_UNIQUE:{source['id']}:{len(uniq)}")
        url=next(iter(uniq))
        discovery={
            "index_url":source["index_url"],
            "index_final_url":final,
            "index_sha256":sha256_bytes(raw),
            **discovery_identity,
            "discovered_url":url,
        }
    else:
        raise SkyPitBindingError(f"UNKNOWN_SOURCE_MODE:{source['mode']}")
    raw,final,headers=fetch_bytes(url,allowed_host=host,timeout=timeout,limit=limit)
    parsed=parse_page(raw,final)
    if "title_terms" in source:
        req(title_pass(parsed.title_candidates,source["title_terms"]),f"TITLE_IDENTITY_FAIL:{source['id']}")
    if "exact_title" in source:
        req(
            any(norm(t)==norm(source["exact_title"]) for t in parsed.title_candidates),
            f"EXACT_ARTICLE_TITLE_FAIL:{source['id']}",
        )
    return {
        "source_id":source["id"],
        "requested_url":url,
        "final_url":final,
        "html_sha256":sha256_bytes(raw),
        "html_bytes":len(raw),
        "content_type":headers.get("content-type"),
        "title_candidates":list(dict.fromkeys(parsed.title_candidates)),
        "text":parsed.visible_text(),
        "discovery":discovery,
        "assigned_rounds":[int(x) for x in source["assigned_rounds"]],
    }

ROUND_RE=re.compile(r"(?<!\d)(3\s*[0-8]|[12]\s*[0-9]|[1-9])\s*(?:a|ª|\^)?\s*GIORNATA\b",re.I)
DATE_TIME_RE=re.compile(
    r"(?P<date>\d{2}/\d{2}/\d{4})\s+"
    r"(?:(?:Luned[iì]|Marted[iì]|Mercoled[iì]|Gioved[iì]|Venerd[iì]|Sabato|Domenica)\s+)?"
    r"(?P<time>[0-2]?\s*\d[\.:]\d{2})\s+",
    re.I,
)
LICENSE_CUT_RE=re.compile(
    r"\b(?:CO\s*[-−]?\s*ESCLUSIVA|ESCLUSIVA|DAZN|SKY|NOW|TV8|ZONA\s+DAZN)\b",
    re.I,
)
TEAM_SEP_RE=re.compile(r"\s*[-–—−]\s*")

def schedule_start(text: str, marker_terms: list[str]) -> int:
    compact=text.casefold()
    pos=0
    for term in marker_terms:
        i=compact.find(term.casefold(),pos)
        req(i>=0,f"SCHEDULE_MARKER_TERM_MISSING:{term}")
        pos=i+len(term)
    return pos

def heading_spans(text: str, start: int) -> list[tuple[int,int,int]]:
    matches=list(ROUND_RE.finditer(text,start))
    out=[]
    for i,m in enumerate(matches):
        end=matches[i+1].start() if i+1<len(matches) else len(text)
        round_no=int(re.sub(r"\s+","",m.group(1)))
        if 1 <= round_no <= 38:
            out.append((round_no,m.start(),end))
    return out

def clean_fixture_label(raw: str) -> str:
    s=norm(raw)
    m=LICENSE_CUT_RE.search(s)
    if m:
        s=s[:m.start()]
    s=re.sub(r"\s*[\*†‡]+\s*$","",s)
    s=norm(s)
    return s

def parse_fixture_segment(segment: str, round_no: int, timezone_name: str) -> list[dict[str,Any]]:
    ms=list(DATE_TIME_RE.finditer(segment))
    fixtures=[]
    seen=set()
    for i,m in enumerate(ms):
        label_end=ms[i+1].start() if i+1<len(ms) else len(segment)
        label=clean_fixture_label(segment[m.end():label_end])
        if not label:
            continue
        sep=TEAM_SEP_RE.search(label)
        if not sep:
            continue
        home=norm(label[:sep.start()])
        away=norm(label[sep.end():])
        # Strip any trailing prose accidentally captured after the away team.
        away=re.split(r"\b(?:MATCH|TOTALE|PUBBLICATO|GIORNATA|CLASSIFICA)\b",away,maxsplit=1,flags=re.I)[0]
        away=norm(away)
        if not home or not away:
            continue
        date=dt.datetime.strptime(m.group("date"),"%d/%m/%Y").date()
        hhmm=re.sub(r"\s+","",m.group("time")).replace(".",":")
        t=dt.time.fromisoformat(hhmm)
        local=dt.datetime.combine(date,t,tzinfo=ZoneInfo(timezone_name))
        utc=local.astimezone(UTC)
        key=(round_no,utc.isoformat(),home.casefold(),away.casefold())
        if key in seen:
            continue
        seen.add(key)
        fixtures.append({
            "round":round_no,
            "date_local":date.isoformat(),
            "time_local":hhmm,
            "timezone":timezone_name,
            "kickoff_local":local.isoformat(),
            "kickoff_utc":utc.isoformat().replace("+00:00","Z"),
            "home":home,
            "away":away,
            "fixture_key":f"{round_no}|{utc.isoformat().replace('+00:00','Z')}|{home.casefold()}|{away.casefold()}",
        })
    return fixtures

def build_fixture_schedule(
    registry: dict[str,Any],
) -> tuple[dict[str,Any],list[dict[str,Any]],list[dict[str,Any]]]:
    fc=registry["fixture_schedule_contract"]
    source_contract={
        "request_timeout_seconds":30,
        "max_response_bytes":4000000,
    }
    source_reports=[]
    source_errors=[]
    round_rows={}
    for source in registry["fixture_schedule_sources"]:
        try:
            report=resolve_schedule_source(source,source_contract)
            text=report.pop("text")
            start=schedule_start(text,fc["schedule_marker_terms"])
            spans=heading_spans(text,start)
            by_round={}
            for rnd,s,e in spans:
                if rnd not in by_round:
                    by_round[rnd]=(s,e)
            parsed_rounds=[]
            for rnd in report["assigned_rounds"]:
                req(rnd in by_round,f"ROUND_HEADING_MISSING:{source['id']}:R{rnd}")
                s,e=by_round[rnd]
                fixtures=parse_fixture_segment(text[s:e],rnd,fc["source_timezone"])
                req(
                    len(fixtures)==int(fc["expected_fixture_n_per_round"]),
                    f"FIXTURE_COUNT:{source['id']}:R{rnd}:{len(fixtures)}",
                )
                req(rnd not in round_rows,f"DUPLICATE_ASSIGNED_ROUND:R{rnd}")
                fixtures=sorted(fixtures,key=lambda x:x["kickoff_utc"])
                round_rows[rnd]={
                    "round":rnd,
                    "source_id":source["id"],
                    "source_final_url":report["final_url"],
                    "source_html_sha256":report["html_sha256"],
                    "fixture_n":len(fixtures),
                    "first_fixture_cutoff_local":fixtures[0]["kickoff_local"],
                    "first_fixture_cutoff_utc":fixtures[0]["kickoff_utc"],
                    "fixtures":fixtures,
                }
                parsed_rounds.append(rnd)
            report["parsed_rounds"]=parsed_rounds
            report["raw_html_persisted"]=False
            report["full_visible_text_persisted"]=False
            source_reports.append(report)
        except Exception as exc:
            source_errors.append({
                "source_id":source["id"],
                "error":f"{type(exc).__name__}:{exc}"[:800],
            })
    expected=set(range(1,39))
    complete=(set(round_rows)==expected and not source_errors)
    ledger={
        "schema_version":"football3-nova-n10-referee-zero-label-fixture-schedule-v1",
        "competition":"Serie_A",
        "season":"2022/23",
        "timezone":fc["source_timezone"],
        "round_n":38,
        "covered_round_n":len(round_rows),
        "fixture_n":sum(x["fixture_n"] for x in round_rows.values()),
        "complete":complete,
        "raw_html_persisted":False,
        "full_visible_text_persisted":False,
        "score_values_parsed":0,
        "result_labels_parsed":0,
        "source_reports":source_reports,
        "source_errors":source_errors,
        "rows":[round_rows[i] for i in sorted(round_rows)],
    }
    return ledger,source_reports,source_errors

def normalize_sky_identity(url: str) -> tuple[str,str]:
    p=urllib.parse.urlparse(url)
    host=(p.hostname or "").lower()
    if host.startswith("www."):
        host=host[4:]
    path=urllib.parse.unquote(p.path or "")
    path=re.sub(r"/amp/?$","",path,flags=re.I)
    path=path.rstrip("/") or "/"
    return host,path

def cdx_query_url(endpoint: str, sky_url: str, cfg: dict[str,Any]) -> str:
    host,path=normalize_sky_identity(sky_url)
    wildcard=f"{host}{path}*"
    params=[
        ("url",wildcard),
        ("output","json"),
        ("fl",",".join(cfg["fields"])),
        ("filter","statuscode:200"),
        ("filter","mimetype:text/html"),
        ("collapse",cfg["collapse"]),
        ("from",str(cfg["from_year"])),
        ("to",str(cfg["to_year"])),
        ("limit",str(cfg["max_rows_per_variant"])),
    ]
    return endpoint+"?"+urllib.parse.urlencode(params)

def parse_cdx(raw: bytes, sky_url: str) -> list[dict[str,Any]]:
    obj=json.loads(raw.decode("utf-8"))
    if not obj:
        return []
    req(isinstance(obj,list) and isinstance(obj[0],list),"CDX_JSON_SCHEMA")
    header=obj[0]
    idx={name:i for i,name in enumerate(header)}
    for field in ["timestamp","original","statuscode","mimetype","digest"]:
        req(field in idx,f"CDX_FIELD_MISSING:{field}")
    target=normalize_sky_identity(sky_url)
    out=[]
    seen=set()
    for row in obj[1:]:
        if not isinstance(row,list) or len(row)<len(header):
            continue
        ts=str(row[idx["timestamp"]])
        original=str(row[idx["original"]])
        if normalize_sky_identity(original)!=target:
            continue
        if not re.fullmatch(r"\d{14}",ts):
            continue
        if str(row[idx["statuscode"]])!="200":
            continue
        capture=dt.datetime.strptime(ts,"%Y%m%d%H%M%S").replace(tzinfo=UTC)
        key=(ts,original)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "timestamp":ts,
            "capture_utc":capture.isoformat().replace("+00:00","Z"),
            "original":original,
            "digest":str(row[idx["digest"]]),
            "statuscode":"200",
            "mimetype":str(row[idx["mimetype"]]),
        })
    return sorted(out,key=lambda x:x["timestamp"])

def fetch_wayback_witness(
    row: dict[str,Any],
    cfg: dict[str,Any],
) -> dict[str,Any]:
    url=cdx_query_url(cfg["endpoint"],row["sky_url"],cfg)
    attempts=int(cfg.get("retry_attempts",1))
    backoffs=[float(x) for x in cfg.get("retry_backoff_seconds",[])]
    last_exc: Exception | None=None
    for attempt in range(1,attempts+1):
        try:
            raw,final,headers=fetch_bytes(
                url,
                allowed_host=cfg["allowed_host"],
                timeout=int(cfg["request_timeout_seconds"]),
                limit=int(cfg["max_response_bytes"]),
                accept="application/json,text/plain,*/*",
            )
            captures=parse_cdx(raw,row["sky_url"])
            return {
                "round":int(row["round"]),
                "sky_url":row["sky_url"],
                "query_url":url,
                "final_url":final,
                "response_sha256":sha256_bytes(raw),
                "response_bytes":len(raw),
                "capture_n":len(captures),
                "first_capture":captures[0] if captures else None,
                "captures":captures[:20],
                "attempt_n":attempt,
            }
        except Exception as exc:
            last_exc=exc
            if attempt>=attempts:
                break
            delay=backoffs[min(attempt-1,len(backoffs)-1)] if backoffs else 0.0
            if delay>0:
                time.sleep(delay)
    assert last_exc is not None
    raise last_exc

def parse_z(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z","+00:00"))

def local_source_to_utc(s: str, timezone_name: str) -> dt.datetime:
    x=dt.datetime.fromisoformat(s)
    if x.tzinfo is None:
        x=x.replace(tzinfo=ZoneInfo(timezone_name))
    return x.astimezone(UTC)

def build_binding(
    registry: dict[str,Any],
    sky_ledger: dict[str,Any],
    anomaly_ledger: dict[str,Any],
    aia_ledger: dict[str,Any],
    fixture_ledger: dict[str,Any],
    resume_witness: dict[str,Any] | None=None,
) -> tuple[dict[str,Any],dict[str,Any]]:
    req(sky_ledger["round_n"]==38 and len(sky_ledger["rows"])==38,"SKY_LEDGER_ROUNDS")
    req(anomaly_ledger["anomaly_rounds"]==[23],"SKY_ANOMALY_CONTRACT")
    req(aia_ledger["canonical_round_n"]==38 and len(aia_ledger["rows"])==38,"AIA_LEDGER_ROUNDS")
    req(fixture_ledger["complete"] is True,"FIXTURE_SCHEDULE_INCOMPLETE")
    req(fixture_ledger["fixture_n"]==380,"FIXTURE_N_NOT_380")
    sky={int(x["round"]):x for x in sky_ledger["rows"]}
    aia={int(x["round"]):x for x in aia_ledger["rows"]}
    fixtures={int(x["round"]):x for x in fixture_ledger["rows"]}
    req(sorted(sky)==list(range(1,39)),"SKY_ROUND_IDENTITY")
    req(sorted(aia)==list(range(1,39)),"AIA_ROUND_IDENTITY")
    req(sorted(fixtures)==list(range(1,39)),"FIXTURE_ROUND_IDENTITY")

    cfg=registry["independent_archive_witness"]
    resume_cfg=registry.get("resume_parent")
    witness_reports={}
    if resume_witness is not None:
        req(resume_cfg is not None,"RESUME_CONFIG_MISSING")
        frozen_success={int(x) for x in resume_cfg["successful_rounds"]}
        frozen_retry={int(x) for x in resume_cfg["retry_rounds"]}
        req(frozen_success.isdisjoint(frozen_retry),"RESUME_PARTITION_OVERLAP")
        req(frozen_success | frozen_retry == set(range(1,39)),"RESUME_PARTITION_INCOMPLETE")
        prior={int(x["round"]):x for x in resume_witness.get("reports",[])}
        req(set(prior)==frozen_success,"RESUME_SUCCESS_REPORT_SET_MISMATCH")
        witness_reports.update(prior)
        target_rounds=sorted(frozen_retry)
    else:
        target_rounds=list(range(1,39))
    witness_errors={}
    max_workers=int(cfg.get("max_concurrent_requests",1))
    req(1 <= max_workers <= 4,"WAYBACK_CONCURRENCY_BOUND")
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs={ex.submit(fetch_wayback_witness,sky[r],cfg):r for r in target_rounds}
        for fut in as_completed(futs):
            rnd=futs[fut]
            try:
                witness_reports[rnd]=fut.result()
            except Exception as exc:
                witness_errors[rnd]=f"{type(exc).__name__}:{exc}"[:800]

    tol=dt.timedelta(seconds=int(registry["pit_binding_contract"]["source_visible_timestamp_early_tolerance_seconds"]))
    rows=[]
    pass_rounds=[]
    no_capture=[]
    post_cutoff=[]
    timestamp_conflicts=[]
    for rnd in range(1,39):
        s=sky[rnd]
        a=aia[rnd]
        f=fixtures[rnd]
        reasons=[]
        witness=witness_reports.get(rnd)
        capture=None if witness is None else witness.get("first_capture")
        sky_pub_utc=local_source_to_utc(s["sky_published_local"],s.get("timezone") or "Europe/Rome")
        first_kickoff=parse_z(f["first_fixture_cutoff_utc"])
        if a.get("round")!=rnd:
            reasons.append("AIA_ROUND_IDENTITY_MISMATCH")
        if f.get("round")!=rnd:
            reasons.append("FIXTURE_ROUND_IDENTITY_MISMATCH")
        if rnd in witness_errors:
            reasons.append("EXTERNAL_FETCH_ERROR")
        if capture is None:
            reasons.append("NO_INDEPENDENT_CAPTURE")
            no_capture.append(rnd)
        else:
            cap=parse_z(capture["capture_utc"])
            if cap + tol < sky_pub_utc:
                reasons.append("CAPTURE_BEFORE_SOURCE_TIMESTAMP_BEYOND_TOLERANCE")
                timestamp_conflicts.append(rnd)
            if not cap < first_kickoff:
                reasons.append("CAPTURE_NOT_PRE_FIRST_KICKOFF")
                post_cutoff.append(rnd)
        status="PASS" if not reasons else "FAIL"
        if status=="PASS":
            pass_rounds.append(rnd)
        rows.append({
            "round":rnd,
            "aia_published_date":a.get("published_date"),
            "sky_visible_published_local":s["sky_published_local"],
            "sky_visible_published_utc":sky_pub_utc.isoformat().replace("+00:00","Z"),
            "first_fixture_cutoff_local":f["first_fixture_cutoff_local"],
            "first_fixture_cutoff_utc":f["first_fixture_cutoff_utc"],
            "fixture_n":f["fixture_n"],
            "fixture_source_id":f["source_id"],
            "fixture_source_url":f["source_final_url"],
            "fixture_source_html_sha256":f["source_html_sha256"],
            "wayback_capture":capture,
            "wayback_response_sha256":None if witness is None else witness["response_sha256"],
            "conservative_publication_available_at_utc":None if capture is None else capture["capture_utc"],
            "binding_status":status,
            "failure_reasons":reasons,
            "identity_anomaly":rnd in anomaly_ledger["anomaly_rounds"],
            "referee_assignment_body_used":False,
        })

    external_error_n=len(witness_errors)
    pass_n=len(pass_rounds)
    if fixture_ledger["complete"] and external_error_n==0 and pass_n==38:
        classification=registry["decision_contract"]["full_pass_classification"]
        next_step=registry["decision_contract"]["next_if_full_pass"]
    elif fixture_ledger["complete"] and external_error_n==0 and pass_n>0:
        classification=registry["decision_contract"]["partial_classification"]
        next_step=registry["decision_contract"]["next_if_partial"]
    else:
        classification=registry["decision_contract"]["blocked_classification"]
        next_step=registry["decision_contract"]["next_if_blocked"]

    binding={
        "schema_version":"football3-nova-n10-referee-sky-publication-pit-binding-v1",
        "status":"N10_REFEREE_SKY_PUBLICATION_PIT_BINDING_COMPLETE",
        "classification":classification,
        "round_n":38,
        "fixture_schedule_binding_complete":fixture_ledger["complete"],
        "fixture_n":fixture_ledger["fixture_n"],
        "independent_witness_round_n":len(witness_reports),
        "independent_witness_error_n":external_error_n,
        "independent_witness_errors":{str(k):v for k,v in sorted(witness_errors.items())},
        "pit_pass_round_n":pass_n,
        "pit_pass_rounds":pass_rounds,
        "no_capture_rounds":no_capture,
        "post_cutoff_capture_rounds":post_cutoff,
        "timestamp_conflict_rounds":timestamp_conflicts,
        "publication_pit_binding_complete":pass_n==38 and external_error_n==0 and fixture_ledger["complete"],
        "publication_available_at_bound_complete":pass_n==38 and external_error_n==0 and fixture_ledger["complete"],
        "referee_assignment_fixture_binding_complete":False,
        "formal_available_at_proven_for_publication_pages":pass_n==38 and external_error_n==0 and fixture_ledger["complete"],
        "formal_available_at_proven_for_referee_assignments":False,
        "referee_oof_allowed":False,
        "rows":rows,
        "next_step":next_step,
    }
    witness_ledger={
        "schema_version":"football3-nova-n10-referee-wayback-witness-ledger-v1",
        "provider":cfg["provider"],
        "round_n":38,
        "report_n":len(witness_reports),
        "error_n":len(witness_errors),
        "resume_parent_used":resume_witness is not None,
        "frozen_prior_report_n":0 if resume_witness is None else len(resume_witness.get("reports",[])),
        "new_query_round_n":len(target_rounds),
        "reports":[witness_reports[i] for i in sorted(witness_reports)],
        "errors":{str(k):v for k,v in sorted(witness_errors.items())},
        "raw_archived_page_body_read":False,
        "cdx_metadata_only":True,
    }
    return binding,witness_ledger

def acquire_parent(registry: dict[str,Any], token: str) -> tuple[dict[str,Any],dict[str,Any],dict[str,Any]]:
    p=registry["parent_freeze"]
    raw=download_artifact_zip(registry["repository"],int(p["artifact_id"]),token)
    req(sha256_bytes(raw)==p["artifact_zip_sha256"],"PARENT_ARTIFACT_SHA")
    _,ledger_raw,ledger=read_unique_suffix(raw,p["ledger_suffix"])
    _,anom_raw,anom=read_unique_suffix(raw,p["anomaly_suffix"])
    req(sha256_bytes(ledger_raw)==p["ledger_sha256"],"PARENT_LEDGER_SHA")
    req(sha256_bytes(anom_raw)==p["anomaly_sha256"],"PARENT_ANOMALY_SHA")
    return ledger,anom,{
        "artifact_zip_sha256":sha256_bytes(raw),
        "ledger_sha256":sha256_bytes(ledger_raw),
        "anomaly_sha256":sha256_bytes(anom_raw),
    }

def acquire_resume(registry: dict[str,Any], token: str) -> tuple[dict[str,Any],dict[str,Any],dict[str,Any]]:
    p=registry["resume_parent"]
    raw=download_artifact_zip(registry["repository"],int(p["artifact_id"]),token)
    zsha=sha256_bytes(raw)
    req(zsha==p["artifact_zip_sha256"],"RESUME_ARTIFACT_SHA")
    _,fixture_raw,fixture=read_unique_suffix(raw,p["fixture_schedule_suffix"])
    _,witness_raw,witness=read_unique_suffix(raw,p["witness_suffix"])
    _,binding_raw,binding=read_unique_suffix(raw,p["pit_binding_suffix"])
    req(sha256_bytes(fixture_raw)==p["fixture_schedule_sha256"],"RESUME_FIXTURE_SHA")
    req(sha256_bytes(witness_raw)==p["witness_sha256"],"RESUME_WITNESS_SHA")
    req(sha256_bytes(binding_raw)==p["pit_binding_sha256"],"RESUME_BINDING_SHA")
    req(fixture.get("complete") is True and fixture.get("fixture_n")==380,"RESUME_FIXTURE_NOT_COMPLETE")
    success={int(x) for x in p["successful_rounds"]}
    reports={int(x["round"]) for x in witness.get("reports",[])}
    req(reports==success,"RESUME_WITNESS_REPORT_SET")
    return fixture,witness,{
        "artifact_zip_sha256":zsha,
        "fixture_schedule_sha256":sha256_bytes(fixture_raw),
        "wayback_witness_sha256":sha256_bytes(witness_raw),
        "pit_binding_sha256":sha256_bytes(binding_raw),
        "successful_rounds":sorted(success),
        "retry_rounds":[int(x) for x in p["retry_rounds"]],
    }

def run(registry_path: Path, aia_path: Path, out: Path, token: str) -> dict[str,Any]:
    registry=json.loads(registry_path.read_text(encoding="utf-8"))
    req(registry["status"]=="DESIGN_LOCKED_ZERO_LABEL_INDEPENDENT_PIT_BINDING","STATUS")
    req(registry["exact_base"]=="62861d06c61b27fcd956248375d8e78d673c4536","EXACT_BASE")
    registry["repository"]="FASHI188/FASHI188-football-analysis"
    hard=registry["hard_rules"]
    req(hard["result_labels_read"] is False and hard["score_values_read"] is False,"ZERO_LABEL")
    req(hard["score_or_result_semantic_parse"] is False,"NO_RESULT_PARSE")
    req(hard["referee_assignment_body_parsed"] is False,"NO_ASSIGNMENT_BODY")
    req(hard["sky_article_body_reacquired"] is False,"NO_SKY_BODY")
    req(hard["training_allowed"] is False and hard["scoring_allowed"] is False,"NO_MODEL")
    req(hard["paid_or_secret_source_allowed"] is False,"NO_PAID_SECRET")
    req(hard["candidate_weight"]==0 and hard["matrix_delta"]==0,"ZERO_WEIGHT")

    aia_raw=aia_path.read_bytes()
    req(sha256_bytes(aia_raw)==registry["aia_ledger"]["sha256"],"AIA_LEDGER_SHA")
    aia=json.loads(aia_raw.decode("utf-8"))
    req(aia["competition"]=="Serie_A" and aia["season"]=="2022/23","AIA_IDENTITY")

    sky,anom,parent_prov=acquire_parent(registry,token)
    fixture_ledger,resume_witness,resume_prov=acquire_resume(registry,token)
    source_errors=[]

    out.mkdir(parents=True,exist_ok=True)
    fixture_bytes=stable_bytes(fixture_ledger)
    req(sha256_bytes(fixture_bytes)==registry["resume_parent"]["fixture_schedule_sha256"],"RESUME_FIXTURE_STABLE_SHA")
    (out/"zero_label_fixture_schedule_ledger.json").write_bytes(fixture_bytes)

    if not fixture_ledger["complete"]:
        receipt={
            "schema_version":"football3-nova-n10-referee-sky-pit-binding-receipt-v1",
            "status":"N10_REFEREE_SKY_PIT_BINDING_BLOCKED",
            "classification":registry["decision_contract"]["blocked_classification"],
            "reason":"FIXTURE_SCHEDULE_PARSE_INCOMPLETE",
            "exact_base":registry["exact_base"],
            "registry_sha256":sha256_bytes(registry_path.read_bytes()),
            "aia_ledger_sha256":sha256_bytes(aia_raw),
            "parent_provenance":parent_prov,
            "fixture_schedule_sha256":sha256_bytes(fixture_bytes),
            "fixture_schedule_complete":False,
            "fixture_schedule_covered_round_n":fixture_ledger["covered_round_n"],
            "fixture_n":fixture_ledger["fixture_n"],
            "fixture_source_errors":source_errors,
            "result_labels_read":0,
            "score_values_read":0,
            "referee_assignment_body_parsed":False,
            "sky_article_body_reacquired":False,
            "training_performed":False,
            "scoring_performed":False,
            "formal_v2_changed":False,
            "current_changed":False,
            "production_changed":False,
            "candidate_weight":0,
            "matrix_delta":0,
            "referee_oof_allowed":False,
            "next_step":registry["decision_contract"]["next_if_blocked"],
        }
        (out/"sky_pit_binding_receipt.json").write_bytes(stable_bytes(receipt))
        print(json.dumps(receipt,sort_keys=True,ensure_ascii=False))
        return receipt

    binding,witness=build_binding(registry,sky,anom,aia,fixture_ledger,resume_witness)
    witness_bytes=stable_bytes(witness)
    binding_bytes=stable_bytes(binding)
    (out/"wayback_witness_ledger.json").write_bytes(witness_bytes)
    (out/"sky_publication_pit_binding_ledger.json").write_bytes(binding_bytes)

    receipt={
        "schema_version":"football3-nova-n10-referee-sky-pit-binding-receipt-v1",
        "status":"N10_REFEREE_SKY_PIT_BINDING_COMPLETE",
        "classification":binding["classification"],
        "exact_base":registry["exact_base"],
        "registry_sha256":sha256_bytes(registry_path.read_bytes()),
        "aia_ledger_sha256":sha256_bytes(aia_raw),
        "parent_provenance":parent_prov,
        "resume_provenance":resume_prov,
        "fixture_schedule_sha256":sha256_bytes(fixture_bytes),
        "wayback_witness_sha256":sha256_bytes(witness_bytes),
        "pit_binding_sha256":sha256_bytes(binding_bytes),
        "fixture_schedule_complete":fixture_ledger["complete"],
        "fixture_schedule_covered_round_n":fixture_ledger["covered_round_n"],
        "fixture_n":fixture_ledger["fixture_n"],
        "fixture_source_error_n":len(source_errors),
        "independent_witness_report_n":witness["report_n"],
        "independent_witness_error_n":witness["error_n"],
        "pit_pass_round_n":binding["pit_pass_round_n"],
        "pit_pass_rounds":binding["pit_pass_rounds"],
        "no_capture_rounds":binding["no_capture_rounds"],
        "post_cutoff_capture_rounds":binding["post_cutoff_capture_rounds"],
        "timestamp_conflict_rounds":binding["timestamp_conflict_rounds"],
        "publication_pit_binding_complete":binding["publication_pit_binding_complete"],
        "publication_available_at_bound_complete":binding["publication_available_at_bound_complete"],
        "formal_available_at_proven_for_publication_pages":binding["formal_available_at_proven_for_publication_pages"],
        "formal_available_at_proven_for_referee_assignments":False,
        "referee_assignment_fixture_binding_complete":False,
        "raw_archived_page_body_read":False,
        "cdx_metadata_only":True,
        "schedule_raw_html_persisted":False,
        "schedule_full_visible_text_persisted":False,
        "result_labels_read":0,
        "score_values_read":0,
        "match_result_payload_read":False,
        "standings_payload_read":False,
        "player_stats_payload_read":False,
        "referee_assignment_body_parsed":False,
        "sky_article_body_reacquired":False,
        "training_performed":False,
        "scoring_performed":False,
        "formal_v2_changed":False,
        "current_changed":False,
        "production_changed":False,
        "candidate_weight":0,
        "matrix_delta":0,
        "referee_oof_allowed":False,
        "next_step":binding["next_step"],
    }
    (out/"sky_pit_binding_receipt.json").write_bytes(stable_bytes(receipt))
    print(json.dumps(receipt,sort_keys=True,ensure_ascii=False))
    return receipt

def main() -> None:
    a=argparse.ArgumentParser()
    a.add_argument("--registry",type=Path,required=True)
    a.add_argument("--aia-ledger",type=Path,required=True)
    a.add_argument("--out",type=Path,required=True)
    x=a.parse_args()
    run(x.registry,x.aia_ledger,x.out,os.environ.get("GITHUB_TOKEN",""))

if __name__=="__main__":
    main()
