#!/usr/bin/env python3
from __future__ import annotations
import argparse, datetime as dt, hashlib, json, re, ssl, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from html import unescape
from pathlib import Path
from typing import Any

class CollectorError(RuntimeError): pass

def req(c,m):
    if not c: raise CollectorError(m)

def sha256_bytes(b:bytes)->str: return hashlib.sha256(b).hexdigest()
def sha256_file(p:Path)->str: return sha256_bytes(p.read_bytes())

# ZERO-LABEL TRANSPORT CONTRACT:
# - article pages: read only through </head>, capped at HEAD_LIMIT;
# - sitemap XML: URL inventory only;
# - article body bytes are never read by page_record and never parsed.
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0 Safari/537.36"
HEAD_LIMIT=196608
XML_LIMIT=8*1024*1024
HEAD_CLOSE_RE=re.compile(br"</head\s*>",re.I)
DATE_RE=re.compile(r"(?<!\d)(20\d{2})[-/.](0?[1-9]|1[0-2])[-/.]([0-2]?\d|3[01])(?!\d)")
DMY_RE=re.compile(r"(?<!\d)([0-2]?\d|3[01])[/.-](0?[1-9]|1[0-2])[/.-](20\d{2})(?!\d)")
TAG_RE=re.compile(r"<[^>]+>",re.S); SPACE_RE=re.compile(r"\s+")
META_PUB_RE=re.compile(
    r'''(?:property|name|itemprop)\s*=\s*["'](?:article:published_time|datePublished|datepublished|date)["'][^>]*content\s*=\s*["']([^"']+)["']|
        content\s*=\s*["']([^"']+)["'][^>]*(?:property|name|itemprop)\s*=\s*["'](?:article:published_time|datePublished|datepublished|date)["']''',re.I|re.X)
TIME_RE=re.compile(r"<time\b[^>]*datetime\s*=\s*[\"']([^\"']+)[\"']",re.I)
JSONLD_DATE_RE=re.compile(r'"datePublished"\s*:\s*"([^"]+)"',re.I)
TITLE_RE=re.compile(r"<title\b[^>]*>(.*?)</title>",re.I|re.S)
H1_RE=re.compile(r"<h1\b[^>]*>(.*?)</h1>",re.I|re.S)
META_TITLE_RE=re.compile(
    r'''(?:property|name)\s*=\s*["'](?:og:title|twitter:title)["'][^>]*content\s*=\s*["']([^"']+)["']|
        content\s*=\s*["']([^"']+)["'][^>]*(?:property|name)\s*=\s*["'](?:og:title|twitter:title)["']''',re.I|re.X)
CANONICAL_RE=re.compile(r'''<link\b[^>]*rel\s*=\s*["']canonical["'][^>]*href\s*=\s*["']([^"']+)["']|<link\b[^>]*href\s*=\s*["']([^"']+)["'][^>]*rel\s*=\s*["']canonical["']''',re.I)

def _request(url:str)->urllib.request.Request:
    parsed=urllib.parse.urlparse(url); req(parsed.scheme=='https','HTTPS_ONLY:'+url)
    return urllib.request.Request(url,headers={
        'User-Agent':UA,
        'Accept':'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5',
        'Accept-Language':'en-US,en;q=0.9,it;q=0.8,es;q=0.8',
        'Cache-Control':'no-cache'})

def _truncate_head(buf:bytes)->bytes:
    m=HEAD_CLOSE_RE.search(buf)
    if not m: raise CollectorError('HEAD_BOUNDARY_NOT_FOUND_BEFORE_LIMIT')
    return buf[:m.end()]

def fetch_head(url:str,timeout:int=25)->tuple[bytes,dict[str,str],str]:
    rq=_request(url); ctx=ssl.create_default_context()
    with urllib.request.urlopen(rq,timeout=timeout,context=ctx) as r:
        buf=bytearray()
        while len(buf)<HEAD_LIMIT:
            chunk=r.read(min(8192,HEAD_LIMIT-len(buf)))
            if not chunk: break
            buf.extend(chunk)
            if HEAD_CLOSE_RE.search(buf):
                head=_truncate_head(bytes(buf))
                return head,{k.lower():v for k,v in r.headers.items()},r.geturl()
    raise CollectorError('HEAD_BOUNDARY_NOT_FOUND_BEFORE_LIMIT')

