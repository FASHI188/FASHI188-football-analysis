#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import ssl
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

class AuditError(RuntimeError):
    pass

def req(c: bool, m: str) -> None:
    if not c:
        raise AuditError(m)

def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def domain_ok(url: str, suffix: str) -> bool:
    h=(urllib.parse.urlparse(url).hostname or "").lower()
    s=suffix.lower()
    return h==s or h.endswith("."+s)

def parse_iso(v: str) -> dt.datetime:
    x=dt.datetime.fromisoformat(v.replace("Z","+00:00"))
    req(x.tzinfo is not None,"TZ_REQUIRED")
    return x.astimezone(dt.timezone.utc)

def fetch_bytes(url: str, timeout: int, limit: int, suffix: str, accept: str) -> tuple[bytes,str,dict[str,str]]:
    req(domain_ok(url,suffix),"NON_OFFICIAL_URL")
    rq=urllib.request.Request(url,headers={
        "User-Agent":"Football3-Nova-N10-AIASitemapHead/1.0",
        "Accept":accept,
    })
    with urllib.request.urlopen(rq,timeout=timeout,context=ssl.create_default_context()) as r:
        final=r.geturl()
        req(domain_ok(final,suffix),"REDIRECT_OUTSIDE_OFFICIAL")
        data=r.read(limit+1)
        req(len(data)<=limit,"RESPONSE_TOO_LARGE")
        return data,final,{k.lower():v for k,v in r.headers.items()}

HEAD_CLOSE=re.compile(br"</head\s*>",re.I)
META_TAG_RE=re.compile(r"<meta\b[^>]*>",re.I)
LINK_TAG_RE=re.compile(r"<link\b[^>]*>",re.I)
TIME_TAG_RE=re.compile(r"<time\b[^>]*>",re.I)
TITLE_RE=re.compile(r"<title\b[^>]*>(.*?)</title>",re.I|re.S)
ATTR_RE=re.compile(r"""([:\w-]+)\s*=\s*["']([^"']*)["']""",re.I)
JSONLD_DATE_RE=re.compile(r'''"datePublished"\s*:\s*"([^"]+)"''',re.I)

def fetch_head_html(url: str, timeout: int, limit: int, suffix: str) -> tuple[bytes,str,dict[str,str]]:
    req(domain_ok(url,suffix),"NON_OFFICIAL_TARGET")
    rq=urllib.request.Request(url,headers={
        "User-Agent":"Football3-Nova-N10-AIASitemapHead/1.0",
        "Accept":"text/html,application/xhtml+xml",
    })
    with urllib.request.urlopen(rq,timeout=timeout,context=ssl.create_default_context()) as r:
        final=r.geturl()
        req(domain_ok(final,suffix),"TARGET_REDIRECT_OUTSIDE_OFFICIAL")
        buf=bytearray()
        while len(buf)<limit:
            chunk=r.read(min(8192,limit-len(buf)))
            if not chunk:
                break
            buf.extend(chunk)
            m=HEAD_CLOSE.search(buf)
            if m:
                return bytes(buf[:m.end()]),final,{k.lower():v for k,v in r.headers.items()}
    raise AuditError("HEAD_BOUNDARY_NOT_FOUND")

def parse_robots_sitemaps(raw: bytes, base_url: str, suffix: str) -> list[str]:
    out=[]
    for line in raw.decode("utf-8","replace").splitlines():
        m=re.match(r"^\s*Sitemap\s*:\s*(\S+)\s*$",line,re.I)
        if not m:
            continue
        u=urllib.parse.urljoin(base_url,m.group(1))
        if domain_ok(u,suffix):
            out.append(u)
    return sorted(dict.fromkeys(out))

def parse_sitemap(raw: bytes) -> tuple[str,list[str]]:
    root=ET.fromstring(raw)
    kind=root.tag.rsplit("}",1)[-1].casefold()
    req(kind in {"sitemapindex","urlset"},"UNSUPPORTED_SITEMAP_ROOT")
    locs=[]
    for e in root.iter():
        if e.tag.rsplit("}",1)[-1].casefold()=="loc" and e.text:
            locs.append(e.text.strip())
    return kind,locs

