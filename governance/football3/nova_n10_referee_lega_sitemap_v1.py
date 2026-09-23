#!/usr/bin/env python3
from __future__ import annotations
import argparse, datetime as dt, hashlib, json, re, ssl, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from html import unescape
from pathlib import Path
from typing import Any

class DiscoveryError(RuntimeError): pass
def req(c: bool, m: str) -> None:
    if not c: raise DiscoveryError(m)
def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

HEAD_CLOSE=re.compile(br"</head\s*>",re.I)
META_PUB_RE=re.compile(
    r'''(?:property|name|itemprop)\s*=\s*["'](?:article:published_time|datePublished|datepublished|date)["'][^>]*content\s*=\s*["']([^"']+)["']|
        content\s*=\s*["']([^"']+)["'][^>]*(?:property|name|itemprop)\s*=\s*["'](?:article:published_time|datePublished|datepublished|date)["']''',
    re.I|re.X,
)
JSONLD_DATE_RE=re.compile(r'"datePublished"\s*:\s*"([^"]+)"',re.I)
META_TITLE_RE=re.compile(
    r'''(?:property|name)\s*=\s*["'](?:og:title|twitter:title)["'][^>]*content\s*=\s*["']([^"']+)["']|
        content\s*=\s*["']([^"']+)["'][^>]*(?:property|name)\s*=\s*["'](?:og:title|twitter:title)["']''',re.I|re.X)
TITLE_RE=re.compile(r"<title\b[^>]*>(.*?)</title>",re.I|re.S)
CANONICAL_RE=re.compile(
    r'''<link\b[^>]*rel\s*=\s*["']canonical["'][^>]*href\s*=\s*["']([^"']+)["']|
        <link\b[^>]*href\s*=\s*["']([^"']+)["'][^>]*rel\s*=\s*["']canonical["']''',re.I|re.X)

def domain_ok(url: str, suffix: str) -> bool:
    h=(urllib.parse.urlparse(url).hostname or "").lower()
    s=suffix.lower()
    return h==s or h.endswith("."+s)

def fetch(url: str, timeout: int, limit: int, accept: str, suffix: str) -> tuple[bytes,str,dict[str,str]]:
    req(domain_ok(url,suffix),"NON_OFFICIAL_URL")
    rq=urllib.request.Request(url,headers={"User-Agent":"Football3-Nova-N10-LegaSitemap/1.0","Accept":accept})
    with urllib.request.urlopen(rq,timeout=timeout,context=ssl.create_default_context()) as r:
        final=r.geturl(); req(domain_ok(final,suffix),"REDIRECT_OUTSIDE_OFFICIAL")
        data=r.read(limit+1); req(len(data)<=limit,"RESPONSE_TOO_LARGE")
        return data,final,{k.lower():v for k,v in r.headers.items()}

def fetch_head_html(url: str, timeout: int, limit: int, suffix: str) -> tuple[bytes,str,dict[str,str]]:
    req(domain_ok(url,suffix),"NON_OFFICIAL_PAGE")
    rq=urllib.request.Request(url,headers={"User-Agent":"Football3-Nova-N10-LegaSitemap/1.0","Accept":"text/html,application/xhtml+xml"})
    with urllib.request.urlopen(rq,timeout=timeout,context=ssl.create_default_context()) as r:
        final=r.geturl(); req(domain_ok(final,suffix),"PAGE_REDIRECT_OUTSIDE_OFFICIAL")
        buf=bytearray()
        while len(buf)<limit:
            chunk=r.read(min(8192,limit-len(buf)))
            if not chunk: break
            buf.extend(chunk)
            m=HEAD_CLOSE.search(buf)
            if m:
                return bytes(buf[:m.end()]),final,{k.lower():v for k,v in r.headers.items()}
    raise DiscoveryError("HEAD_BOUNDARY_NOT_FOUND")

def robots_sitemaps(raw: bytes, base: str, suffix: str) -> list[str]:
    out=[]
    for line in raw.decode("utf-8","replace").splitlines():
        m=re.match(r"\s*Sitemap\s*:\s*(\S+)\s*$",line,re.I)
        if not m: continue
        u=urllib.parse.urljoin(base,m.group(1))
        if domain_ok(u,suffix): out.append(u)
    return sorted(dict.fromkeys(out))

def parse_sitemap(raw: bytes) -> tuple[str,list[dict[str,str|None]]]:
    root=ET.fromstring(raw)
    kind=root.tag.rsplit("}",1)[-1]
    req(kind in {"urlset","sitemapindex"},"UNKNOWN_SITEMAP_ROOT")
    rows=[]
    for child in list(root):
        ckind=child.tag.rsplit("}",1)[-1]
        if kind=="urlset" and ckind!="url": continue
        if kind=="sitemapindex" and ckind!="sitemap": continue
        loc=None; lastmod=None
        for e in child:
            name=e.tag.rsplit("}",1)[-1]
            if name=="loc" and e.text: loc=e.text.strip()
            elif name=="lastmod" and e.text: lastmod=e.text.strip()
        if loc: rows.append({"loc":loc,"lastmod":lastmod})
    return kind,rows

