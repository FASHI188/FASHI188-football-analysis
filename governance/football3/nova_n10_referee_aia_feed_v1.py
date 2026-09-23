#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import hashlib
import json
import re
import ssl
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

class FeedDiscoveryError(RuntimeError):
    pass

def req(c: bool, m: str) -> None:
    if not c:
        raise FeedDiscoveryError(m)

def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

HEAD_LIMIT=262144
HEAD_CLOSE=re.compile(br"</head\s*>",re.I)
LINK_TAG_RE=re.compile(r"<link\b[^>]*>",re.I)
ATTR_RE=re.compile(r"""([:\w-]+)\s*=\s*["']([^"']*)["']""",re.I)

def domain_ok(url: str, suffix: str) -> bool:
    h=(urllib.parse.urlparse(url).hostname or "").lower()
    s=suffix.lower()
    return h==s or h.endswith("."+s)

def fetch_head_html(url: str, timeout: int, suffix: str) -> tuple[bytes,str,dict[str,str]]:
    req(domain_ok(url,suffix),"NON_OFFICIAL_ENTRY")
    rq=urllib.request.Request(url,headers={
        "User-Agent":"Football3-Nova-N10-AIAFeed/1.0",
        "Accept":"text/html,application/xhtml+xml",
    })
    with urllib.request.urlopen(rq,timeout=timeout,context=ssl.create_default_context()) as r:
        final=r.geturl()
        req(domain_ok(final,suffix),"ENTRY_REDIRECT_OUTSIDE_OFFICIAL")
        buf=bytearray()
        while len(buf)<HEAD_LIMIT:
            chunk=r.read(min(8192,HEAD_LIMIT-len(buf)))
            if not chunk:
                break
            buf.extend(chunk)
            m=HEAD_CLOSE.search(buf)
            if m:
                return bytes(buf[:m.end()]),final,{k.lower():v for k,v in r.headers.items()}
    raise FeedDiscoveryError("HEAD_BOUNDARY_NOT_FOUND")

def discover_feed_links(head: bytes, base_url: str, suffix: str, allowed_types: list[str]) -> list[dict[str,str]]:
    s=head.decode("utf-8","replace")
    out=[]
    allowed={x.casefold() for x in allowed_types}
    for m in LINK_TAG_RE.finditer(s):
        attrs={k.casefold():v for k,v in ATTR_RE.findall(m.group(0))}
        rel={x.casefold() for x in re.split(r"\s+",attrs.get("rel","").strip()) if x}
        typ=attrs.get("type","").casefold()
        href=attrs.get("href")
        if "alternate" not in rel or typ not in allowed or not href:
            continue
        u=urllib.parse.urljoin(base_url,href)
        if domain_ok(u,suffix):
            out.append({"url":u,"type":typ,"title":attrs.get("title","")})
    uniq={}
    for x in out:
        uniq[(x["url"],x["type"])]=x
    return sorted(uniq.values(),key=lambda x:(x["url"],x["type"]))

def fetch_feed(url: str, timeout: int, limit: int, suffix: str) -> tuple[bytes,str,dict[str,str]]:
    req(domain_ok(url,suffix),"NON_OFFICIAL_FEED")
    rq=urllib.request.Request(url,headers={
        "User-Agent":"Football3-Nova-N10-AIAFeed/1.0",
        "Accept":"application/rss+xml,application/atom+xml,application/feed+json,application/xml,text/xml,application/json;q=0.9,*/*;q=0.1",
    })
    with urllib.request.urlopen(rq,timeout=timeout,context=ssl.create_default_context()) as r:
        final=r.geturl()
        req(domain_ok(final,suffix),"FEED_REDIRECT_OUTSIDE_OFFICIAL")
        data=r.read(limit+1)
        req(len(data)<=limit,"FEED_TOO_LARGE")
        return data,final,{k.lower():v for k,v in r.headers.items()}

def local_name(tag: str) -> str:
    return tag.rsplit("}",1)[-1].casefold()

def child_text(el: ET.Element, names: set[str]) -> str | None:
    for c in list(el):
        if local_name(c.tag) in names:
            if c.text:
                return " ".join(c.text.split())
    return None

def parse_date(v: str | None) -> str | None:
    if not v:
        return None
    s=v.strip()
    try:
        x=dt.datetime.fromisoformat(s.replace("Z","+00:00"))
        if x.tzinfo is None:
            x=x.replace(tzinfo=dt.timezone.utc)
        return x.astimezone(dt.timezone.utc).isoformat()
    except Exception:
        pass
    try:
        x=email.utils.parsedate_to_datetime(s)
        if x.tzinfo is None:
            x=x.replace(tzinfo=dt.timezone.utc)
        return x.astimezone(dt.timezone.utc).isoformat()
    except Exception:
        return None