def fetch_xml(url:str,timeout:int=25)->tuple[bytes,str]:
    rq=_request(url); ctx=ssl.create_default_context()
    with urllib.request.urlopen(rq,timeout=timeout,context=ctx) as r:
        raw=r.read(XML_LIMIT+1); req(len(raw)<=XML_LIMIT,'XML_TOO_LARGE')
        ctype=(r.headers.get('content-type') or '').lower()
        req('xml' in ctype or raw.lstrip().startswith(b'<?xml') or b'<urlset' in raw[:500] or b'<sitemapindex' in raw[:500],'NOT_SITEMAP_XML')
        return raw,r.geturl()

def textify(raw:bytes)->str:
    s=raw.decode('utf-8','replace')
    s=re.sub(r'<script\b.*?</script>|<style\b.*?</style>',' ',s,flags=re.I|re.S)
    return SPACE_RE.sub(' ',unescape(TAG_RE.sub(' ',s))).strip()

def title_of(raw:bytes)->str:
    s=raw.decode('utf-8','replace')
    for m in META_TITLE_RE.finditer(s):
        v=m.group(1) or m.group(2)
        if v: return SPACE_RE.sub(' ',unescape(TAG_RE.sub(' ',v))).strip()
    m=H1_RE.search(s) or TITLE_RE.search(s)
    return SPACE_RE.sub(' ',unescape(TAG_RE.sub(' ',m.group(1)))).strip() if m else ''

def canonical_of(raw:bytes,fallback:str)->str:
    s=raw.decode('utf-8','replace'); m=CANONICAL_RE.search(s)
    v=(m.group(1) or m.group(2)).strip() if m else fallback
    return urllib.parse.urljoin(fallback,unescape(v))

def normalize_datetime(v:str)->tuple[str|None,str]:
    v=unescape(v).strip()
    if not v:return None,'MISSING'
    z=v.replace('Z','+00:00')
    try:
        x=dt.datetime.fromisoformat(z); return x.isoformat(),'DATETIME_TZ' if x.tzinfo else 'DATETIME_NO_TZ'
    except ValueError: pass
    try:return dt.date.fromisoformat(v[:10]).isoformat(),'DATE_ONLY'
    except ValueError: pass
    m=DMY_RE.search(v)
    if m:return dt.date(int(m.group(3)),int(m.group(2)),int(m.group(1))).isoformat(),'DATE_ONLY'
    m=DATE_RE.search(v)
    if m:return dt.date(int(m.group(1)),int(m.group(2)),int(m.group(3))).isoformat(),'DATE_ONLY'
    return None,'UNPARSED'

def publication_of(raw:bytes)->tuple[str|None,str,str|None]:
    # Compatibility helper: page_record passes HEAD-ONLY bytes to this function.
    s=raw.decode('utf-8','replace'); vals=[]
    for m in META_PUB_RE.finditer(s): vals.append((m.group(1) or m.group(2),'META'))
    vals += [(m.group(1),'TIME') for m in TIME_RE.finditer(s)]
    vals += [(m.group(1),'JSONLD') for m in JSONLD_DATE_RE.finditer(s)]
    for v,src in vals:
        norm,precision=normalize_datetime(v)
        if norm:return norm,precision,src
    # Kept only for compatibility tests. In production acquisition raw is HEAD-only,
    # so this fallback can never scan article body/result content.
    t=textify(raw); m=DMY_RE.search(t)
    if m:return dt.date(int(m.group(3)),int(m.group(2)),int(m.group(1))).isoformat(),'DATE_ONLY','HEAD_TEXT'
    m=DATE_RE.search(t)
    if m:return dt.date(int(m.group(1)),int(m.group(2)),int(m.group(3))).isoformat(),'DATE_ONLY','HEAD_TEXT'
    return None,'MISSING',None

def parse_sitemap(raw:bytes)->tuple[str,list[str]]:
    root=ET.fromstring(raw); kind=root.tag.rsplit('}',1)[-1]; req(kind in {'urlset','sitemapindex'},'UNKNOWN_SITEMAP_ROOT')
    locs=[]
    for e in root.iter():
        if e.tag.rsplit('}',1)[-1]=='loc' and e.text:locs.append(e.text.strip())
    return kind,locs

def domain_ok(url:str,suffix:str)->bool:
    h=(urllib.parse.urlparse(url).hostname or '').lower(); s=suffix.lower(); return h==s or h.endswith('.'+s)

