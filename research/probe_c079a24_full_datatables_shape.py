#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,re
from pathlib import Path
import requests
from bs4 import BeautifulSoup

PAGE='https://footiqo.com/database/leagues/argentina-liga-profesional/'
AJAX='https://footiqo.com/wp-admin/admin-ajax.php'
REQ_HEADERS={'O25','U25','O35','U35','O45','U45'}

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--out-dir',required=True); a=ap.parse_args(); out=Path(a.out_dir); out.mkdir(parents=True,exist_ok=True)
 sess=requests.Session(); sess.headers.update({'User-Agent':'Mozilla/5.0 C079A2.4 public odds shape probe','Referer':PAGE,'X-Requested-With':'XMLHttpRequest'})
 r=sess.get(PAGE,timeout=45); r.raise_for_status(); soup=BeautifulSoup(r.text,'lxml')
 nonce=''
 x=soup.find(id='wdtNonce')
 if x is not None: nonce=(x.get('value') or '')
 odds=[]
 for t in soup.find_all('table'):
  cls=' '.join(t.get('class') or []); ids=re.findall(r'wpDataTableID-(\d+)',cls)
  if not ids: continue
  thead=t.find('thead'); headers=[th.get_text(' ',strip=True) for th in thead.find_all('th')] if thead else []
  if REQ_HEADERS.issubset(set(headers)): odds.append({'table_id':int(ids[0]),'headers':headers})
 # body rows never inspected.
 reports=[]
 for tm in odds:
  data={'draw':'1','start':'0','length':'1','search[value]':'','search[regex]':'false','order[0][column]':'0','order[0][dir]':'asc'}
  if nonce:data['wdtNonce']=nonce
  for i,h in enumerate(tm['headers']):
   data[f'columns[{i}][data]']=str(i)
   data[f'columns[{i}][name]']=h
   data[f'columns[{i}][searchable]']='true'
   data[f'columns[{i}][orderable]']='true'
   data[f'columns[{i}][search][value]']=''
   data[f'columns[{i}][search][regex]']='false'
  rr=sess.post(AJAX,params={'action':'get_wdtable','table_id':str(tm['table_id'])},data=data,timeout=45)
  rep={'table_id':tm['table_id'],'headers':tm['headers'],'http_status':rr.status_code,'content_type':rr.headers.get('content-type',''),'response_bytes':len(rr.content),'nonce_sent':bool(nonce)}
  try:
   obj=rr.json(); rep['json_parsed']=True; rep['json_keys']=sorted(obj.keys()) if isinstance(obj,dict) else []
   if isinstance(obj,dict):
    for k in ['draw','recordsTotal','recordsFiltered','iTotalRecords','iTotalDisplayRecords']:
     if k in obj and isinstance(obj[k],(int,float,str,type(None))): rep[k]=obj[k]
    for k in ['data','aaData']:
     if isinstance(obj.get(k),list):
      rep[f'{k}_row_count']=len(obj[k])
      if obj[k]:
       row=obj[k][0]; rep[f'{k}_first_row_type']=type(row).__name__; rep[f'{k}_first_row_keys']=sorted(row.keys()) if isinstance(row,dict) else None; rep[f'{k}_first_row_length']=len(row) if isinstance(row,(list,tuple,dict)) else None
  except Exception as e:
   rep['json_parsed']=False; rep['json_error']=repr(e); rep['body_prefix']=rr.text[:200].replace('\n',' ')
  reports.append(rep)
 ok=sum(x.get('json_parsed') and any(k in x for k in ['recordsTotal','iTotalRecords']) for x in reports)
 terminal='FULL_DATATABLES_REQUEST_SHAPE_CONFIRMED' if ok>=1 else 'FULL_DATATABLES_REQUEST_SHAPE_NOT_CONFIRMED'
 summary={'schema_version':'C079A24_FULL_DT_SHAPE_V1','terminal':terminal,'nonce_found':bool(nonce),'odds_table_count':len(odds),'reports':reports,
 'hard_boundary':{'requested_rows_per_odds_table':1,'only_odds_tables_requested':True,'odds_row_values_persisted':False,'response_row_schema_only_persisted':True,'score_result_table_requested':False,'score_values_parsed':False,'result_targets_computed':False,'authentication_attempted':False,'premium_bypass_attempted':False,'model_fit':False},'formal_weight':0}
 (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n'); print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