def candidate_url(url: str, path_terms: list[str], context_terms: list[str]) -> bool:
    p=urllib.parse.urlparse(url)
    text=(p.path+"?"+p.query).casefold()
    return any(x.casefold() in text for x in path_terms) and all(x.casefold() in text for x in context_terms)

def normalize_datetime(v: str) -> str | None:
    v=unescape(v).strip()
    if not v: return None
    z=v.replace("Z","+00:00")
    try:
        x=dt.datetime.fromisoformat(z)
        if x.tzinfo is None: x=x.replace(tzinfo=dt.timezone.utc)
        return x.astimezone(dt.timezone.utc).isoformat()
    except ValueError:
        pass
    try:
        d=dt.date.fromisoformat(v[:10])
        return dt.datetime(d.year,d.month,d.day,tzinfo=dt.timezone.utc).isoformat()
    except ValueError:
        return None

def publication_of(head: bytes) -> tuple[str|None,str|None]:
    s=head.decode("utf-8","replace")
    for m in META_PUB_RE.finditer(s):
        v=m.group(1) or m.group(2)
        x=normalize_datetime(v)
        if x:return x,"META"
    m=JSONLD_DATE_RE.search(s)
    if m:
        x=normalize_datetime(m.group(1))
        if x:return x,"JSONLD_HEAD"
    return None,None

def title_of(head: bytes) -> str | None:
    s=head.decode("utf-8","replace")
    for m in META_TITLE_RE.finditer(s):
        v=m.group(1) or m.group(2)
        if v:return re.sub(r"\s+"," ",unescape(v)).strip()[:300]
    m=TITLE_RE.search(s)
    if m:return re.sub(r"\s+"," ",unescape(re.sub(r"<[^>]+>"," ",m.group(1)))).strip()[:300]
    return None

def canonical_of(head: bytes, fallback: str) -> str:
    s=head.decode("utf-8","replace"); m=CANONICAL_RE.search(s)
    if not m:return fallback
    v=m.group(1) or m.group(2)
    return urllib.parse.urljoin(fallback,unescape(v.strip()))

def in_window(pub: str|None, start: str, end: str) -> bool:
    if not pub:return False
    x=dt.datetime.fromisoformat(pub)
    lo=dt.datetime.fromisoformat(start.replace("Z","+00:00"))
    hi=dt.datetime.fromisoformat(end.replace("Z","+00:00"))
    return lo<=x<=hi

def discover_sitemaps(reg: dict[str,Any],timeout: int) -> tuple[list[dict[str,Any]],list[dict[str,Any]],list[str]]:
    src=reg["source"]; ac=reg["acquisition_contract"]; suffix=src["allowed_domain_suffix"]
    robots,robots_final,robots_headers=fetch(src["robots_url"],timeout,1_000_000,"text/plain,*/*;q=0.2",suffix)
    roots=robots_sitemaps(robots,robots_final,suffix)
    if not roots: roots=[src["standard_sitemap_fallback"]]
    q=list(roots); seen=set(); docs=[]; errors=[]; urls=[]
    while q and len(seen)<int(ac["max_sitemap_documents"]):
        u=q.pop(0)
        if u in seen:continue
        seen.add(u)
        try:
            raw,final,headers=fetch(u,timeout,int(ac["max_sitemap_bytes_each"]),"application/xml,text/xml,*/*;q=0.2",suffix)
            kind,rows=parse_sitemap(raw)
            docs.append({"source_url":u,"final_url":final,"kind":kind,"sha256":sha256_bytes(raw),"row_n":len(rows),"content_type":headers.get("content-type")})
            if kind=="sitemapindex":
                for row in rows:
                    loc=str(row["loc"])
                    if domain_ok(loc,suffix) and loc not in seen:q.append(loc)
            else:
                for row in rows:
                    loc=str(row["loc"])
                    if domain_ok(loc,suffix) and candidate_url(loc,reg["target"]["path_terms"],reg["target"]["required_context_terms"]):
                        urls.append(loc)
        except Exception as e:
            errors.append({"stage":"sitemap","url":u,"error":f"{type(e).__name__}:{e}"[:400]})
    return docs,errors,sorted(dict.fromkeys(urls))[:int(ac["max_candidate_urls"])]