def parse_xml_feed(raw: bytes) -> tuple[str,list[dict[str,Any]]]:
    root=ET.fromstring(raw)
    kind=local_name(root.tag)
    items=[]
    if kind=="rss":
        nodes=[x for x in root.iter() if local_name(x.tag)=="item"]
        for n in nodes:
            items.append({
                "title":child_text(n,{"title"}),
                "link":child_text(n,{"link"}),
                "published_at":parse_date(child_text(n,{"pubdate","published"})),
                "updated_at":parse_date(child_text(n,{"updated"})),
                "guid":child_text(n,{"guid","id"}),
            })
        return "rss",items
    if kind=="feed":
        nodes=[x for x in list(root) if local_name(x.tag)=="entry"]
        for n in nodes:
            link=None
            for c in list(n):
                if local_name(c.tag)=="link":
                    rel=(c.attrib.get("rel") or "alternate").casefold()
                    href=c.attrib.get("href")
                    if rel=="alternate" and href:
                        link=href
                        break
            items.append({
                "title":child_text(n,{"title"}),
                "link":link,
                "published_at":parse_date(child_text(n,{"published"})),
                "updated_at":parse_date(child_text(n,{"updated"})),
                "guid":child_text(n,{"id"}),
            })
        return "atom",items
    raise FeedDiscoveryError("UNSUPPORTED_XML_FEED_ROOT:"+kind)

def parse_json_feed(raw: bytes) -> tuple[str,list[dict[str,Any]]]:
    x=json.loads(raw.decode("utf-8"))
    req(isinstance(x,dict) and isinstance(x.get("items"),list),"JSON_FEED_SHAPE")
    items=[]
    for it in x["items"]:
        if not isinstance(it,dict):
            continue
        items.append({
            "title":it.get("title"),
            "link":it.get("url") or it.get("external_url"),
            "published_at":parse_date(it.get("date_published")),
            "updated_at":parse_date(it.get("date_modified")),
            "guid":str(it.get("id")) if it.get("id") is not None else None,
        })
    return "jsonfeed",items

def parse_feed(raw: bytes, content_type: str | None) -> tuple[str,list[dict[str,Any]]]:
    c=(content_type or "").casefold()
    if "json" in c or raw.lstrip().startswith(b"{"):
        return parse_json_feed(raw)
    return parse_xml_feed(raw)

def normalize_item(item: dict[str,Any], feed_url: str, suffix: str) -> dict[str,Any]:
    link=item.get("link")
    if isinstance(link,str) and link:
        link=urllib.parse.urljoin(feed_url,link)
        if not domain_ok(link,suffix):
            link=None
    title=item.get("title")
    if isinstance(title,str):
        title=" ".join(title.split())[:500]
    return {
        "title":title,
        "link":link,
        "published_at":item.get("published_at"),
        "updated_at":item.get("updated_at"),
        "guid":item.get("guid"),
    }

def target_match(item: dict[str,Any], target: dict[str,Any]) -> bool:
    title=(item.get("title") or "").casefold()
    link=(item.get("link") or "").casefold()
    slug=target["target_slug_term"].casefold()
    if slug and slug in link:
        return True
    return all(term.casefold() in title for term in target["frozen_title_terms"])

def target_date_match(item: dict[str,Any], target_date: str) -> bool:
    for k in ("published_at","updated_at"):
        v=item.get(k)
        if isinstance(v,str) and v[:10]==target_date:
            return True
    return False

