#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import re
import ssl
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

class SkyVisibleTimestampError(RuntimeError):
    pass

class MarkerFound(Exception):
    pass

def req(cond: bool, msg: str) -> None:
    if not cond:
        raise SkyVisibleTimestampError(msg)

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def host_ok(url: str, suffix: str) -> bool:
    h=(urllib.parse.urlparse(url).hostname or "").lower()
    s=suffix.lower()
    return h==s or h.endswith("."+s)

def norm(s: str) -> str:
    return re.sub(r"\s+"," ",html.unescape(s or "")).strip()

def title_matches(title: str, terms: list[str]) -> bool:
    folded=norm(title).casefold()
    return all(norm(t).casefold() in folded for t in terms)

class VisibleMarkerParser(HTMLParser):
    def __init__(
        self,
        *,
        required_title_terms: list[str],
        expected_date: str,
        expected_time: str,
    ) -> None:
        super().__init__(convert_charrefs=True)
        self.required_title_terms=required_title_terms
        self.expected_date=norm(expected_date)
        self.expected_time=norm(expected_time)
        self.in_title=False
        self.title_parts: list[str]=[]
        self.title_candidates: list[str]=[]
        self.title_identity_pass=False
        self.after_title_text=""
        self.marker_found=False
        self.marker_visible_context=""
        date_pattern=re.escape(self.expected_date).replace(r"\ ",r"\s+")
        time_pattern=re.escape(self.expected_time)
        self.marker_re=re.compile(
            date_pattern + r".{0,120}?" + time_pattern,
            re.I,
        )

    def handle_starttag(self, tag: str, attrs: list[tuple[str,str|None]]) -> None:
        d={str(k).lower():(v or "") for k,v in attrs}
        tag=tag.lower()
        if tag=="title":
            self.in_title=True
            self.title_parts=[]
        elif tag=="meta":
            key=(d.get("property") or d.get("name") or "").casefold()
            if key in {"og:title","twitter:title"}:
                value=norm(d.get("content",""))
                if value:
                    self.title_candidates.append(value)
                    if title_matches(value,self.required_title_terms):
                        self.title_identity_pass=True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower()=="title":
            self.in_title=False
            title=norm(" ".join(self.title_parts))
            if title:
                self.title_candidates.append(title)
                if title_matches(title,self.required_title_terms):
                    self.title_identity_pass=True

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
            return
        if not self.title_identity_pass:
            return
        piece=norm(data)
        if not piece:
            return
        self.after_title_text=norm((self.after_title_text+" "+piece)[-4000:])
        match=self.marker_re.search(self.after_title_text)
        if match:
            self.marker_found=True
            self.marker_visible_context=match.group(0)
            raise MarkerFound()

def parse_chunks_until_marker(
    chunks: Iterable[bytes],
    *,
    required_title_terms: list[str],
    expected_date: str,
    expected_time: str,
    max_bytes: int,
) -> dict[str,Any]:
    parser=VisibleMarkerParser(
        required_title_terms=required_title_terms,
        expected_date=expected_date,
        expected_time=expected_time,
    )
    raw=bytearray()
    network_bytes=0
    found=False
    for chunk in chunks:
        if not chunk:
            break
        network_bytes+=len(chunk)
        req(network_bytes<=max_bytes,"VISIBLE_MARKER_NOT_FOUND_WITHIN_LIMIT")
        raw.extend(chunk)
        try:
            parser.feed(chunk.decode("utf-8","replace"))
        except MarkerFound:
            found=True
            break
    req(found and parser.marker_found,"VISIBLE_TIMESTAMP_MARKER_NOT_FOUND")
    req(parser.title_identity_pass,"TITLE_IDENTITY_NOT_CONFIRMED")

    time_bytes=expected_time.encode("utf-8")
    boundary=bytes(raw).rfind(time_bytes)
    req(boundary>=0,"TIME_LITERAL_RAW_BOUNDARY_NOT_FOUND")
    boundary+=len(time_bytes)
    prefix=bytes(raw[:boundary])
    overshoot=network_bytes-boundary
    req(overshoot>=0,"NEGATIVE_OVERSHOOT")

    return {
        "title_candidates":list(dict.fromkeys(parser.title_candidates)),
        "title_identity_pass":parser.title_identity_pass,
        "visible_marker_context":parser.marker_visible_context,
        "marker_found":True,
        "network_bytes_read":network_bytes,
        "prefix_boundary_bytes":boundary,
        "prefix_sha256":sha256_bytes(prefix),
        "overshoot_bytes_discarded":overshoot,
        "overshoot_parsed":False,
        "overshoot_persisted":False,
        "raw_prefix_persisted":False,
    }

