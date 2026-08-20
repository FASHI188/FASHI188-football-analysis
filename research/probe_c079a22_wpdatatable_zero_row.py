#!/usr/bin/env python3
from __future__ import annotations
import argparse,json
from pathlib import Path
import requests

ENDPOINT='https://footiqo.com/wp-admin/admin-ajax.php'
TABLES=[1370,1371]

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--out-dir',required=True); a=ap.parse_args(); out=Path(a.out_dir); out.mkdir(parents=True,exist_ok=True)
 s=requests.Session(); s.headers.update({'User-Agent':'Mozilla/5.0 C079A2.2 zero-row public-table probe','Referer':'https://footiqo.com/database/leagues/argentina-liga-profesional/'})
 reports=[]; usable=0
 for tid in TABLES:
  params={'action':'get_wdtable','table_id':str(tid)}
  # length=0 intentionally requests no table rows; draw/start are standard DataTables metadata fields.
  data={'draw':'1','start':'0','length':'0'}
  try:
   r=s.post(ENDPOINT,params=params,data=data,timeout=45)
   ctype=r.headers.get('content-type',''); obj=None; err=None
   try: obj=r.json()
   except Exception as e: err=repr(e)
   rep={'table_id':tid,'http_status':r.status_code,'content_type':ctype,'response_bytes':len(r.content),'json_parsed':obj is not None}
   if isinstance(obj,dict):
    # Persist metadata keys/counts only; never persist data row values.
    rep['json_keys']=sorted(obj.keys())
    for k in ['draw','recordsTotal','recordsFiltered','iTotalRecords','iTotalDisplayRecords']:
     if k in obj and isinstance(obj[k],(int,float,str,type(None))): rep[k]=obj[k]
    rows=None
    for k in ['data','aaData']:
     if k in obj and isinstance(obj[k],list): rows=len(obj[k]); rep[f'{k}_row_count']=rows
    if rows==0 and any(k in obj for k in ['recordsTotal','iTotalRecords']): usable+=1
   else:
    rep['json_error']=err
    rep['body_prefix_metadata_only']=r.text[:300].replace('\n',' ')
   reports.append(rep)
  except Exception as e:
   reports.append({'table_id':tid,'error':repr(e)})
 terminal='ZERO_ROW_METADATA_ENDPOINT_CONFIRMED' if usable>=1 else 'ZERO_ROW_PROBE_NOT_CONFIRMED'
 summary={'schema_version':'C079A22_ZERO_ROW_PROBE_V1','terminal':terminal,'endpoint':ENDPOINT,'reports':reports,
 'hard_boundary':{'requested_length':0,'odds_rows_requested':False,'odds_row_values_persisted':False,'score_rows_requested':False,'score_values_parsed':False,'result_targets_computed':False,'authentication_attempted':False,'premium_bypass_attempted':False,'model_fit':False},'formal_weight':0}
 (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n'); print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
