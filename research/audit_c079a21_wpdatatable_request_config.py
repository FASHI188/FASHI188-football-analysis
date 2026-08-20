#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,re
from pathlib import Path
from urllib.parse import urljoin,urlparse
import requests
from bs4 import BeautifulSoup

URL='https://footiqo.com/database/leagues/argentina-liga-profesional/'
TOKENS=['action=get_wdtable','get_wdtable','dataTableParams','tableWpId','tableDescription','serverSide','ajax.url','ajaxUrl','wpDataTableID-','wpDataTables[','wpDataTable_','table_9','1191','1370','1371']

def compact(s): return re.sub(r'\s+',' ',s)
def contexts(text,token,r=900):
 out=[]; low=text.lower(); t=token.lower(); pos=0
 while True:
  i=low.find(t,pos)
  if i<0:break
  out.append(compact(text[max(0,i-r):min(len(text),i+len(token)+r)])[:2200]); pos=i+len(t)
 return out

def same_site(u):
 h=(urlparse(u).hostname or '').lower(); return h=='footiqo.com' or h.endswith('.footiqo.com')

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--out-dir',required=True); a=ap.parse_args(); out=Path(a.out_dir); out.mkdir(parents=True,exist_ok=True)
 s=requests.Session(); s.headers.update({'User-Agent':'Mozilla/5.0 C079A2.1 zero-label config audit'})
 r=s.get(URL,timeout=45); r.raise_for_status(); soup=BeautifulSoup(r.text,'lxml')
 # Remove row values before any extraction.
 for tb in soup.find_all('tbody'): tb.decompose()
 for tr in soup.find_all('tr'): tr.decompose()
 sources=[]
 for i,sc in enumerate(soup.find_all('script')):
  src=(sc.get('src') or '').strip()
  if src:
   u=urljoin(URL,src)
   if same_site(u):
    try:
     rr=s.get(u,timeout=30)
     if rr.status_code==200 and len(rr.content)<=8_000_000: sources.append((u,rr.text))
    except Exception: pass
  else:
   tx=sc.string or sc.get_text(' ',strip=False)
   if tx:sources.append((f'inline_{i}',tx))
 hits=[]
 for name,tx in sources:
  matched={}
  for tok in TOKENS:
   cs=contexts(tx,tok)
   if cs: matched[tok]=cs[:12]
  if matched:hits.append({'source':name,'matches':matched})
 # Extract exact query fragments around get_wdtable without executing them.
 query_fragments=[]
 patterns=[r'action=get_wdtable[^\"\'\s<>{}]{0,1200}',r'get_wdtable[^\"\'\s<>{}]{0,1200}',r'ajax\s*:\s*\{.{0,1800}?\}',r'dataTableParams\s*[:=].{0,2400}']
 for name,tx in sources:
  for pat in patterns:
   for m in re.finditer(pat,tx,re.I|re.S):
    q=compact(m.group(0))[:2600]
    if q not in [x['fragment'] for x in query_fragments]: query_fragments.append({'source':name,'pattern':pat,'fragment':q})
 table_meta=[]
 for t in soup.find_all('table'):
  cls=' '.join(t.get('class') or []); tid=t.get('id') or ''
  if 'wpDataTable' in cls or 'wpDataTableID-' in cls:
   ids=re.findall(r'wpDataTableID-(\d+)',cls)
   table_meta.append({'dom_id':tid,'wp_table_ids':ids,'class':cls})
 concrete=[x for x in query_fragments if 'get_wdtable' in x['fragment'].lower()]
 terminal='REQUEST_CONFIG_DISCOVERED' if concrete else 'REQUEST_CONFIG_NOT_FOUND'
 summary={'schema_version':'C079A21_REQUEST_CONFIG_V1','terminal':terminal,'page':URL,'http_status':r.status_code,'table_meta':table_meta,'config_sources_with_hits':len(hits),'hits':hits[:100],'query_fragments':query_fragments[:150],'concrete_get_wdtable_fragments':concrete[:50],
 'hard_boundary':{'table_body_rows_removed_before_extraction':True,'match_row_values_persisted':False,'score_fields_parsed':False,'odds_rows_requested':False,'ajax_data_request_executed':False,'authentication_attempted':False,'premium_bypass_attempted':False,'model_fit':False},'formal_weight':0}
 (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n'); print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
