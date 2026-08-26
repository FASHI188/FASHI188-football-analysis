#!/usr/bin/env python3
from __future__ import annotations
import csv,hashlib,io,json,sys,urllib.request
from datetime import datetime
from pathlib import Path
HERE=Path(__file__).resolve().parent;OUT=HERE/'results'
SEASON='2526';DIVS=('E0','D1','I1','SP1','F1');START='2026-03-16';N=100;BASE='https://www.football-data.co.uk/mmz4281';SAFE=('Date','HomeTeam','AwayTeam')
def date(s):
 for f in ('%d/%m/%Y','%d/%m/%y'):
  try:return datetime.strptime(s.strip(),f).date().isoformat()
  except ValueError:pass
 raise ValueError(s)
def rows(div):
 u=f'{BASE}/{SEASON}/{div}.csv';req=urllib.request.Request(u,headers={'User-Agent':'football3-batch002-lock/1.0'})
 with urllib.request.urlopen(req,timeout=60) as r:t=r.read().decode('utf-8-sig',errors='replace')
 rd=csv.DictReader(io.StringIO(t));z=[]
 if not rd.fieldnames or not all(c in rd.fieldnames for c in SAFE):raise RuntimeError(f'missing safe columns {div}')
 for x in rd:
  ds=(x.get('Date') or '').strip();h=(x.get('HomeTeam') or '').strip();a=(x.get('AwayTeam') or '').strip()
  if not ds or not h or not a:continue
  d=date(ds)
  if d>=START:z.append({'date':d,'division':div,'home':h,'away':a})
 return z,u
def sha(x):return hashlib.sha256(json.dumps(x,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
def run():
 c=[];src={}
 for d in DIVS:
  q,u=rows(d);c+=q;src[d]=u
 c.sort(key=lambda x:(x['date'],x['division'],x['home'],x['away']))
 if len(c)<N:raise RuntimeError(f'insufficient candidates {len(c)}')
 lock=[{'batch_index':i,**x,'cutoff_rule':'T-24h from official kickoff; exact kickoff resolved before research','prediction_status':'UNRESEARCHED_UNSCORED'} for i,x in enumerate(c[:N],1)]
 s={'schema_version':'football3-batch002-lock-v1','status':'LOCKED','purpose':'second independent 100-match pseudo-prospective batch after Batch-001 diagnosis','selection':{'season':SEASON,'divisions_predeclared':list(DIVS),'start_date_predeclared':START,'target_rows':N,'ordering':['date','division','home','away'],'selected_rows':N,'first_date':lock[0]['date'],'last_date':lock[-1]['date']},'governance':{'safe_columns_read':list(SAFE),'outcome_columns_read':False,'selection_uses_results':False,'selection_uses_odds':False,'selection_uses_postmatch_stats':False,'cohort_locked_before_Batch002_model_predictions':True,'reveal_only_after_all_candidate_predictions_locked':True},'sources':src,'cohort_sha256':sha(lock),'rows':lock}
 OUT.mkdir(parents=True,exist_ok=True);(OUT/'batch002_locked_100.json').write_text(json.dumps(s,indent=2,ensure_ascii=False)+'\n');print(json.dumps({'status':'LOCKED','selection':s['selection'],'cohort_sha256':s['cohort_sha256']},indent=2))
def verify():
 s=json.loads((OUT/'batch002_locked_100.json').read_text());g=s['governance'];assert s['status']=='LOCKED' and len(s['rows'])==100 and not g['outcome_columns_read'] and not g['selection_uses_results'] and not g['selection_uses_odds'] and s['cohort_sha256']==sha(s['rows']);print('BATCH002_LOCK_VERIFY_PASS')
if __name__=='__main__':
 if len(sys.argv)!=2 or sys.argv[1] not in {'run','verify'}:raise SystemExit('usage: lock_batch002.py {run|verify}')
 {'run':run,'verify':verify}[sys.argv[1]]()