def discover_sitemap_target(
    roots: list[str], target_slug: str, timeout: int, suffix: str,
    max_docs: int, max_bytes: int
) -> tuple[list[dict[str,Any]],list[str],list[dict[str,Any]]]:
    q=list(roots)
    seen=set()
    docs=[]
    matches=[]
    errors=[]
    while q and len(seen)<max_docs:
        u=q.pop(0)
        if u in seen:
            continue
        seen.add(u)
        try:
            raw,final,headers=fetch_bytes(
                u,timeout,max_bytes,suffix,
                "application/xml,text/xml,*/*;q=0.1"
            )
            kind,locs=parse_sitemap(raw)
            docs.append({
                "source_url":u,"final_url":final,"kind":kind,
                "bytes":len(raw),"sha256":sha256_bytes(raw),
                "loc_n":len(locs),"content_type":headers.get("content-type")
            })
            if kind=="sitemapindex":
                for x in locs:
                    if domain_ok(x,suffix) and x not in seen:
                        q.append(x)
            else:
                for x in locs:
                    if domain_ok(x,suffix) and target_slug.casefold() in x.casefold():
                        matches.append(x)
        except Exception as e:
            errors.append({"url":u,"error":f"{type(e).__name__}:{e}"[:400]})
    return docs,sorted(dict.fromkeys(matches)),errors

def tag_attrs(tag: str) -> dict[str,str]:
    return {k.casefold():v for k,v in ATTR_RE.findall(tag)}

def normalize_pub(v: str) -> tuple[str | None,str]:
    s=v.strip()
    try:
        x=dt.datetime.fromisoformat(s.replace("Z","+00:00"))
        if x.tzinfo is None:
            return x.isoformat(),"DATETIME_NO_TZ"
        return x.astimezone(dt.timezone.utc).isoformat(),"DATETIME_TZ"
    except Exception:
        pass
    try:
        d=dt.date.fromisoformat(s[:10])
        return d.isoformat(),"DATE_ONLY"
    except Exception:
        return None,"UNPARSED"

def extract_head_metadata(head: bytes, final_url: str) -> dict[str,Any]:
    s=head.decode("utf-8","replace")
    title=None
    mt=TITLE_RE.search(s)
    if mt:
        title=re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",mt.group(1))).strip()[:500]
    canonical=None
    og_title=None
    candidates=[]
    for m in LINK_TAG_RE.finditer(s):
        a=tag_attrs(m.group(0))
        rel={x.casefold() for x in a.get("rel","").split()}
        href=a.get("href")
        if "canonical" in rel and href:
            canonical=urllib.parse.urljoin(final_url,href)
            break
    for m in META_TAG_RE.finditer(s):
        a=tag_attrs(m.group(0))
        key=(a.get("property") or a.get("name") or a.get("itemprop") or "").casefold()
        val=a.get("content")
        if not val:
            continue
        if key in {"og:title","twitter:title"} and not og_title:
            og_title=val.strip()[:500]
        if key in {"article:published_time","datepublished","date"}:
            norm,precision=normalize_pub(val)
            if norm:
                candidates.append({"value":norm,"precision":precision,"source":key})
    for m in TIME_TAG_RE.finditer(s):
        a=tag_attrs(m.group(0))
        val=a.get("datetime")
        if val:
            norm,precision=normalize_pub(val)
            if norm:
                candidates.append({"value":norm,"precision":precision,"source":"time:datetime"})
    for m in JSONLD_DATE_RE.finditer(s):
        norm,precision=normalize_pub(m.group(1))
        if norm:
            candidates.append({"value":norm,"precision":precision,"source":"jsonld:datePublished"})
    uniq={}
    for c in candidates:
        uniq[(c["value"],c["precision"],c["source"])]=c
    return {
        "title":title,
        "og_title":og_title,
        "canonical_url":canonical,
        "publication_candidates":list(uniq.values()),
    }

