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
from pathlib import Path
from typing import Any

class InventoryError(RuntimeError):
    pass

def req(c: bool, m: str) -> None:
    if not c:
        raise InventoryError(m)

def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def domain_ok(url: str, suffix: str) -> bool:
    h=(urllib.parse.urlparse(url).hostname or "").lower()
    s=suffix.lower()
    return h==s or h.endswith("."+s)

TAG_RE=re.compile(r"<[^>]+>",re.S)
SPACE_RE=re.compile(r"\s+")
A_RE=re.compile(r"""<a\b([^>]*)>(.*?)</a>""",re.I|re.S)
HREF_RE=re.compile(r"""\bhref\s*=\s*["']([^"']+)["']""",re.I)
DATE_RE=re.compile(r"(?<!\d)([0-3]\d/[01]\d/20\d{2})(?!\d)")

def clean_text(s: str) -> str:
    return SPACE_RE.sub(" ", html.unescape(TAG_RE.sub(" ",s))).strip()

def normalize_title(s: str) -> str:
    return clean_text(s).casefold()

def fetch_page(url: str, timeout: int, limit: int, suffix: str) -> tuple[bytes,str,dict[str,str]]:
    req(domain_ok(url,suffix),"NON_OFFICIAL_PAGE")
    rq=urllib.request.Request(url,headers={
        "User-Agent":"Football3-Nova-N10-AIAIndexInventory/2.0",
        "Accept":"text/html,application/xhtml+xml",
    })
    with urllib.request.urlopen(rq,timeout=timeout,context=ssl.create_default_context()) as r:
        final=r.geturl()
        req(domain_ok(final,suffix),"REDIRECT_OUTSIDE_OFFICIAL")
        raw=r.read(limit+1)
        req(len(raw)<=limit,"CATEGORY_PAGE_TOO_LARGE")
        return raw,final,{k.lower():v for k,v in r.headers.items()}

def same_category_pagination(url: str, base_url: str, suffix: str, category_param: str, category_value: str, pagination_param: str) -> int | None:
    u=urllib.parse.urljoin(base_url,url)
    if not domain_ok(u,suffix):
        return None
    p=urllib.parse.urlparse(u)
    base_path=urllib.parse.urlparse(base_url).path.rstrip("/")
    if p.path.rstrip("/") != base_path:
        return None
    qs=urllib.parse.parse_qs(p.query,keep_blank_values=True)
    if qs.get(category_param,[""])[0] != category_value:
        return None
    vals=qs.get(pagination_param)
    if not vals or len(vals)!=1:
        return None
    try:
        n=int(vals[0])
    except Exception:
        return None
    return n if n>=0 else None

def canonical_page_url(base_url: str, n: int, category_param: str, category_value: str, pagination_param: str) -> str:
    p=urllib.parse.urlparse(base_url)
    q=urllib.parse.urlencode([(pagination_param,str(n)),(category_param,category_value)])
    return urllib.parse.urlunparse((p.scheme,p.netloc,p.path,"",q,""))

def discover_pagination(raw: bytes, final_url: str, suffix: str, category_param: str, category_value: str, pagination_param: str) -> list[str]:
    s=raw.decode("utf-8","replace")
    found: dict[int,str]={}
    for m in A_RE.finditer(s):
        hm=HREF_RE.search(m.group(1))
        if not hm:
            continue
        u=urllib.parse.urljoin(final_url,html.unescape(hm.group(1)))
        n=same_category_pagination(u,final_url,suffix,category_param,category_value,pagination_param)
        if n is not None:
            found[n]=canonical_page_url(final_url,n,category_param,category_value,pagination_param)
    return [found[n] for n in sorted(found)]

def title_allowed(title: str, required: list[str], excluded: list[str]) -> bool:
    t=normalize_title(title)
    return all(x.casefold() in t for x in required) and not any(x.casefold() in t for x in excluded)

def parse_round(title: str, patterns: list[str]) -> int | None:
    t=clean_text(title)
    for pat in patterns:
        m=re.search(pat,t,re.I)
        if m:
            n=int(m.group(1))
            if 1<=n<=60:
                return n
    return None

def nearest_date(s: str, anchor_start: int, anchor_end: int, radius: int=900) -> str | None:
    lo=max(0,anchor_start-radius)
    hi=min(len(s),anchor_end+radius)
    center=(anchor_start+anchor_end)//2
    candidates=[]
    for m in DATE_RE.finditer(s,lo,hi):
        dist=min(abs(m.start()-center),abs(m.end()-center))
        candidates.append((dist,m.group(1)))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]

def parse_date_iso(v: str) -> str:
    return dt.datetime.strptime(v,"%d/%m/%Y").date().isoformat()

