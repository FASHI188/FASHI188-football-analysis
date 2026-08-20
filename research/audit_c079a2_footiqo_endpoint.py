#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, re
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

URL='https://footiqo.com/database/leagues/argentina-liga-profesional/'
KEYWORDS=['datatable','datatables','ajax','serverside','processing','pagelength','lengthmenu','buttons','csv','excel','export','admin-ajax','wp_ajax','wp-json','/api/','download','historical','odds']
ENDPOINT_RE=re.compile(r'''(?P<q>['"])(?P<u>(?:https?:)?//[^'"\s]+|/[A-Za-z0-9_./?=&%:-]{4,})(?P=q)''',re.I)

def same_site(u:str)->bool:
    h=(urlparse(u).hostname or '').lower()
    return h=='footiqo.com' or h.endswith('.footiqo.com')

def snippet(text:str,needle:str,radius:int=240):
    low=text.lower(); i=low.find(needle.lower())
    if i<0:return None
    s=max(0,i-radius); e=min(len(text),i+len(needle)+radius)
    return re.sub(r'\s+',' ',text[s:e])[:650]

def harvest(text:str,source:str):
    hits=[]
    low=text.lower()
    for k in KEYWORDS:
        if k in low:
            sn=snippet(text,k)
            if sn and sn not in [x['snippet'] for x in hits]: hits.append({'source':source,'keyword':k,'snippet':sn})
    eps=[]
    for m in ENDPOINT_RE.finditer(text):
        u=m.group('u')
        lu=u.lower()
        if any(k in lu for k in ['ajax','api','export','csv','excel','download','odds','table','data']):
            eps.append(u[:1000])
    return hits,sorted(set(eps))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--out-dir',required=True); a=ap.parse_args()
    out=Path(a.out_dir); out.mkdir(parents=True,exist_ok=True)
    session=requests.Session(); session.headers.update({'User-Agent':'Mozilla/5.0 C079A2 zero-label endpoint audit'})
    try:
        r=session.get(URL,timeout=45); r.raise_for_status(); html=r.text
        soup=BeautifulSoup(html,'lxml')
        # Critical zero-row boundary: never persist or inspect table-body cell text.
        for tb in soup.find_all('tbody'): tb.decompose()
        for tr in soup.find_all('tr'): tr.decompose()
        script_srcs=[]; inline=[]
        for s in soup.find_all('script'):
            src=(s.get('src') or '').strip()
            if src: script_srcs.append(urljoin(URL,src))
            else:
                tx=s.string or s.get_text(' ',strip=False)
                if tx: inline.append(tx)
        tag_meta=[]
        for tag in soup.find_all(['table','form','button','a']):
            attrs={k:str(v)[:800] for k,v in tag.attrs.items() if k.lower() in {'id','class','data-url','data-ajax','data-endpoint','href','action','name','type','data-table','data-export'}}
            if attrs:
                ss=json.dumps(attrs,ensure_ascii=False).lower()
                if any(k in ss for k in KEYWORDS): tag_meta.append({'tag':tag.name,'attrs':attrs})
        hits=[]; endpoints=[]
        for i,tx in enumerate(inline):
            h,e=harvest(tx,f'inline_script_{i}'); hits+=h; endpoints+=e
        js_reports=[]
        for u in sorted(set(script_srcs)):
            if not same_site(u): continue
            try:
                jr=session.get(u,timeout=30); ct=jr.headers.get('content-type','')
                if jr.status_code!=200 or len(jr.content)>8_000_000: continue
                tx=jr.text
                h,e=harvest(tx,u)
                if h or e:
                    js_reports.append({'url':u,'http_status':jr.status_code,'content_type':ct,'hits':h[:80],'endpoint_candidates':e[:80]})
                    hits+=h; endpoints+=e
            except Exception as ex:
                js_reports.append({'url':u,'error':repr(ex)})
        endpoints=sorted(set(endpoints))
        concrete=[]
        for u in endpoints:
            lu=u.lower()
            if any(x in lu for x in ['ajax','wp-json','/api/','export','download','.csv','.xlsx','.xls']): concrete.append(u)
        client_full=any(any(x in (h['snippet'].lower()) for x in ['serverSide: false','serverside:false','data:','rows:']) for h in hits)
        if concrete: terminal='ENDPOINT_DISCOVERED'
        elif client_full: terminal='CLIENT_SIDE_FULL_DATA_INDICATED'
        else: terminal='NO_PROGRAMMATIC_ENDPOINT_FOUND'
        summary={
          'schema_version':'C079A2_ENDPOINT_AUDIT_V1','terminal':terminal,'target_url':URL,'http_status':r.status_code,
          'script_src_count':len(script_srcs),'same_site_script_report_count':len(js_reports),'config_hit_count':len(hits),'endpoint_candidate_count':len(endpoints),'concrete_endpoint_candidates':concrete[:100],
          'tag_metadata':tag_meta[:100],'inline_config_hits':hits[:120],'js_reports':js_reports[:100],
          'hard_boundary':{'table_body_rows_removed_before_text_analysis':True,'match_row_values_persisted':False,'FTHG_FTAG_FTR_parsed':False,'odds_rows_exported':False,'result_targets_computed':False,'model_fit':False,'authentication_attempted':False,'premium_bypass_attempted':False},
          'formal_weight':0,'next':'If endpoint discovered, freeze a separate C079-A3 zero-label full-volume retrieval gate using the unchanged C079-A scientific source gates.'
        }
    except Exception as e:
        summary={'schema_version':'C079A2_ENDPOINT_AUDIT_V1','terminal':'ENGINEERING_ERROR','error':repr(e),'hard_boundary':{'match_row_values_persisted':False,'FTHG_FTAG_FTR_parsed':False,'odds_rows_exported':False,'result_targets_computed':False,'model_fit':False,'authentication_attempted':False,'premium_bypass_attempted':False},'formal_weight':0}
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