def identity_pass(meta: dict[str,Any], final_url: str, target: dict[str,Any], suffix: str) -> bool:
    if not domain_ok(final_url,suffix):
        return False
    urls=[final_url,meta.get("canonical_url")]
    if not any(isinstance(u,str) and target["slug_term"].casefold() in u.casefold() for u in urls):
        return False
    text=((meta.get("og_title") or "")+" "+(meta.get("title") or "")).casefold()
    if text.strip():
        if not all(t.casefold() in text for t in target["title_terms"]):
            return False
    return True

def publication_pass(meta: dict[str,Any], target: dict[str,Any]) -> tuple[bool,dict[str,Any]|None]:
    cutoff=parse_iso(target["safe_cutoff_utc"])
    floor=parse_iso(target["publication_floor_utc"])
    target_date=target["expected_publication_date"]
    eligible=[]
    for c in meta.get("publication_candidates",[]):
        val=c["value"]
        if c["precision"]=="DATE_ONLY":
            try:
                d=dt.date.fromisoformat(val[:10])
            except Exception:
                continue
            if d.isoformat()!=target_date:
                continue
            if d < cutoff.date():
                eligible.append(c)
        else:
            try:
                x=parse_iso(val) if c["precision"]=="DATETIME_TZ" else dt.datetime.fromisoformat(val).replace(tzinfo=dt.timezone.utc)
            except Exception:
                continue
            if floor <= x < cutoff and x.date().isoformat()==target_date:
                eligible.append(c)
    if not eligible:
        return False,None
    eligible=sorted(eligible,key=lambda x:(x["precision"]!="DATETIME_TZ",x["value"],x["source"]))
    return True,eligible[0]

