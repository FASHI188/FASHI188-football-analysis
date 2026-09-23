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
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

class SkySeasonInventoryError(RuntimeError):
    pass

class TimestampFound(Exception):
    pass

ITALIAN_MONTHS = {
    "gen": 1, "feb": 2, "mar": 3, "apr": 4, "mag": 5, "giu": 6,
    "lug": 7, "ago": 8, "set": 9, "ott": 10, "nov": 11, "dic": 12,
}
TIMESTAMP_RE = re.compile(
    r"(?<!\d)(\d{1,2})\s+(gen|feb|mar|apr|mag|giu|lug|ago|set|ott|nov|dic)\s+"
    r"(20\d{2})\s*-\s*([0-2]\d:[0-5]\d)",
    re.I,
)

def req(cond: bool, msg: str) -> None:
    if not cond:
        raise SkySeasonInventoryError(msg)

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(s or "")).strip()

def host_ok(url: str, suffix: str) -> bool:
    h=(urllib.parse.urlparse(url).hostname or "").lower()
    s=suffix.lower()
    return h==s or h.endswith("."+s)

def normalized_url(url: str) -> str:
    p=urllib.parse.urlparse(url)
    path=urllib.parse.unquote(p.path or "")
    if path.endswith("/amp"):
        path=path[:-4]
    path=path.rstrip("/") or "/"
    return urllib.parse.urlunparse(("https",(p.hostname or "").lower(),path,"","",""))

def round_signal(text: str, url: str, round_no: int) -> bool:
    combined=norm(text+" "+urllib.parse.unquote(urllib.parse.urlparse(url).path)).casefold()
    n=str(round_no)
    pats=[
        rf"(?<!\d){re.escape(n)}\s*(?:\^|ª|º|a)?\s*giornata",
        rf"giornata[-_\s]*{re.escape(n)}(?!\d)",
    ]
    if round_no==1:
        pats.append(r"\bprima\s+giornata\b")
    return any(re.search(p,combined,re.I) for p in pats)

def referee_signal(text: str, url: str, terms: list[str]) -> bool:
    combined=norm(text+" "+urllib.parse.unquote(urllib.parse.urlparse(url).path)).casefold()
    return any(t.casefold() in combined for t in terms)

def serie_a_signal(text: str, url: str) -> bool:
    p=urllib.parse.urlparse(url)
    combined=norm(text).casefold()
    return "/calcio/serie-a/" in p.path.casefold() or "serie a" in combined

class ArchiveParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url=base_url
        self.current: dict[str,Any] | None=None
        self.anchors: list[dict[str,str]]=[]
    def handle_starttag(self, tag: str, attrs: list[tuple[str,str|None]]) -> None:
        if tag.lower()!="a":
            return
        d={str(k).lower():(v or "") for k,v in attrs}
        href=d.get("href","").strip()
        if not href:
            return
        self.current={
            "href":urllib.parse.urljoin(self.base_url,href),
            "parts":[],
            "attr_text":" ".join(x for x in [d.get("title",""),d.get("aria-label","")] if x),
        }
    def handle_data(self, data: str) -> None:
        if self.current is not None:
            self.current["parts"].append(data)
    def handle_endtag(self, tag: str) -> None:
        if tag.lower()=="a" and self.current is not None:
            txt=norm(self.current["attr_text"]+" "+" ".join(self.current["parts"]))
            self.anchors.append({"href":self.current["href"],"text":txt})
            self.current=None

def fetch_bytes(url: str, *, timeout: int, limit: int, suffix: str, accept: str) -> tuple[bytes,str,dict[str,str]]:
    req(host_ok(url,suffix),"REQUEST_OUTSIDE_SOURCE_DOMAIN")
    rq=urllib.request.Request(url,headers={
        "User-Agent":"Football3-Nova-N10-SkySeasonInventory/1.0",
        "Accept":accept,
        "Accept-Language":"it-IT,it;q=0.9,en;q=0.5",
    })
    with urllib.request.urlopen(rq,timeout=timeout,context=ssl.create_default_context()) as r:
        final=r.geturl()
        req(host_ok(final,suffix),"REDIRECT_OUTSIDE_SOURCE_DOMAIN")
        data=r.read(limit+1)
        req(len(data)<=limit,"RESPONSE_TOO_LARGE")
        return data,final,{k.lower():v for k,v in r.headers.items()}

def archive_url(template: str, day: dt.date, page: int=1) -> str:
    base=template.format(yyyy=f"{day.year:04d}",mm=f"{day.month:02d}",dd=f"{day.day:02d}")
    if page<=1:
        return base
    return base+"?"+urllib.parse.urlencode({"pag":page})