def fetch_visible_marker(
    sample: dict[str,Any],
    source: dict[str,Any],
    contract: dict[str,Any],
) -> dict[str,Any]:
    url=sample["url"]
    req(host_ok(url,source["domain_suffix"]),"REQUEST_OUTSIDE_SOURCE_DOMAIN")
    rq=urllib.request.Request(url,headers={
        "User-Agent":"Football3-Nova-N10-SkyVisibleTimestamp/1.0",
        "Accept":"text/html,application/xhtml+xml",
        "Accept-Language":"it-IT,it;q=0.9,en;q=0.5",
    })
    with urllib.request.urlopen(
        rq,
        timeout=int(contract["request_timeout_seconds"]),
        context=ssl.create_default_context(),
    ) as response:
        final=response.geturl()
        req(host_ok(final,source["domain_suffix"]),"REDIRECT_OUTSIDE_SOURCE_DOMAIN")
        def chunks():
            remaining=int(contract["max_network_bytes"])
            while remaining>0:
                piece=response.read(min(1024,remaining))
                if not piece:
                    break
                remaining-=len(piece)
                yield piece
        parsed=parse_chunks_until_marker(
            chunks(),
            required_title_terms=sample["required_title_terms"],
            expected_date=sample["expected_marker_date"],
            expected_time=sample["expected_marker_time"],
            max_bytes=int(contract["max_network_bytes"]),
        )
        parsed["final_url"]=final
        parsed["content_type"]=response.headers.get("Content-Type")
        return parsed

def prefixture_pass(sample: dict[str,Any]) -> bool:
    published=dt.datetime.fromisoformat(sample["expected_display_local"])
    kickoff=dt.datetime.fromisoformat(sample["first_fixture_local"])
    return published < kickoff

def audit_sample(
    sample: dict[str,Any],
    source: dict[str,Any],
    contract: dict[str,Any],
) -> dict[str,Any]:
    parsed=fetch_visible_marker(sample,source,contract)
    before=prefixture_pass(sample)
    marker_exact=(
        sample["expected_marker_date"] in parsed["visible_marker_context"]
        and sample["expected_marker_time"] in parsed["visible_marker_context"]
    )
    sample_pass=(
        parsed["title_identity_pass"]
        and parsed["marker_found"]
        and marker_exact
        and before
    )
    return {
        "round":sample["round"],
        "source_url":sample["url"],
        "final_url":parsed["final_url"],
        "content_type":parsed["content_type"],
        "title_candidates":parsed["title_candidates"],
        "title_identity_pass":parsed["title_identity_pass"],
        "visible_marker_date":sample["expected_marker_date"],
        "visible_marker_time":sample["expected_marker_time"],
        "visible_marker_context":parsed["visible_marker_context"],
        "marker_exact_pass":marker_exact,
        "expected_display_local":sample["expected_display_local"],
        "first_fixture_local":sample["first_fixture_local"],
        "timezone":sample["timezone"],
        "publication_before_first_fixture_pass":before,
        "network_bytes_read":parsed["network_bytes_read"],
        "prefix_boundary_bytes":parsed["prefix_boundary_bytes"],
        "prefix_sha256":parsed["prefix_sha256"],
        "overshoot_bytes_discarded":parsed["overshoot_bytes_discarded"],
        "overshoot_parsed":False,
        "overshoot_persisted":False,
        "raw_prefix_persisted":False,
        "summary_text_read":False,
        "article_body_read":False,
        "referee_assignment_body_parsed":False,
        "sample_pass":sample_pass,
    }