def matches_url(url:str,pats:list[str])->bool:
    path=urllib.parse.urlparse(url).path.lower(); return any(re.search(p,path,re.I) for p in pats)

def round_no(title:str,text:str,pats:list[str])->int|None:
    for src in (title,text[:1500]):
        for p in pats:
            m=re.search(p,src,re.I)
            if m:
                n=int(m.group(1))
                if 1<=n<=60:return n
    return None

def in_target(pub:str|None,start:str,end:str)->bool:return bool(pub and start<=pub[:10]<=end)

def discover_urls(cfg:dict[str,Any],timeout:int,max_sitemaps:int=80)->tuple[list[str],list[dict[str,Any]]]:
    found=set(cfg.get('seed_urls',[])); errors=[]; q=list(cfg.get('sitemap_urls',[])); seen=set()
    while q and len(seen)<max_sitemaps:
        u=q.pop(0)
        if u in seen:continue
        seen.add(u)
        try:
            raw,final=fetch_xml(u,timeout); req(domain_ok(final,cfg['domain_suffix']),'SITEMAP_REDIRECT_OUTSIDE_DOMAIN')
            kind,locs=parse_sitemap(raw)
            if kind=='sitemapindex':
                for x in locs:
                    if domain_ok(x,cfg['domain_suffix']) and x not in seen:q.append(x)
            else:
                for x in locs:
                    if domain_ok(x,cfg['domain_suffix']) and matches_url(x,cfg['url_patterns']):found.add(x)
        except Exception as e:errors.append({'url':u,'stage':'sitemap','error':type(e).__name__+':'+str(e)[:240]})
    return sorted(found),errors