def same_archive_day_page(url: str, day: dt.date) -> int | None:
    p=urllib.parse.urlparse(url)
    expected=f"/archivio/{day.year:04d}/{day.month:02d}/{day.day:02d}"
    if p.path.rstrip("/")!=expected:
        return None
    q=urllib.parse.parse_qs(p.query)
    raw=(q.get("pag") or ["1"])[0]
    try:
        n=int(raw)
    except Exception:
        return None
    return n if n>=1 else None

def parse_archive(raw: bytes, final_url: str) -> ArchiveParser:
    parser=ArchiveParser(final_url)
    parser.feed(raw.decode("utf-8","replace"))
    return parser

def fetch_archive_day(day: dt.date, source: dict[str,Any]) -> dict[str,Any]:
    timeout=int(source["request_timeout_seconds"])
    limit=int(source["archive_page_max_bytes"])
    max_pages=int(source["max_archive_pages_per_day"])
    suffix=source["domain_suffix"]
    first_url=archive_url(source["archive_url_template"],day,1)
    raw,final,headers=fetch_bytes(
        first_url,timeout=timeout,limit=limit,suffix=suffix,
        accept="text/html,application/xhtml+xml",
    )
    parsed=parse_archive(raw,final)
    pages={1}
    for a in parsed.anchors:
        n=same_archive_day_page(a["href"],day)
        if n is not None and n<=max_pages:
            pages.add(n)
    page_reports=[{
        "page":1,"url":first_url,"final_url":final,"bytes":len(raw),
        "sha256":sha256_bytes(raw),"content_type":headers.get("content-type"),
        "anchors":parsed.anchors,
    }]
    for page in sorted(pages):
        if page==1:
            continue
        u=archive_url(source["archive_url_template"],day,page)
        raw2,final2,headers2=fetch_bytes(
            u,timeout=timeout,limit=limit,suffix=suffix,
            accept="text/html,application/xhtml+xml",
        )
        parsed2=parse_archive(raw2,final2)
        page_reports.append({
            "page":page,"url":u,"final_url":final2,"bytes":len(raw2),
            "sha256":sha256_bytes(raw2),"content_type":headers2.get("content-type"),
            "anchors":parsed2.anchors,
        })
    return {
        "date":day.isoformat(),
        "page_n":len(page_reports),
        "pages":page_reports,
    }

def candidate_links(day_report: dict[str,Any], target: dict[str,Any], discovery: dict[str,Any], source: dict[str,Any]) -> list[dict[str,Any]]:
    out=[]
    seen=set()
    for page in day_report["pages"]:
        for a in page["anchors"]:
            u=a["href"]
            if not host_ok(u,source["domain_suffix"]):
                continue
            if "/archivio/" in urllib.parse.urlparse(u).path.casefold():
                continue
            if not serie_a_signal(a["text"],u):
                continue
            if not referee_signal(a["text"],u,discovery["referee_terms"]):
                continue
            if not round_signal(a["text"],u,int(target["round"])):
                continue
            nu=normalized_url(u)
            if nu in seen:
                continue
            seen.add(nu)
            out.append({
                "round":target["round"],
                "archive_date":day_report["date"],
                "archive_page":page["page"],
                "url":u,
                "normalized_url":nu,
                "archive_anchor_text":a["text"][:500],
            })
    return sorted(out,key=lambda x:(x["archive_date"],x["archive_page"],x["normalized_url"]))

def title_identity(title: str, round_no: int, referee_terms: list[str]) -> bool:
    if not serie_a_signal(title,"https://sport.sky.it/calcio/serie-a/x"):
        return False
    if not referee_signal(title,"",referee_terms):
        return False
    return round_signal(title,"",round_no)