def extract_target_cards(
    raw: bytes,
    final_url: str,
    suffix: str,
    target: dict[str,Any],
    page_sha: str
) -> list[dict[str,Any]]:
    s=raw.decode("utf-8","replace")
    rows=[]
    for m in A_RE.finditer(s):
        hm=HREF_RE.search(m.group(1))
        if not hm:
            continue
        link=urllib.parse.urljoin(final_url,html.unescape(hm.group(1)))
        if not domain_ok(link,suffix):
            continue
        path=urllib.parse.urlparse(link).path.casefold()
        if "/news/" not in path:
            continue
        title=clean_text(m.group(2))
        if not title_allowed(title,target["required_title_terms"],target["excluded_title_terms"]):
            continue
        date_text=nearest_date(s,m.start(),m.end())
        if not date_text:
            continue
        try:
            date_iso=parse_date_iso(date_text)
        except Exception:
            continue
        rnd=parse_round(title,target["round_patterns"])
        rows.append({
            "title":title[:500],
            "published_date":date_iso,
            "link":link,
            "round":rnd,
            "source_page":final_url,
            "source_page_sha256":page_sha,
        })
    uniq={}
    for r in rows:
        uniq[(r["link"],r["published_date"],r["title"])]=r
    return sorted(uniq.values(),key=lambda r:(r["published_date"],r["round"] or 99,r["link"]),reverse=True)

def parse_all_visible_dates(raw: bytes) -> list[str]:
    s=raw.decode("utf-8","replace")
    out=[]
    for m in DATE_RE.finditer(s):
        try:
            out.append(parse_date_iso(m.group(1)))
        except Exception:
            pass
    return sorted(set(out))

def page_num(url: str, pagination_param: str) -> int:
    qs=urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    try:
        return int(qs.get(pagination_param,["0"])[0])
    except Exception:
        return 0