def run(registry: Path,out: Path)->dict[str,Any]:
    p=json.loads(registry.read_text(encoding="utf-8"))
    req(p["status"]=="DESIGN_LOCKED_ZERO_LABEL_BOUNDED_PREFIX","STATUS")
    req(p["exact_base"]=="5b46bb6d30791fb3af624b9c438915bd03b8334b","EXACT_BASE")
    req(p["parent"]["source_family_closed"] is False,"SOURCE_FAMILY_ALREADY_CLOSED")
    req([s["round"] for s in p["samples"]]==[1,10,19,28,38],"FROZEN_ROUNDS")

    source=p["source"]
    pc=p["prefix_contract"]
    tc=p["timestamp_contract"]
    h=p["hard_rules"]

    req(source["acquisition_mode"]=="BOUNDED_VISIBLE_ARTICLE_HEADER_TIMESTAMP","MODE")
    req(source["article_body_read_allowed"] is False,"NO_ARTICLE_BODY_SOURCE")
    req(source["summary_text_read_allowed"] is False,"NO_SUMMARY_SOURCE")
    req(source["referee_assignment_content_read_allowed"] is False,"NO_ASSIGNMENT_SOURCE")
    req(pc["visible_timestamp_must_occur_after_title_identity"] is True,"TITLE_FIRST")
    req(pc["stop_immediately_when_timestamp_marker_confirmed"] is True,"STOP_AT_MARKER")
    req(pc["chunk_overshoot_parsed"] is False and pc["chunk_overshoot_persisted"] is False,"NO_OVERSHOOT_USE")
    req(pc["raw_prefix_persisted"] is False,"NO_RAW_PREFIX")
    req(tc["exact_frozen_marker_required"] is True and tc["exact_minute_required"] is True,"EXACT_MINUTE")
    req(tc["publication_before_first_fixture_required"] is True,"PRE_FIXTURE")
    req(tc["independent_immutable_archive_witness"] is False,"NOT_IMMUTABLE")
    req(tc["formal_available_at_proven"] is False,"NO_FORMAL_AVAILABLE_AT")

    req(h["result_labels_read"] is False and h["score_values_read"] is False,"ZERO_LABEL")
    req(h["match_payload_read"] is False and h["standings_payload_read"] is False and h["player_stats_payload_read"] is False,"NO_SPORT_PAYLOAD")
    req(h["article_body_read"] is False and h["summary_text_read"] is False,"NO_BODY")
    req(h["referee_assignment_body_parsed"] is False,"NO_ASSIGNMENT_BODY")
    req(h["raw_prefix_persisted"] is False,"NO_RAW_PREFIX_HARD")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False,"NO_MODEL")
    req(h["paid_or_secret_source_allowed"] is False,"NO_PAID_SECRET")
    req(h["candidate_weight"]==0 and h["matrix_delta"]==0,"ZERO_WEIGHT")

    reports=[]
    errors=[]
    for sample in p["samples"]:
        try:
            reports.append(audit_sample(sample,source,pc))
        except Exception as exc:
            errors.append({
                "round":sample["round"],
                "source_url":sample["url"],
                "error":f"{type(exc).__name__}:{exc}"[:500],
            })

    passed=sorted(r["round"] for r in reports if r["sample_pass"])
    all_pass=(len(passed)==5 and not errors)
    classification=(
        p["decision_contract"]["positive_classification"]
        if all_pass else p["decision_contract"]["fail_classification"]
    )
    reason=(
        "ALL_FROZEN_SKY_VISIBLE_PUBLICATION_MARKERS_CONFIRMED_PRE_FIXTURE"
        if all_pass else
        "SKY_VISIBLE_TIMESTAMP_CONTRACT_NOT_FULLY_SATISFIED"
    )

    out.mkdir(parents=True,exist_ok=True)
    receipt={
        "schema_version":"football3-nova-n10-referee-sky-visible-timestamp-receipt-v1",
        "status":"N10_REFEREE_SKY_VISIBLE_TIMESTAMP_FEASIBILITY_COMPLETE",
        "classification":classification,
        "reason":reason,
        "exact_base":p["exact_base"],
        "registry_sha256":sha256_bytes(registry.read_bytes()),
        "sample_n":5,
        "report_n":len(reports),
        "error_n":len(errors),
        "reports":reports,
        "errors":errors,
        "passed_round_n":len(passed),
        "passed_rounds":passed,
        "all_samples_pass":all_pass,
        "source_family":source["source_family"],
        "independent_contemporaneous_publisher":True,
        "source_declared_visible_publication_timestamp":all_pass,
        "independent_immutable_archive_witness":False,
        "formal_available_at_proven":False,
        "fixture_level_binding_complete":False,
        "raw_prefix_persisted":False,
        "summary_text_read":False,
        "article_body_read":False,
        "referee_assignment_body_parsed":False,
        "match_payload_read":False,
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
        "full_big5_data_ready":False,
        "referee_oof_allowed":False,
        "next_step":(
            p["decision_contract"]["next_if_positive"]
            if all_pass else p["decision_contract"]["next_if_fail"]
        ),
    }
    (out/"sky_visible_timestamp_receipt.json").write_text(
        json.dumps(receipt,indent=2,sort_keys=True,ensure_ascii=False)+"\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt,sort_keys=True,ensure_ascii=False))
    return receipt

def main():
    a=argparse.ArgumentParser()
    a.add_argument("--registry",type=Path,required=True)
    a.add_argument("--out",type=Path,required=True)
    x=a.parse_args()
    run(x.registry,x.out)

if __name__=="__main__":
    main()