def page_record(cfg:dict[str,Any],url:str,timeout:int,retrieved_at:str)->tuple[dict[str,Any]|None,dict[str,Any]|None]:
    try:head,headers,final=fetch_head(url,timeout)
    except Exception as e:return None,{'url':url,'stage':'head','error':type(e).__name__+':'+str(e)[:240]}
    if not domain_ok(final,cfg['domain_suffix']):return None,{'url':url,'stage':'redirect','error':'REDIRECT_OUTSIDE_OFFICIAL_DOMAIN','final_url':final}
    title=title_of(head); canonical=canonical_of(head,final); searchable=(title+' '+canonical).lower()
    if cfg.get('required_text_any') and not any(x.lower() in searchable for x in cfg['required_text_any']):
        return None,{'url':url,'stage':'metadata_filter','error':'REQUIRED_TITLE_OR_URL_TEXT_NOT_FOUND','title':title}
    pub,precision,source=publication_of(head); rnd=round_no(title,canonical,cfg.get('round_patterns',[]))
    projection={'competition':cfg['competition'],'source_id':cfg['source_id'],'canonical_url':canonical,'published_at':pub,'publication_precision':precision,'round':rnd,'title':title}
    pbytes=json.dumps(projection,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
    return {**projection,'authority':cfg['authority'],'source_url':url,'final_url':final,'retrieved_at':retrieved_at,'publication_evidence':source,
            'content_sha256':sha256_bytes(head),'content_scope':'HTML_HEAD_ONLY','metadata_projection_sha256':sha256_bytes(pbytes),'content_bytes':len(head),
            'article_body_read':False,'http_last_modified':headers.get('last-modified'),'etag':headers.get('etag'),'result_labels_read':0,'score_values_read':0},None

def _runtime_zero_label_contract()->None:
    doc=b'<html><head><title>Match officials for Matchweek 14</title></head><body>HOME 2-1 AWAY 26/10/2022</body></html>'
    head=_truncate_head(doc)
    req(b'2-1' not in head and b'26/10/2022' not in head,'BODY_LEAK')
    req(title_of(head)=='Match officials for Matchweek 14','HEAD_TITLE')

def build(registry:Path,out:Path,timeout:int=25)->dict[str,Any]:
    _runtime_zero_label_contract()
    p=json.loads(registry.read_text()); req(p['status']=='DESIGN_LOCKED_ZERO_LABEL','STATUS')
    req(p['exact_base']=='b201d3476b22f6fd2e80378990d83dfa6320b7da','EXACT_BASE')
    h=p['hard_rules']; req(h['target_result_labels_read'] is False,'NO_LABELS'); req(h['training_allowed'] is False and h['scoring_allowed'] is False,'NO_MODEL')
    req(h['official_public_sources_only'] is True and h['paid_or_secret_source_allowed'] is False,'PUBLIC_ONLY')
    s=p['safety']; req(s['result_labels_read']==0 and s['score_values_read']==0 and s['training_performed'] is False and s['scoring_performed'] is False,'SAFETY')
    out.mkdir(parents=True,exist_ok=True); retrieved_at=dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    all_rows=[]; source_reports=[]; gap=list(p['gap_receipts']); target=p['target']; expected=set(range(1,int(target['expected_rounds'])+1))
    for cfg in p['sources']:
        urls,errs=discover_urls(cfg,timeout); rows=[]
        for u in urls:
            rec,err=page_record(cfg,u,timeout,retrieved_at)
            if err:errs.append(err); continue
            if rec and in_target(rec['published_at'],target['season_start'],target['season_end']):rows.append(rec)
        uniq={}
        for r in rows:uniq[(r['round'],r['content_sha256'])]=r
        rows=sorted(uniq.values(),key=lambda x:(999 if x['round'] is None else x['round'],x['source_url']))
        rounds=sorted({r['round'] for r in rows if isinstance(r['round'],int) and r['round'] in expected}); missing=sorted(expected-set(rounds))
        report={'competition':cfg['competition'],'source_id':cfg['source_id'],'candidate_url_n':len(urls),'accepted_page_n':len(rows),'rounds_found':rounds,'missing_rounds':missing,
                'round_inventory_complete':len(missing)==0,'publication_missing_n':sum(r['published_at'] is None for r in rows),'date_only_n':sum(r['publication_precision']=='DATE_ONLY' for r in rows),
                'article_body_read_n':sum(bool(r['article_body_read']) for r in rows),'fetch_error_n':len(errs),'fetch_errors':errs[:100],'fixture_level_inventory_complete':False,
                'classification':'PAGE_INVENTORY_COMPLETE_FIXTURE_BINDING_PENDING' if len(missing)==0 else 'STOP_DATA_COVERAGE'}
        source_reports.append(report); all_rows.extend(rows)
    inv=out/'official_page_inventory.jsonl'; inv.write_text(''.join(json.dumps(r,sort_keys=True,ensure_ascii=False)+'\n' for r in all_rows))
    (out/'gap_receipts.json').write_text(json.dumps(gap,indent=2,sort_keys=True,ensure_ascii=False)+'\n')
    inventory_complete=all(x['round_inventory_complete'] for x in source_reports)
    receipt={'schema_version':'football3-nova-n10-referee-official-archive-receipt-v1.1','status':'N10_REFEREE_OFFICIAL_ARCHIVE_COLLECTOR_COMPLETE','classification':'STOP_DATA_COVERAGE',
             'exact_base':p['exact_base'],'registry_sha256':sha256_file(registry),'retrieved_at':retrieved_at,'official_page_row_n':len(all_rows),'source_reports':source_reports,
             'inventory_competitions':target['inventory_competitions'],'page_round_inventory_complete_for_three':inventory_complete,'fixture_level_inventory_complete_for_three':False,
             'gap_only_competitions':target['gap_only_competitions'],'gap_receipt_n':len(gap),'full_big5_data_ready':False,'article_body_read':False,
             'result_labels_read':0,'score_values_read':0,'training_performed':False,'scoring_performed':False,'formal_v2_changed':False,'current_changed':False,'production_changed':False,
             'candidate_weight':0,'matrix_delta':0,'inventory_sha256':sha256_file(inv),
             'next_step':'CONTINUE_OFFICIAL_METADATA_DISCOVERY_AND_COMPLETE_FIXTURE_LEVEL_BINDING_FOR_EPL_LA_LIGA_SERIE_A_AND_CONTINUE_BUNDESLIGA_LIGUE1_AVAILABLE_AT_DISCOVERY; DO_NOT_START_REFEREE_OOF'}
    (out/'collector_receipt.json').write_text(json.dumps(receipt,indent=2,sort_keys=True,ensure_ascii=False)+'\n'); print(json.dumps(receipt,sort_keys=True,ensure_ascii=False)); return receipt

def main():
    a=argparse.ArgumentParser(); a.add_argument('--registry',type=Path,required=True); a.add_argument('--out',type=Path,required=True); a.add_argument('--timeout',type=int,default=25)
    x=a.parse_args(); build(x.registry,x.out,x.timeout)
if __name__=='__main__':main()