def run(registry: Path,out: Path,timeout: int=20)->dict[str,Any]:
    p=json.loads(registry.read_text(encoding="utf-8"))
    req(p["status"]=="DESIGN_LOCKED_ZERO_LABEL","STATUS")
    req(p["exact_base"]=="49d3e99d18f98803cef43fe79fc2e0bf738a9a59","EXACT_BASE")
    h=p["hard_rules"]; src=p["source"]; target=p["target"]; cc=p["crawl_contract"]; dc=p["date_contract"]; dec=p["decision_contract"]
    req(h["result_labels_read"] is False and h["score_values_read"] is False,"ZERO_LABEL")
    req(h["appointment_names_parsed"] is False,"NO_APPOINTMENT_NAMES")
    req(h["article_body_read"] is False and h["article_snippet_persisted"] is False,"NO_ARTICLE_CONTENT")
    req(h["match_payload_read"] is False and h["standings_payload_read"] is False and h["player_stats_payload_read"] is False,"NO_SPORT_PAYLOAD")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False,"NO_MODEL")
    req(h["paid_or_secret_source_allowed"] is False,"NO_SECRET")
    req(h["candidate_weight"]==0 and h["matrix_delta"]==0,"ZERO_WEIGHT")
    req(cc["mechanically_discovered_pagination_only"] is True and cc["blind_page_number_generation"] is False,"MECHANICAL_PAGINATION")
    req(cc["pagination_normalization"]=="MECHANICALLY_DISCOVER_P_VALUE_THEN_CANONICALIZE_TO_P_AND_C_ONLY","PAGINATION_NORMALIZATION")
    req(cc["article_body_fetch_allowed"] is False and cc["snippet_persisted"] is False,"METADATA_ONLY")
    req(dc["independent_immutable_archive_witness"] is False and dc["formal_available_at_proven"] is False,"NO_OVERCLAIM")
    req(dec["referee_oof_allowed"] is False,"NO_OOF")

    max_pages=int(cc["max_pages"])
    start=src["start_url"]
    queue=[start]
    discovered={start}
    fetched=set()
    page_reports=[]
    rows=[]
    errors=[]
    target_seen=False
    stopped_old=False

    while queue and len(fetched)<max_pages:
        queue.sort(key=lambda u:page_num(u,src["pagination_param"]))
        u=queue.pop(0)
        if u in fetched:
            continue
        fetched.add(u)
        try:
            raw,final,headers=fetch_page(u,timeout,2500000,src["allowed_domain_suffix"])
            page_sha=sha256_bytes(raw)
            cards=extract_target_cards(raw,final,src["allowed_domain_suffix"],target,page_sha)
            dates=parse_all_visible_dates(raw)
            season_cards=[r for r in cards if target["season_start"]<=r["published_date"]<=target["season_end"]]
            rows.extend(season_cards)
            if season_cards:
                target_seen=True
            next_urls=discover_pagination(
                raw,final,src["allowed_domain_suffix"],
                src["category_param"],src["category_value"],src["pagination_param"]
            )
            for nu in next_urls:
                if nu not in discovered:
                    discovered.add(nu)
                    queue.append(nu)
            page_reports.append({
                "requested_url":u,
                "final_url":final,
                "page_number":page_num(final,src["pagination_param"]),
                "bytes":len(raw),
                "sha256":page_sha,
                "target_card_n":len(cards),
                "target_season_card_n":len(season_cards),
                "visible_date_min":min(dates) if dates else None,
                "visible_date_max":max(dates) if dates else None,
                "mechanically_discovered_pagination_n":len(next_urls),
                "content_type":headers.get("content-type"),
            })
            if (
                cc["stop_after_page_all_items_older_than_season_start_once_target_seen"]
                and target_seen and dates and max(dates)<target["season_start"]
            ):
                stopped_old=True
                break
        except Exception as e:
            errors.append({"url":u,"error":f"{type(e).__name__}:{e}"[:500]})

    uniq={}
    for r in rows:
        uniq[(r["link"],r["published_date"],r["title"])]=r
    rows=sorted(uniq.values(),key=lambda r:(r["round"] or 99,r["published_date"],r["link"]))

    round_map={}
    for r in rows:
        rnd=r.get("round")
        if isinstance(rnd,int) and 1<=rnd<=int(target["expected_rounds"]):
            round_map.setdefault(rnd,[]).append(r)
    rounds=sorted(round_map)
    expected=list(range(1,int(target["expected_rounds"])+1))
    missing=sorted(set(expected)-set(rounds))
    duplicate_rounds=sorted(k for k,v in round_map.items() if len(v)>1)
    complete=rounds==expected
    positive=bool(rows)
    classification="POSITIVE_SIGNAL_SOURCE_FEASIBILITY" if positive else "STOP_DATA_COVERAGE"

    inventory={
        "schema_version":"football3-nova-n10-referee-aia-index-inventory-v2",
        "competition":target["competition"],
        "season":target["season"],
        "entry_n":len(rows),
        "rounds_covered":rounds,
        "round_count":len(rounds),
        "expected_round_count":len(expected),
        "missing_rounds":missing,
        "duplicate_rounds":duplicate_rounds,
        "round_inventory_complete":complete,
        "publication_semantics":dc["publication_semantics"],
        "independent_immutable_archive_witness":False,
        "formal_available_at_proven":False,
        "rows":rows,
    }
    inventory_raw=json.dumps(inventory,sort_keys=True,ensure_ascii=False,separators=(",",":")).encode()
    inventory["inventory_sha256"]=sha256_bytes(inventory_raw)

    out.mkdir(parents=True,exist_ok=True)
    (out/"aia_target_season_index_inventory.json").write_text(json.dumps(inventory,indent=2,sort_keys=True,ensure_ascii=False)+"\n",encoding="utf-8")
    receipt={
        "schema_version":"football3-nova-n10-referee-aia-index-inventory-receipt-v2",
        "status":"N10_REFEREE_AIA_INDEX_INVENTORY_V2_COMPLETE",
        "classification":classification,
        "exact_base":p["exact_base"],
        "registry_sha256":sha256_bytes(registry.read_bytes()),
        "reopen_due_changed_external_reachability":True,
        "page_fetch_n":len(page_reports),
        "page_reports":page_reports,
        "error_n":len(errors),
        "errors":errors,
        "mechanically_discovered_page_n":len(discovered),
        "stopped_after_all_visible_dates_older_than_season_start":stopped_old,
        "target_season_entry_n":len(rows),
        "rounds_covered":rounds,
        "round_count":len(rounds),
        "missing_rounds":missing,
        "duplicate_rounds":duplicate_rounds,
        "target_season_round_inventory_complete":complete,
        "inventory_sha256":inventory["inventory_sha256"],
        "article_body_read":False,
        "article_snippet_persisted":False,
        "appointment_names_parsed":False,
        "independent_immutable_archive_witness":False,
        "formal_available_at_proven":False,
        "fixture_level_binding_complete":False,
        "full_big5_data_ready":False,
        "referee_oof_allowed":False,
        "result_labels_read":0,
        "score_values_read":0,
        "training_performed":False,
        "scoring_performed":False,
        "formal_v2_changed":False,
        "current_changed":False,
        "production_changed":False,
        "candidate_weight":0,
        "matrix_delta":0,
        "next_step":(
            "PRESERVE_POSITIVE_INDEX_SIGNAL; IF_COMPLETE_BUILD_SEPARATE_ZERO_LABEL_FIXTURE_PIT_BINDING_AND_INDEPENDENT_AVAILABILITY_WITNESS; IF_PARTIAL_CLOSE_MISSING_ROUNDS_WITH_NEW_LEGAL_FREE_METADATA_SOURCE"
            if positive else
            "STOP_AIA_INDEX_INVENTORY_ROUTE_AND_CONTINUE_ONLY_WITH_NEW_LEGAL_FREE_SOURCE"
        ),
    }
    (out/"aia_index_inventory_receipt.json").write_text(json.dumps(receipt,indent=2,sort_keys=True,ensure_ascii=False)+"\n",encoding="utf-8")
    print(json.dumps(receipt,sort_keys=True,ensure_ascii=False))
    return receipt

def main():
    a=argparse.ArgumentParser()
    a.add_argument("--registry",type=Path,required=True)
    a.add_argument("--out",type=Path,required=True)
    a.add_argument("--timeout",type=int,default=20)
    x=a.parse_args()
    run(x.registry,x.out,x.timeout)

if __name__=="__main__":
    main()
