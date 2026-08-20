#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,hashlib,json
from pathlib import Path
import numpy as np
import pandas as pd

CODES=['EC','T1','G1']
ID=['Date','HomeTeam','AwayTeam']
MKT=['Avg>2.5','Avg<2.5','AvgC>2.5','AvgC<2.5']
START=pd.Timestamp('2025-07-01'); END=pd.Timestamp('2026-06-30')

def sha256(p:Path):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''): h.update(b)
 return h.hexdigest()
def ids_sha(keys): return hashlib.sha256(('\n'.join(sorted(keys))+'\n').encode()).hexdigest()
def devig(o,u):
 io=1.0/o; iu=1.0/u; return io/(io+iu)
def logit(p):
 p=np.clip(np.asarray(p,float),1e-9,1-1e-9); return np.log(p/(1-p))

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--source-dir',required=True); ap.add_argument('--out-dir',required=True); a=ap.parse_args()
 root=Path(a.source_dir); out=Path(a.out_dir); out.mkdir(parents=True,exist_ok=True)
 rows=[]; keys=[]; reports={}; parsed_total=0; valid_total=0; date_valid_total=0; nonzero_total=0; files75=0; all_in_window=True
 for code in CODES:
  p=root/f'{code}.csv'
  if not p.is_file() or p.stat().st_size==0: raise RuntimeError(f'missing {code}')
  with p.open('r',encoding='utf-8-sig',errors='replace',newline='') as f: hdr=next(csv.reader(f))
  req=ID+MKT+['FTHG','FTAG']; missing=[c for c in req if c not in hdr]
  if missing: raise RuntimeError(f'{code} missing {missing}')
  use=ID+MKT
  d=pd.read_csv(p,usecols=use,dtype=str,low_memory=False)
  if {'FTHG','FTAG','FTR','HTHG','HTAG','HTR'}.intersection(d.columns): raise RuntimeError('result column materialized')
  # Separate csv reader only for boolean score-pair presence; values never converted/stored.
  completed=[]
  with p.open('r',encoding='utf-8-sig',errors='replace',newline='') as f:
   rd=csv.DictReader(f)
   for r in rd: completed.append(bool((r.get('FTHG') or '').strip()) and bool((r.get('FTAG') or '').strip()))
  if len(completed)!=len(d): raise RuntimeError('row alignment mismatch')
  idok=d[ID].notna().all(axis=1)
  for c in ID: idok &= d[c].astype(str).str.strip().ne('')
  dates=pd.to_datetime(d['Date'],errors='coerce',dayfirst=True); dateok=dates.notna(); date_valid_total += int((idok&dateok).sum())
  parsed=int(idok.sum()); parsed_total+=parsed
  num=pd.DataFrame({c:pd.to_numeric(d[c],errors='coerce') for c in MKT},index=d.index)
  mok=num.notna().all(axis=1)&(num>1.0).all(axis=1)
  comp=pd.Series(completed,index=d.index,dtype=bool)
  ok=idok&dateok&mok&comp
  valid=int(ok.sum()); valid_total+=valid; cov=valid/parsed if parsed else 0.; files75 += int(cov>=.75)
  ix=ok[ok].index
  if len(ix):
   o=devig(num.loc[ix,'Avg>2.5'].to_numpy(float),num.loc[ix,'Avg<2.5'].to_numpy(float)); c=devig(num.loc[ix,'AvgC>2.5'].to_numpy(float),num.loc[ix,'AvgC<2.5'].to_numpy(float)); mv=logit(c)-logit(o); nonzero_total += int(np.sum(np.abs(mv)>1e-12))
  fkeys=[]
  for i in ix:
   dt=dates.loc[i]
   if not(START<=dt<=END): all_in_window=False
   r=d.loc[i]; key=f"{code}|{str(r['Date']).strip()}|{str(r['HomeTeam']).strip()}|{str(r['AwayTeam']).strip()}"; keys.append(key); fkeys.append(key)
   rows.append({'identity_key':key,'code':code,'Date':str(r['Date']).strip(),'HomeTeam':str(r['HomeTeam']).strip(),'AwayTeam':str(r['AwayTeam']).strip(),**{m:str(r[m]).strip() for m in MKT}})
  reports[code]={'parsed_identity_count':parsed,'frozen_valid_count':valid,'valid_fraction':cov,'identity_sha256':ids_sha(fkeys),'raw_sha256':sha256(p),'raw_bytes':p.stat().st_size,'target_result_numeric_materialized':0}
 dup=len(keys)-len(set(keys)); frac=valid_total/parsed_total if parsed_total else 0.; move=nonzero_total/valid_total if valid_total else 0.
 gate={'file_count_3':len(reports)==3,'frozen_valid_ge_700':valid_total>=700,'duplicates_zero':dup==0,'valid_dates_ge_0_995':date_valid_total/max(parsed_total,1)>=.995,'overall_valid_fraction_ge_0_80':frac>=.80,'files_coverage_ge_0_75_at_least_2':files75>=2,'movement_rate_ge_0_05':move>=.05,'all_dates_in_2526_window':all_in_window,'target_result_numeric_materialized_zero':True}
 status='PASS_SUPPLEMENTAL_ZERO_LABEL_SOURCE' if all(gate.values()) else 'STOP_SUPPLEMENTAL_SOURCE'
 rows=sorted(rows,key=lambda x:x['identity_key']); snap=out/'supplemental_market_snapshot.csv'; fields=['identity_key','code','Date','HomeTeam','AwayTeam']+MKT
 with snap.open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
 s={'schema_version':'C078E3_SUPPLEMENTAL_SOURCE_V1','status':status,'frozen_identity_count':valid_total,'frozen_identity_sha256':ids_sha(keys),'duplicate_count':dup,'parsed_identity_count':parsed_total,'valid_fraction':frac,'valid_date_fraction':date_valid_total/max(parsed_total,1),'files_ge75':files75,'nonzero_movement_rate':move,'market_snapshot_sha256':sha256(snap),'files':reports,'gate':gate,'label_boundary':{'numeric_FTHG_FTAG_access':False,'numeric_result_storage':False,'goal_totals_computed':False,'tail_membership_computed':False,'model_fit':False,'score_pair_presence_boolean_only':True},'formal_weight':0}
 (out/'summary.json').write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n'); print(json.dumps(s,ensure_ascii=False,indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