def run(registry: Path,out: Path,timeout: int=20)->dict[str,Any]:
    p=json.loads(registry.read_text(encoding="utf-8"))
    req(p["status"]=="DESIGN_LOCKED_ZERO_LABEL","STATUS")
    req(p["exact_base"]=="bcf22301e3b0700e3724a86cccd924e9f5b498cb","EXACT_BASE")
    h=p["hard_rules"]
    req(h["result_labels_read"] is False and h["score_values_read"] is False,"ZERO_LABEL")
    req(h["match_payload_read"] is False and h["standings_payload_read"] is False and h["player_stats_payload_read"] is False,"NO_SPORT_PAYLOAD")
    req(h["article_body_read"] is False,"NO_ARTICLE_BODY")
    req(h["old_aia_index_route_repeated"] is False,"NO_OLD_INDEX_REPEAT")
    req(h["sitemap_lastmod_used_as_publication_proof"] is False,"NO_LASTMOD_PROOF")
    req(h["hidden_endpoint_guessing_allowed"] is False,"NO_GUESSING")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False,"NO_MODEL")
    req(h["paid_or_secret_source_allowed"] is False,"NO_SECRET")
    req(h["candidate_weight"]==0 and h["matrix_delta"]==0,"ZERO_WEIGHT")

    src=p["source"]; ac=p["acquisition_contract"]; target=p["target"]
    req(src["sitemap_discovery"]=="ROBOTS_SITEMAP_DIRECTIVES_ONLY","ROBOTS_ONLY")
    req(ac["target_html_scope"]=="HEAD_ONLY_THROUGH_FIRST_CLOSING_HEAD","HEAD_ONLY")
    req(ac["article_body_fetch_allowed"] is False,"NO_BODY")

    errors=[]
    robots_report=None
    roots=[]
    try:
        raw,final,headers=fetch_bytes(
            src["robots_url"],timeout,1000000,src["allowed_domain_suffix"],
            "text/plain,*/*;q=0.1"
        )
        roots=parse_robots_sitemaps(raw,final,src["allowed_domain_suffix"])
        robots_report={
            "source_url":src["robots_url"],"final_url":final,
            "bytes":len(raw),"sha256":sha256_bytes(raw),
            "sitemap_directive_n":len(roots),"sitemap_urls":roots,
            "content_type":headers.get("content-type"),
        }
    except Exception as e:
        errors.append({"stage":"robots","url":src["robots_url"],"error":f"{type(e).__name__}:{e}"[:400]})

    sitemap_docs=[]
    sitemap_target_urls=[]
    sitemap_errors=[]
    if roots:
        sitemap_docs,sitemap_target_urls,sitemap_errors=discover_sitemap_target(
            roots,target["slug_term"],timeout,src["allowed_domain_suffix"],
            int(ac["max_sitemap_documents"]),int(ac["max_sitemap_bytes_each"])
        )
        errors.extend({"stage":"sitemap",**x} for x in sitemap_errors)

    target_report=None
    identity=False
    pub_pass=False
    selected_pub=None
    try:
        head,final,headers=fetch_head_html(
            target["official_url"],timeout,int(ac["max_target_head_bytes"]),src["allowed_domain_suffix"]
        )
        meta=extract_head_metadata(head,final)
        identity=identity_pass(meta,final,target,src["allowed_domain_suffix"])
        pub_pass,selected_pub=publication_pass(meta,target)
        target_report={
            "source_url":target["official_url"],"final_url":final,
            "head_bytes":len(head),"head_sha256":sha256_bytes(head),
            "content_type":headers.get("content-type"),
            "metadata":meta,
            "identity_pass":identity,
            "publication_metadata_pass":pub_pass,
            "selected_publication":selected_pub,
            "body_read":False,
        }
    except Exception as e:
        errors.append({"stage":"target_head","url":target["official_url"],"error":f"{type(e).__name__}:{e}"[:400]})

    positive=bool(identity and pub_pass)
    classification="POSITIVE_SIGNAL_SOURCE_FEASIBILITY" if positive else "STOP_DATA_COVERAGE"
    out.mkdir(parents=True,exist_ok=True)
    receipt={
        "schema_version":"football3-nova-n10-referee-aia-sitemap-head-receipt-v1",
        "status":"N10_REFEREE_AIA_SITEMAP_HEAD_AUDIT_COMPLETE",
        "classification":classification,
        "exact_base":p["exact_base"],"registry_sha256":sha256_bytes(registry.read_bytes()),
        "robots_report":robots_report,
        "sitemap_document_n":len(sitemap_docs),"sitemap_documents":sitemap_docs,
        "sitemap_target_url_n":len(sitemap_target_urls),"sitemap_target_urls":sitemap_target_urls,
        "sitemap_lastmod_used_as_publication_proof":False,
        "target_report":target_report,
        "target_identity_pass":identity,
        "publication_metadata_pass":pub_pass,
        "formal_available_at_proven":positive,
        "error_n":len(errors),"errors":errors,
        "article_body_read":False,"match_payload_read":False,"standings_payload_read":False,"player_stats_payload_read":False,
        "old_aia_index_route_repeated":False,"hidden_endpoint_guessing":False,
        "result_labels_read":0,"score_values_read":0,"training_performed":False,"scoring_performed":False,
        "formal_v2_changed":False,"current_changed":False,"production_changed":False,
        "candidate_weight":0,"matrix_delta":0,"full_big5_data_ready":False,"referee_oof_allowed":False,
        "next_step":(
            "PRESERVE_POSITIVE_SOURCE_SIGNAL_AND_BUILD_ZERO_LABEL_TARGET_SEASON_AIA_HEAD_INVENTORY_IN_NEW_BATCH"
            if positive else
            "STOP_AIA_SITEMAP_HEAD_ROUTE_AND_CONTINUE_ONLY_WITH_NEW_LEGAL_FREE_SOURCE"
        ),
    }
    (out/"aia_sitemap_head_receipt.json").write_text(json.dumps(receipt,indent=2,sort_keys=True)+"\n",encoding="utf-8")
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