def run(registry: Path,out: Path,timeout: int=20)->dict[str,Any]:
    p=json.loads(registry.read_text(encoding="utf-8"))
    req(p["status"]=="DESIGN_LOCKED_ZERO_LABEL","STATUS")
    req(p["exact_base"]=="55bf455f9330f0a85e96bd5d49ffb270d69c5578","EXACT_BASE")
    h=p["hard_rules"]
    req(h["result_labels_read"] is False and h["score_values_read"] is False,"ZERO_LABEL")
    req(h["match_payload_read"] is False and h["standings_payload_read"] is False and h["player_stats_payload_read"] is False,"NO_SPORT_PAYLOAD")
    req(h["article_body_read"] is False and h["feed_item_body_read"] is False,"NO_BODY")
    req(h["hidden_endpoint_guessing_allowed"] is False,"NO_ENDPOINT_GUESSING")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False,"NO_MODEL")
    req(h["paid_or_secret_source_allowed"] is False,"NO_SECRET")
    req(h["candidate_weight"]==0 and h["matrix_delta"]==0,"ZERO_WEIGHT")
    ac=p["acquisition_contract"]; src=p["source"]; target=p["target"]
    req(src["discovery_mode"]=="HTML_HEAD_REL_ALTERNATE_ONLY","DISCOVERY_MODE")
    req(ac["html_scope"]=="HEAD_ONLY_THROUGH_FIRST_CLOSING_HEAD","HEAD_ONLY")
    req(ac["recursive_feed_discovery"] is False,"NO_RECURSION")

    head_reports=[]; links=[]; errors=[]
    for entry in src["entry_urls"]:
        try:
            head,final,headers=fetch_head_html(entry,timeout,src["allowed_domain_suffix"])
            found=discover_feed_links(head,final,src["allowed_domain_suffix"],ac["allowed_feed_types"])
            links.extend(found)
            head_reports.append({
                "entry_url":entry,"final_url":final,"head_bytes":len(head),
                "head_sha256":sha256_bytes(head),"discovered_feed_n":len(found),
                "discovered_feeds":found,"content_type":headers.get("content-type")
            })
        except Exception as e:
            errors.append({"stage":"head","url":entry,"error":f"{type(e).__name__}:{e}"[:400]})

    uniq={}
    for x in links:
        uniq[(x["url"],x["type"])]=x
    feeds=sorted(uniq.values(),key=lambda x:(x["url"],x["type"]))[:int(ac["max_feed_n"])]

    feed_reports=[]; all_items=[]
    for f in feeds:
        try:
            raw,final,headers=fetch_feed(f["url"],timeout,int(ac["max_feed_bytes_each"]),src["allowed_domain_suffix"])
            kind,items=parse_feed(raw,headers.get("content-type"))
            safe=[normalize_item(it,final,src["allowed_domain_suffix"]) for it in items]
            all_items.extend([{**it,"feed_url":final} for it in safe])
            feed_reports.append({
                "source_url":f["url"],"final_url":final,"declared_type":f["type"],
                "parsed_kind":kind,"bytes":len(raw),"sha256":sha256_bytes(raw),
                "item_n":len(safe),"content_type":headers.get("content-type")
            })
        except Exception as e:
            errors.append({"stage":"feed","url":f["url"],"error":f"{type(e).__name__}:{e}"[:400]})

    target_items=[it for it in all_items if target_match(it,target)]
    target_pass_items=[it for it in target_items if target_date_match(it,target["target_publication_date"])]
    source_feasible=bool(feed_reports and any(r["item_n"]>0 for r in feed_reports))
    target_pass=bool(target_pass_items)
    classification="POSITIVE_SIGNAL_SOURCE_FEASIBILITY" if target_pass else "STOP_DATA_COVERAGE"

    out.mkdir(parents=True,exist_ok=True)
    receipt={
        "schema_version":"football3-nova-n10-referee-aia-feed-receipt-v1",
        "status":"N10_REFEREE_AIA_FEED_DISCOVERY_COMPLETE",
        "classification":classification,
        "exact_base":p["exact_base"],"registry_sha256":sha256_bytes(registry.read_bytes()),
        "head_report_n":len(head_reports),"head_reports":head_reports,
        "discovered_feed_n":len(feeds),"feed_report_n":len(feed_reports),"feed_reports":feed_reports,
        "feed_source_feasible":source_feasible,
        "target_identity_item_n":len(target_items),"target_metadata_pass_n":len(target_pass_items),
        "target_metadata_items":target_pass_items,
        "error_n":len(errors),"errors":errors,
        "feed_item_body_read":False,"feed_item_summary_persisted":False,"article_body_read":False,
        "match_payload_read":False,"standings_payload_read":False,"player_stats_payload_read":False,
        "hidden_endpoint_guessing":False,"recursive_feed_discovery":False,
        "result_labels_read":0,"score_values_read":0,"training_performed":False,"scoring_performed":False,
        "formal_v2_changed":False,"current_changed":False,"production_changed":False,
        "candidate_weight":0,"matrix_delta":0,"full_big5_data_ready":False,"referee_oof_allowed":False,
        "next_step":(
            "IF_TARGET_METADATA_PASS_FREEZE_FEED_ITEM_AND_DESIGN_FIXTURE_LEVEL_ZERO_LABEL_BINDING"
            if target_pass else
            "STOP_AIA_FEED_ROUTE_AND_CONTINUE_ONLY_WITH_NEW_LEGAL_FREE_SOURCE"
        )
    }
    (out/"aia_feed_receipt.json").write_text(json.dumps(receipt,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(receipt,sort_keys=True))
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
