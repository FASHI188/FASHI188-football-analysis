#!/usr/bin/env python3
from __future__ import annotations
import csv,hashlib,json,sys
from datetime import datetime
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
OUT=HERE/'results'
START='2026-04-27';N=100
DIVS=('E0','D1','I1','SP1','F1')
FILES={
 'E0':ROOT/'raw'/'ENG_PremierLeague'/'2025-26.csv',
 'D1':ROOT/'raw'/'GER_Bundesliga'/'2025-26.csv',
 'I1':ROOT/'raw'/'ITA_SerieA'/'2025-26.csv',
 'SP1':ROOT/'raw'/'ESP_LaLiga'/'2025-26.csv',
 'F1':ROOT/'raw'/'FRA_Ligue1'/'2025-26.csv',
}
SAFE=('Date','HomeTeam','AwayTeam')

def date(s):
 for f in ('%d/%m/%Y','%d/%m/%y'):
  try:return datetime.strptime(s.strip(),f).date().isoformat()
  except ValueError:pass
 raise ValueError(s)

def rows(div):
 p=FILES[div];z=[]
 with p.open(encoding='utf-8-sig',errors='replace',newline='') as fh:
  rd=csv.DictReader(fh)
  if not rd.fieldnames or not all(c in rd.fieldnames for c in SAFE):raise RuntimeError(f'missing safe columns {div}')
  for x in rd:
   ds=(x.get('Date') or '').strip();h=(x.get('HomeTeam') or '').strip();a=(x.get('AwayTeam') or '').strip()
   if not ds or not h or not a:continue
   d=date(ds)
   if d>=START:z.append({'date':d,'division':div,'home':h,'away':a})
 return z,str(p.relative_to(ROOT))

def sha(x):return hashlib.sha256(json.dumps(x,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def run():
 c=[];src={}
 for d in DIVS:
  q,p=rows(d);c+=q;src[d]=p
 c.sort(key=lambda x:(x['date'],x['division'],x['home'],x['away']))
 if len(c)<N:raise RuntimeError(f'insufficient candidates {len(c)}')
 lock=[{'batch_index':i,**x,'cutoff_rule':'T-24h from official kickoff; exact kickoff resolved before prediction','prediction_status':'UNRESEARCHED_UNSCORED'} for i,x in enumerate(c[:N],1)]
 s={
  'schema_version':'football3-batch004-lock-v1','status':'LOCKED',
  'purpose':'fresh fourth independent 100-match pseudo-prospective batch for historically prevalidated S92 draw gate',
  'selection':{'season':'2025-26','divisions_predeclared':list(DIVS),'start_date_predeclared':START,'target_rows':N,'ordering':['date','division','home','away'],'selected_rows':N,'first_date':lock[0]['date'],'last_date':lock[-1]['date']},
  'governance':{
   'safe_fields_accessed':list(SAFE),
   'source_files_contain_other_fields_but_not_accessed_for_selection':True,
   'outcome_fields_accessed':False,
   'selection_uses_results':False,
   'selection_uses_odds':False,
   'selection_uses_postmatch_stats':False,
   'cohort_locked_before_Batch004_candidate_predictions':True,
   'reveal_only_after_all_candidate_predictions_locked':True,
   'Batch002_results_not_used_for_Batch004_row_selection':True,
   'Batch003_results_not_used_for_Batch004_row_selection':True,
   'S92_threshold_fixed_before_Batch004_selection':True,
  },
  'sources':src,'cohort_sha256':sha(lock),'rows':lock}
 OUT.mkdir(parents=True,exist_ok=True);(OUT/'batch004_locked_100.json').write_text(json.dumps(s,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
 print(json.dumps({'status':'LOCKED','selection':s['selection'],'cohort_sha256':s['cohort_sha256']},indent=2))

def verify():
 s=json.loads((OUT/'batch004_locked_100.json').read_text(encoding='utf-8'));g=s['governance']
 assert s['status']=='LOCKED' and len(s['rows'])==100
 assert not g['outcome_fields_accessed'] and not g['selection_uses_results'] and not g['selection_uses_odds']
 assert g['S92_threshold_fixed_before_Batch004_selection']
 assert s['cohort_sha256']==sha(s['rows']);assert s['selection']['first_date']>=START
 print('BATCH004_LOCK_VERIFY_PASS')

if __name__=='__main__':
 if len(sys.argv)!=2 or sys.argv[1] not in {'run','verify'}:raise SystemExit('usage: lock_batch004.py {run|verify}')
 {'run':run,'verify':verify}[sys.argv[1]]()
