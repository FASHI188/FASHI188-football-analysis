#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,hashlib,json
from pathlib import Path
import numpy as np
import pandas as pd

CODES=["E1","E2","E3","SC0","SC1","SC2","SC3","D2","I2","SP2","F2","P1"]
ID_COLS=["Date","HomeTeam","AwayTeam"]
MARKET_COLS=["Avg>2.5","Avg<2.5","AvgC>2.5","AvgC<2.5"]
OPTIONAL=["Div","Time"]
EXPECTED_N=4184
EXPECTED_SHA="7762c0f94adf3e734d7fce7f73dd203b61a761fafb733f717b939f3db35423ce"
SPLIT=pd.Timestamp("2026-01-01")
EXPECTED_EARLY=2065
EXPECTED_LATE=2119

def sha256(p:Path)->str:
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
 return h.hexdigest()

def ids_sha(keys:list[str])->str:
 return hashlib.sha256(("\n".join(sorted(keys))+"\n").encode()).hexdigest()

def header(p:Path)->list[str]:
 with p.open('r',encoding='utf-8-sig',errors='replace',newline='') as f: return next(csv.reader(f))

def devig_over(o,u):
 io=1/o; iu=1/u; return io/(io+iu)

def logit(p):
 p=np.clip(np.asarray(p,float),1e-9,1-1e-9); return np.log(p/(1-p))

