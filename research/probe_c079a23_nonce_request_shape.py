#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,re
from pathlib import Path
import requests
from bs4 import BeautifulSoup

PAGE='https://footiqo.com/database/leagues/argentina-liga-profesional/'
AJAX='https://footiqo.com/wp-admin/admin-ajax.php'

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--out-dir',required=True); a=ap.parse_args(); out=Path(a.out_dir); out.mkdir(parents=True,exist_ok=True)
 s=requests.Session(); s.headers.update({'User-Agent':'Mozilla/5.0 C079A2.3 public-table shape probe','Referer':PAGE})
 r=s.get(PAGE,timeout=45); r.raise_for_status(); soup=BeautifulSoup(r.text,'lxml')
 # Never inspect body rows; headers/config only.
 table_map=[]
 for t in soup.find_all('table'):
  cls=' '.join(t.get('class') or []); ids=re.findall(r'wpDataTableID-(\d+)',cls)
  if not ids: continue
  headers=[]
  thead=t.find('thead')
  if thead: headers=[th.get_text(' ',strip=True) for th in thead.find_all('th')]
  table_map.append({'dom_id':t.get('id'),'wp_table_id':int(ids[0]),'headers':headers})
 for tb in soup.find_all('tbody'): tb.decompose()
 for tr in soup.find_all('tr'): tr.decompose()
 nonce_inputs=[]
 for inp in soup.find_all('input'):
  ident=((inp.get('id') or '')+' '+(inp.get('name') or '')).lower()
  if 'nonce' in ident or 'wdt' in ident:
   nonce_inputs.append({'id':inp.get('id'),'name':inp.get('name'),'value':inp.get('value')})
 nonce=None
 for x in nonce_inputs:
  if (x.get('id') or '').lower()=='wdtnonce' and x.get('value'): nonce=x['value']; break
 if nonce is None:
  for x in nonce_inputs:
   if x.get('value') and 'nonce' in (((x.get('id') or '')+' '+(x.get('name') or '')).lower()): nonce=x['value']; break
 odds_tables=[]
 for tm in table_map:
  h={x.strip() for x in tm['headers']}
  if {'O25','U25','O35','U35','O45','U45'}.issubset(h): odds_tables.append(tm)
 reports=[]
 for tm in odds_tables:
  tid=tm['wp_table_id']
  data={'draw':'1','start':'0','length':'1'}
  if nonce: data['wdtNonce']=nonce
  try:
   rr=s.post(AJAX,params={'action':'get_wdtable','table_id':str(tid)},data=data,timeout=45)
   rep={'table_id':tid,'http_status':rr.status_code,'content_type':rr.headers.get('content-type',''),'response_bytes':len(rr.content),'nonce_sent':bool(nonce)}
   try:
    obj=rr.json(); rep['json_parsed']=True; rep['json_keys']=sorted(obj.keys()) if isinstance(obj,dict) else []
    if isinstance(obj,dict):
     for k in ['draw','recordsTotal','recordsFiltered','iTotalRecords','iTotalDisplayRecords']:
      if k in obj and isinstance(obj[k],(int,float,str,type(None))): rep[k]=obj[k]
     for k in ['data','aaData']:
      if isinstance(obj.get(k),list):
       rep[f'{k}_row_count']=len(obj[k])
       if obj[k]:
        row=obj[k][0]
        # Persist schema only, never row values.
        rep[f'{k}_first_row_type']=type(row).__name__
        rep[f'{k}_first_row_keys']=sorted(row.keys()) if isinstance(row,dict) else None
   except Exception as e:
    rep['json_parsed']=False; rep['json_error']=repr(e); rep['body_prefix']=rr.text[:200].replace('\n',' ')
   reports.append(rep)
  except Exception as e: reports.append({'table_id':tid,'error':repr(e),'nonce_sent':bool(nonce)})
 confirmed=sum(bool(x.get('json_parsed')) and any(k in x for k in ['recordsTotal','iTotalRecords']) for x in reports)
 terminal='ODDS_AJAX_REQUEST_SHAPE_CONFIRMED' if confirmed>=1 else 'ODDS_AJAX_REQUEST_SHAPE_NOT_CONFIRMED'
 summary={'schema_version':'C079A23_NONCE_SHAPE_V1','terminal':terminal,'page_http_status':r.status_code,'nonce_found':bool(nonce),'nonce_input_count':len(nonce_inputs),'table_header_map':table_map,'odds_tables':odds_tables,'reports':reports,
 'hard_boundary':{'tbody_removed_before_text_analysis':True,'requested_rows_per_odds_table':1,'only_confirmed_odds_tables_requested':True,'odds_row_values_persisted':False,'only_response_schema_and_counts_persisted':True,'score_result_table_requested':False,'score_values_parsed':False,'result_targets_computed':False,'authentication_attempted':False,'premium_bypass_attempted':False,'model_fit':False},'formal_weight':0}
 (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n'); print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