class HeaderTimestampParser(HTMLParser):
    IGNORE={"script","style","noscript","template","svg"}
    def __init__(self, round_no: int, referee_terms: list[str], allowed_dates: set[str]) -> None:
        super().__init__(convert_charrefs=True)
        self.round_no=round_no
        self.referee_terms=referee_terms
        self.allowed_dates=allowed_dates
        self.ignore_depth=0
        self.in_title=False
        self.title_parts=[]
        self.title_candidates=[]
        self.title_identity_pass=False
        self.visible_text=""
        self.marker_context=""
        self.marker_iso_local=""
        self.marker_time=""
    def _consider_title(self, value: str) -> None:
        v=norm(value)
        if not v:
            return
        self.title_candidates.append(v)
        if title_identity(v,self.round_no,self.referee_terms):
            self.title_identity_pass=True
    def handle_starttag(self, tag: str, attrs: list[tuple[str,str|None]]) -> None:
        tag=tag.lower()
        if tag in self.IGNORE:
            self.ignore_depth+=1
            return
        d={str(k).lower():(v or "") for k,v in attrs}
        if tag=="title":
            self.in_title=True
            self.title_parts=[]
        elif tag=="meta":
            key=(d.get("property") or d.get("name") or "").casefold()
            if key in {"og:title","twitter:title"}:
                self._consider_title(d.get("content",""))
    def handle_endtag(self, tag: str) -> None:
        tag=tag.lower()
        if tag in self.IGNORE and self.ignore_depth>0:
            self.ignore_depth-=1
            return
        if tag=="title":
            self.in_title=False
            self._consider_title(" ".join(self.title_parts))
    def handle_data(self, data: str) -> None:
        if self.ignore_depth>0:
            return
        if self.in_title:
            self.title_parts.append(data)
            return
        if not self.title_identity_pass:
            return
        piece=norm(data)
        if not piece:
            return
        self.visible_text=norm((self.visible_text+" "+piece)[-4000:])
        for m in TIMESTAMP_RE.finditer(self.visible_text):
            day=int(m.group(1)); mon=ITALIAN_MONTHS[m.group(2).casefold()]; year=int(m.group(3)); tm=m.group(4)
            iso_date=f"{year:04d}-{mon:02d}-{day:02d}"
            if iso_date not in self.allowed_dates:
                continue
            self.marker_context=m.group(0)
            self.marker_iso_local=iso_date+"T"+tm+":00"
            self.marker_time=tm
            raise TimestampFound()

def fetch_candidate_header(candidate: dict[str,Any], target: dict[str,Any], source: dict[str,Any], discovery: dict[str,Any]) -> dict[str,Any]:
    max_bytes=int(source["candidate_page_max_network_bytes"])
    rq=urllib.request.Request(candidate["url"],headers={
        "User-Agent":"Football3-Nova-N10-SkySeasonInventory/1.0",
        "Accept":"text/html,application/xhtml+xml",
        "Accept-Language":"it-IT,it;q=0.9,en;q=0.5",
    })
    allowed_dates={
        (dt.date.fromisoformat(target["published_date"])+dt.timedelta(days=int(o))).isoformat()
        for o in discovery["day_offsets"]
    }
    with urllib.request.urlopen(rq,timeout=int(source["request_timeout_seconds"]),context=ssl.create_default_context()) as response:
        final=response.geturl()
        req(host_ok(final,source["domain_suffix"]),"CANDIDATE_REDIRECT_OUTSIDE_SOURCE_DOMAIN")
        parser=HeaderTimestampParser(int(target["round"]),discovery["referee_terms"],allowed_dates)
        raw=bytearray()
        network_bytes=0
        found=False
        while network_bytes < max_bytes:
            chunk=response.read(min(1024,max_bytes-network_bytes))
            if not chunk:
                break
            network_bytes+=len(chunk)
            raw.extend(chunk)
            try:
                parser.feed(chunk.decode("utf-8","replace"))
            except TimestampFound:
                found=True
                break
        req(found and parser.marker_iso_local,"VISIBLE_TIMESTAMP_NOT_FOUND_IN_ALLOWED_WINDOW")
        req(parser.title_identity_pass,"TITLE_ROUND_REFEREE_IDENTITY_NOT_CONFIRMED")
        time_literal=parser.marker_time.encode("utf-8")
        boundary=bytes(raw).rfind(time_literal)
        req(boundary>=0,"RAW_TIME_BOUNDARY_NOT_FOUND")
        boundary+=len(time_literal)
        prefix=bytes(raw[:boundary])
        return {
            **candidate,
            "final_url":final,
            "final_normalized_url":normalized_url(final),
            "title_candidates":list(dict.fromkeys(parser.title_candidates)),
            "title_identity_pass":True,
            "visible_marker_context":parser.marker_context,
            "published_local":parser.marker_iso_local,
            "timezone":"Europe/Rome",
            "network_bytes_read":network_bytes,
            "prefix_boundary_bytes":boundary,
            "prefix_sha256":sha256_bytes(prefix),
            "overshoot_bytes_discarded":network_bytes-boundary,
            "raw_prefix_persisted":False,
            "summary_text_read":False,
            "article_body_read":False,
            "referee_assignment_body_parsed":False,
        }

def candidate_preference(c: dict[str,Any]) -> tuple[Any,...]:
    title=" ".join(c.get("title_candidates") or [])
    signal=(title+" "+c.get("final_normalized_url","")).casefold()
    strong=0 if ("designaz" in signal or "arbitr" in signal) else 1
    return (
        c["published_local"],
        strong,
        len(c["final_normalized_url"]),
        c["final_normalized_url"],
    )