def run(registry: Path,out: Path,timeout: int=20)->dict[str,Any]:
    p=json.loads(registry.read_text(encoding="utf-8"))
    req(p["status"]=="DESIGN_LOCKED_ZERO_LABEL","STATUS")
    req(p["exact_base"]=="7c11355623d61391ba8b205beb2772f9ff249046","EXACT_BASE")
    h=p["hard_rules"]; ac=p["acquisition_contract"]; pubc=p["publication_contract"]
    req(h["result_labels_read"] is False and h["score_values_read"] is False,"ZERO_LABEL")
    req(h["match_payload_read"] is False and h["standings_payload_read"] is False and h["player_stats_payload_read"] is False,"NO_SPORT_PAYLOAD")
    req(h["article_body_read"] is False and ac["article_body_read"] is False,"NO_BODY")
    req(h["training_allowed"] is False and h["scoring_allowed"] is False,"NO_MODEL")
    req(h["paid_or_secret_source_allowed"] is False,"NO_SECRET")
    req(pubc["sitemap_lastmod_is_publication_proof"] is False,"LASTMOD_NOT_PUBLICATION")

    docs,errors,candidates=discover_sitemaps(p,timeout)
    reports=[]
    for u in candidates:
        try:
            head,final,headers=fetch_head_html(u,timeout,int(ac["candidate_head_limit_bytes"]),p["source"]["allowed_domain_suffix"])
            publication,evidence=publication_of(head)
            canonical=canonical_of(head,final)
            reports.append({
                "source_url":u,"final_url":final,"canonical_url":canonical,
                "title":title_of(head),"published_at":publication,"publication_evidence":evidence,
                "published_in_target_season":in_window(publication,p["target"]["season_start"],p["target"]["season_end"]),
                "head_sha256":sha256_bytes(head),"head_bytes":len(head),"content_type":headers.get("content-type"),
                "article_body_read":False,"result_labels_read":0,"score_values_read":0,
            })
        except Exception as e:
            errors.append({"stage":"candidate_head","url":u,"error":f"{type(e).__name__}:{e}"[:400]})

    target_rows=[x for x in reports if x["published_in_target_season"]]
    classification="POSITIVE_SIGNAL_SOURCE_FEASIBILITY" if target_rows else "STOP_DATA_COVERAGE"
    out.mkdir(parents=True,exist_ok=True)
    inventory={
        "schema_version":"football3-nova-n10-referee-lega-sitemap-inventory-v1",
        "candidate_url_n":len(candidates),"candidate_urls":candidates,"candidate_reports":reports,
        "target_season_candidate_n":len(target_rows),"target_season_candidates":target_rows,
    }
    (out/"candidate_inventory.json").write_text(json.dumps(inventory,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    receipt={
        "schema_version":"football3-nova-n10-referee-lega-sitemap-receipt-v1",
        "status":"N10_REFEREE_LEGA_SITEMAP_DISCOVERY_COMPLETE","classification":classification,
        "exact_base":p["exact_base"],"registry_sha256":sha256_bytes(registry.read_bytes()),
        "sitemap_document_n":len(docs),"sitemap_documents":docs,"candidate_url_n":len(candidates),
        "candidate_report_n":len(reports),"target_season_candidate_n":len(target_rows),
        "target_season_candidates":[{"source_url":x["source_url"],"canonical_url":x["canonical_url"],"published_at":x["published_at"],"title":x["title"],"head_sha256":x["head_sha256"]} for x in target_rows],
        "error_n":len(errors),"errors":errors,
        "sitemap_lastmod_used_as_publication_proof":False,"article_body_read":False,
        "match_payload_read":False,"standings_payload_read":False,"player_stats_payload_read":False,
        "result_labels_read":0,"score_values_read":0,"training_performed":False,"scoring_performed":False,
        "formal_v2_changed":False,"current_changed":False,"production_changed":False,
        "candidate_weight":0,"matrix_delta":0,"full_big5_data_ready":False,"referee_oof_allowed":False,
        "next_step":(
            "IF_TARGET_SEASON_CANDIDATES_FREEZE_EXACT_URLS_AND_BUILD_ROUND_LEVEL_HEAD_METADATA_INVENTORY"
            if target_rows else
            "STOP_LEGA_SITEMAP_ROUTE_AND_CONTINUE_ONLY_WITH_NEW_LEGAL_FREE_SOURCE"
        )
    }
    (out/"lega_sitemap_receipt.json").write_text(json.dumps(receipt,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(receipt,sort_keys=True)); return receipt

def main():
    a=argparse.ArgumentParser(); a.add_argument("--registry",type=Path,required=True); a.add_argument("--out",type=Path,required=True); a.add_argument("--timeout",type=int,default=20)
    x=a.parse_args(); run(x.registry,x.out,x.timeout)
if __name__=="__main__": main()