def main()->int:
 ap=argparse.ArgumentParser(); ap.add_argument('--source-dir',required=True); ap.add_argument('--out-dir',required=True); a=ap.parse_args()
 root=Path(a.source_dir); out=Path(a.out_dir); out.mkdir(parents=True,exist_ok=True)
 rows=[]; keys=[]; reports={}; valid_dates=0; nonzero=0; early=0; late=0
 for code in CODES:
  p=root/f'{code}.csv'
  if not p.is_file() or p.stat().st_size==0: raise RuntimeError(f'missing/empty {code}')
  h=header(p); missing=[c for c in ID_COLS+MARKET_COLS if c not in h]
  if missing: raise RuntimeError(f'{code} missing {missing}')
  usecols=ID_COLS+MARKET_COLS+[c for c in OPTIONAL if c in h]
  d=pd.read_csv(p,usecols=usecols,dtype=str,low_memory=False)
  forbidden={'FTHG','FTAG','FTR','HTHG','HTAG','HTR'}
  if forbidden.intersection(d.columns): raise RuntimeError('outcome column materialized')
  idmask=d[ID_COLS].notna().all(axis=1)
  for c in ID_COLS: idmask &= d[c].astype(str).str.strip().ne('')
  di=d.loc[idmask].copy()
  dates=pd.to_datetime(di['Date'],errors='coerce',dayfirst=True); date_ok=dates.notna(); valid_dates += int(date_ok.sum())
  num=pd.DataFrame({c:pd.to_numeric(di[c],errors='coerce') for c in MARKET_COLS},index=di.index)
  market_ok=num.notna().all(axis=1)&(num>1.0).all(axis=1)&date_ok
  if not bool(market_ok.all()): raise RuntimeError(f'{code}: market rows not 100% valid')
  ix=market_ok[market_ok].index
  po=devig_over(num.loc[ix,'Avg>2.5'].to_numpy(float),num.loc[ix,'Avg<2.5'].to_numpy(float))
  pc=devig_over(num.loc[ix,'AvgC>2.5'].to_numpy(float),num.loc[ix,'AvgC<2.5'].to_numpy(float))
  mv=logit(pc)-logit(po); nonzero += int(np.sum(np.abs(mv)>1e-12))
  early += int((dates.loc[ix]<SPLIT).sum()); late += int((dates.loc[ix]>=SPLIT).sum())
  fkeys=[]
  for idx in ix:
   r=di.loc[idx]
   key=f"{code}|{str(r['Date']).strip()}|{str(r['HomeTeam']).strip()}|{str(r['AwayTeam']).strip()}"
   keys.append(key); fkeys.append(key)
   rows.append({
    'identity_key':key,'code':code,'Date':str(r['Date']).strip(),'HomeTeam':str(r['HomeTeam']).strip(),'AwayTeam':str(r['AwayTeam']).strip(),
    'Avg>2.5':str(r['Avg>2.5']).strip(),'Avg<2.5':str(r['Avg<2.5']).strip(),'AvgC>2.5':str(r['AvgC>2.5']).strip(),'AvgC<2.5':str(r['AvgC<2.5']).strip()
   })
  reports[code]={'identity_count':len(di),'identity_sha256':ids_sha(fkeys),'raw_sha256':sha256(p),'raw_bytes':p.stat().st_size,'selected_columns':usecols,'target_result_columns_materialized':0}
 dup=len(keys)-len(set(keys)); ident_sha=ids_sha(keys); n=len(keys); movement_rate=nonzero/n if n else 0.0; date_frac=valid_dates/n if n else 0.0
 rows=sorted(rows,key=lambda x:x['identity_key'])
 snap=out/'market_snapshot.csv'
 fields=['identity_key','code','Date','HomeTeam','AwayTeam']+MARKET_COLS
 with snap.open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
 snapshot_identity_sha=ids_sha([r['identity_key'] for r in rows])
 gate={
  'fixed_file_count_12':len(reports)==12,
  'identity_count_exact_4184':n==EXPECTED_N,
  'identity_sha_exact_expected':ident_sha==EXPECTED_SHA,
  'duplicate_identity_count_zero':dup==0,
  'valid_date_fraction_ge_0_995':date_frac>=0.995,
  'market_valid_count_exact_4184':len(rows)==EXPECTED_N,
  'market_valid_fraction_1_0':len(rows)==n,
  'nonzero_movement_rate_ge_0_05':movement_rate>=0.05,
  'early_exact_2065':early==EXPECTED_EARLY,
  'late_exact_2119':late==EXPECTED_LATE,
  'snapshot_identity_sha_matches':snapshot_identity_sha==ident_sha,
  'target_result_columns_materialized_zero':all(v['target_result_columns_materialized']==0 for v in reports.values()),
 }
 passed=all(gate.values())
 s={
  'schema_version':'C078D_FREEZE_FRESH4184_V1','status':'PASS_FRESH4184_SNAPSHOT_FREEZE' if passed else 'STOP_SOURCE_DRIFT',
  'identity_count':n,'identity_sha256':ident_sha,'duplicate_identity_count':dup,'valid_date_fraction':date_frac,
  'market_valid_count':len(rows),'market_valid_fraction':len(rows)/n if n else 0.0,'nonzero_movement_rate':movement_rate,
  'split_date':'2026-01-01','early_market_valid_count':early,'late_market_valid_count':late,
  'market_snapshot_sha256':sha256(snap),'market_snapshot_identity_sha256':snapshot_identity_sha,'files':reports,'gate':gate,
  'label_boundary':{'target_result_columns_materialized':0,'FTHG_FTAG_numeric_conversion':False,'FTHG_FTAG_values_stored':False,'FTHG_FTAG_values_hashed':False,'goal_totals_computed':False,'goal_difference_computed':False,'tail_membership_computed':False,'model_fit':False,'durable_output_contains_only_identity_and_market_values':True},
  'hard_boundaries':{'C077B_labels_read':False,'C071_reserve52180_opened':False,'C070F1597_opened':False,'A05_or_protected_opened':False,'formal_weight':0,'CURRENT_change':False,'unified_matrix_generated':False},
  'next_if_pass':'commit immutable market_snapshot.csv and preregister C078-E calibration-to-confirmation before any numeric score access'
 }
 (out/'summary.json').write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(json.dumps(s,ensure_ascii=False,indent=2)); return 0 if passed else 2

if __name__=='__main__': raise SystemExit(main())