def run(registry: Path, ledger: Path, out: Path) -> dict[str,Any]:
    p=json.loads(registry.read_text(encoding="utf-8"))
    req(p["status"]=="DESIGN_LOCKED_ZERO_LABEL_INDEX_PLUS_BOUNDED_HEADER","STATUS")
    req(p["exact_base"]=="31c756e256b8d9b4ffbe4411b9dedec2a350d9d0","EXACT_BASE")
    req(p["parent"]["source_family_closed"] is False,"SOURCE_FAMILY_ALREADY_CLOSED")
    req(p["parent"]["canonical_round_n"]==38,"PARENT_ROUND_N")
    source=p["source"]; discovery=p["discovery_contract"]; header=p["header_contract"]; hard=p["hard_rules"]
    req(discovery["day_offsets"]==[0,1,2],"FROZEN_DAY_OFFSETS")
    req(discovery["no_guessed_article_urls"] is True,"NO_GUESSED_URLS")
    req(discovery["no_search_engine_results_in_runtime"] is True,"NO_SEARCH_RUNTIME")
    req(header["stop_immediately_at_first_valid_timestamp_after_title"] is True,"STOP_AT_TIMESTAMP")
    req(header["raw_prefix_persisted"] is False and header["article_body_read"] is False,"NO_BODY")
    req(hard["result_labels_read"] is False and hard["score_values_read"] is False,"ZERO_LABEL")
    req(hard["match_payload_read"] is False and hard["standings_payload_read"] is False and hard["player_stats_payload_read"] is False,"NO_SPORT_PAYLOAD")
    req(hard["article_body_read"] is False and hard["summary_text_read"] is False,"NO_ARTICLE_BODY")
    req(hard["referee_assignment_body_parsed"] is False,"NO_ASSIGNMENT_BODY")
    req(hard["training_allowed"] is False and hard["scoring_allowed"] is False,"NO_MODEL")
    req(hard["paid_or_secret_source_allowed"] is False,"NO_PAID_SECRET")
    req(hard["candidate_weight"]==0 and hard["matrix_delta"]==0,"ZERO_WEIGHT")

    l=json.loads(ledger.read_text(encoding="utf-8"))
    req(l["schema_version"]=="football3-nova-n10-aia-canonical-round-ledger-v1","LEDGER_SCHEMA")
    req(l["competition"]=="Serie_A" and l["season"]=="2022/23","LEDGER_IDENTITY")
    req(l["canonical_round_n"]==38 and len(l["rows"])==38,"LEDGER_COVERAGE")
    req([int(x["round"]) for x in l["rows"]]==list(range(1,39)),"LEDGER_ROUNDS")

    target_days: dict[int,list[dt.date]]={}
    unique_days=set()
    for row in l["rows"]:
        base=dt.date.fromisoformat(row["published_date"])
        days=[base+dt.timedelta(days=int(o)) for o in discovery["day_offsets"]]
        target_days[int(row["round"])]=days
        unique_days.update(days)

    day_reports: dict[str,Any]={}
    day_errors: dict[str,str]={}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs={ex.submit(fetch_archive_day,d,source):d for d in sorted(unique_days)}
        for fut in as_completed(futs):
            d=futs[fut]
            try:
                day_reports[d.isoformat()]=fut.result()
            except Exception as exc:
                day_errors[d.isoformat()]=f"{type(exc).__name__}:{exc}"[:500]

    round_reports=[]
    valid_total=0
    conflict_rounds=[]
    gap_rounds=[]
    for row in l["rows"]:
        rnd=int(row["round"])
        discovered=[]
        for d in target_days[rnd]:
            rep=day_reports.get(d.isoformat())
            if rep:
                discovered.extend(candidate_links(rep,row,discovery,source))
        uniq={}
        for c in discovered:
            uniq[c["normalized_url"]]=c
        discovered=sorted(uniq.values(),key=lambda x:(x["archive_date"],x["normalized_url"]))

        validated=[]
        validation_errors=[]
        for c in discovered:
            try:
                validated.append(fetch_candidate_header(c,row,source,discovery))
            except Exception as exc:
                validation_errors.append({
                    "url":c["url"],
                    "error":f"{type(exc).__name__}:{exc}"[:500],
                })
        by_final={}
        for c in validated:
            by_final[c["final_normalized_url"]]=c
        validated=sorted(by_final.values(),key=candidate_preference)
        valid_total+=len(validated)
        canonical=validated[0] if validated else None
        if len(validated)>1:
            conflict_rounds.append(rnd)
        if not validated:
            gap_rounds.append(rnd)
        round_reports.append({
            "round":rnd,
            "aia_published_date":row["published_date"],
            "archive_days":[d.isoformat() for d in target_days[rnd]],
            "archive_day_success_n":sum(1 for d in target_days[rnd] if d.isoformat() in day_reports),
            "archive_day_error_n":sum(1 for d in target_days[rnd] if d.isoformat() in day_errors),
            "discovered_candidate_n":len(discovered),
            "valid_candidate_n":len(validated),
            "valid_candidates":validated,
            "validation_errors":validation_errors,
            "canonical_candidate":canonical,
            "status":"COVERED" if canonical else "GAP",
            "conflict":len(validated)>1,
        })

    covered=[r["round"] for r in round_reports if r["canonical_candidate"] is not None]
    coverage=len(covered)/38.0
    if len(covered)==38:
        classification=p["decision_contract"]["positive_classification"]
        next_step=p["decision_contract"]["next_if_positive"]
    elif covered:
        classification=p["decision_contract"]["partial_classification"]
        next_step=p["decision_contract"]["next_if_partial"]
    else:
        classification=p["decision_contract"]["fail_classification"]
        next_step=p["decision_contract"]["next_if_fail"]

    inventory={
        "schema_version":"football3-nova-n10-referee-sky-season-inventory-v1",
        "competition":"Serie_A",
        "season":"2022/23",
        "source_family":"CONTEMPORANEOUS_NEWS_PUBLICATION_WITNESS",
        "coverage_round_n":len(covered),
        "coverage":coverage,
        "covered_rounds":covered,
        "gap_rounds":gap_rounds,
        "conflict_rounds":conflict_rounds,
        "rows":[{
            "round":r["round"],
            "status":r["status"],
            "conflict":r["conflict"],
            "canonical_candidate":r["canonical_candidate"],
            "valid_candidate_n":r["valid_candidate_n"],
        } for r in round_reports],
    }
    inventory_bytes=(json.dumps(inventory,indent=2,sort_keys=True,ensure_ascii=False)+"\n").encode("utf-8")
    out.mkdir(parents=True,exist_ok=True)
    (out/"sky_season_publication_inventory.json").write_bytes(inventory_bytes)

    receipt={
        "schema_version":"football3-nova-n10-referee-sky-season-inventory-receipt-v1",
        "status":"N10_REFEREE_SKY_SEASON_PUBLICATION_INVENTORY_COMPLETE",
        "classification":classification,
        "exact_base":p["exact_base"],
        "registry_sha256":sha256_bytes(registry.read_bytes()),
        "ledger_file_sha256":sha256_bytes(ledger.read_bytes()),
        "inventory_sha256":sha256_bytes(inventory_bytes),
        "round_n":38,
        "unique_archive_day_n":len(unique_days),
        "archive_day_success_n":len(day_reports),
        "archive_day_error_n":len(day_errors),
        "archive_day_errors":day_errors,
        "covered_round_n":len(covered),
        "coverage":coverage,
        "covered_rounds":covered,
        "gap_rounds":gap_rounds,
        "conflict_rounds":conflict_rounds,
        "valid_candidate_total_n":valid_total,
        "round_reports":round_reports,
        "raw_archive_html_persisted":False,
        "raw_article_prefix_persisted":False,
        "summary_text_read":False,
        "article_body_read":False,
        "referee_assignment_body_parsed":False,
        "independent_immutable_archive_witness":False,
        "formal_available_at_proven":False,
        "fixture_level_binding_complete":False,
        "full_big5_data_ready":False,
        "referee_oof_allowed":False,
        "result_labels_read":0,
        "score_values_read":0,
        "match_payload_read":False,
        "standings_payload_read":False,
        "player_stats_payload_read":False,
        "training_performed":False,
        "scoring_performed":False,
        "formal_v2_changed":False,
        "current_changed":False,
        "production_changed":False,
        "candidate_weight":0,
        "matrix_delta":0,
        "next_step":next_step,
    }
    (out/"sky_season_inventory_receipt.json").write_text(
        json.dumps(receipt,indent=2,sort_keys=True,ensure_ascii=False)+"\n",encoding="utf-8"
    )
    print(json.dumps(receipt,sort_keys=True,ensure_ascii=False))
    return receipt

def main() -> None:
    a=argparse.ArgumentParser()
    a.add_argument("--registry",type=Path,required=True)
    a.add_argument("--ledger",type=Path,required=True)
    a.add_argument("--out",type=Path,required=True)
    x=a.parse_args()
    run(x.registry,x.ledger,x.out)

if __name__=="__main__":
    main()
