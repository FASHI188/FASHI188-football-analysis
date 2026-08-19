#!/usr/bin/env python3
from __future__ import annotations

import argparse, csv, hashlib, json
from pathlib import Path

SCHEMA="C076D_FOOTBALLDATA_2526_LOWER_SOURCE_GATE_V1"
CODES=["E1","E2","E3","SC0","SC1","SC2","SC3","D2","I2","SP2","F2","P1"]
VIEWED={"E0","SP1","I1","D1","F1","N1","B1"}
REQ_ID=["Date","HomeTeam","AwayTeam"]
REQ_SCORE=["FTHG","FTAG"]

def sha256(p:Path):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
 return h.hexdigest()
def ids_sha(keys): return hashlib.sha256(("\n".join(sorted(keys))+"\n").encode()).hexdigest()

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--source-dir',required=True); ap.add_argument('--out-dir',required=True); a=ap.parse_args(); root=Path(a.source_dir); out=Path(a.out_dir); out.mkdir(parents=True,exist_ok=True)
 if set(CODES)&VIEWED: raise RuntimeError('code overlap with C074-G viewed leagues')
 keys=[]; reports={}; total_present=0; total_rows=0
 for code in CODES:
  p=root/f'{code}.csv'
  if not p.is_file() or p.stat().st_size==0: raise RuntimeError(f'missing/empty {code}.csv')
  n=present=0; fkeys=[]
  with p.open('r',encoding='utf-8-sig',errors='replace',newline='') as f:
   reader=csv.DictReader(f)
   header=reader.fieldnames or []
   missing=[c for c in REQ_ID+REQ_SCORE if c not in header]
   if missing: raise RuntimeError(f'{code} missing columns {missing}')
   for row in reader:
    date=(row.get('Date') or '').strip(); home=(row.get('HomeTeam') or '').strip(); away=(row.get('AwayTeam') or '').strip()
    if not(date and home and away): continue
    key=f'{code}|{date}|{home}|{away}'; keys.append(key); fkeys.append(key); n+=1
    # Presence-only. These strings are neither converted nor retained in any output.
    score_pair_present=bool((row.get('FTHG') or '').strip()) and bool((row.get('FTAG') or '').strip())
    present+=int(score_pair_present)
  frac=present/n if n else 0.0; total_rows+=n; total_present+=present
  reports[code]={'identity_count':n,'score_pair_presence_count':present,'score_pair_presence_fraction':frac,'raw_sha256':sha256(p),'raw_bytes':p.stat().st_size,'identity_sha256':ids_sha(fkeys)}
 dup=len(keys)-len(set(keys)); overall=total_present/total_rows if total_rows else 0.0; minfile=min(v['score_pair_presence_fraction'] for v in reports.values()) if reports else 0.0
 gate={'fixed_file_count_12':len(reports)==12,'identity_count_ge_3500':total_rows>=3500,'duplicate_identity_count_zero':dup==0,'overall_score_pair_presence_ge_0_99':overall>=.99,'each_file_score_pair_presence_ge_0_98':minfile>=.98,'C074G_code_overlap_zero':not(set(CODES)&VIEWED)}
 passed=all(gate.values())
 s={'schema_version':SCHEMA,'status':'PASS_ZERO_VALUE_FRESH_SOURCE_GATE' if passed else 'FAIL_SOURCE_GATE','fixed_codes':CODES,'identity_count':total_rows,'identity_sha256':ids_sha(keys),'duplicate_identity_count':dup,'score_pair_presence_count':total_present,'score_pair_presence_fraction':overall,'minimum_file_presence_fraction':minfile,'files':reports,'gate':gate,'label_boundary':{'FTHG_FTAG_numeric_conversion':False,'FTHG_FTAG_values_stored':False,'FTHG_FTAG_values_hashed':False,'goal_totals_computed':False,'tail_membership_computed':False,'model_fit':False,'only_nonempty_score_pair_presence_used':True},'protected_boundaries':{'C075C_consumed_tail_labels_reused':False,'C075E_consumed_tail_labels_reused':False,'C071_reserve_52180_opened':False,'C070F_confirmation1597_opened':False,'A05_opened':False,'protected_opened':False,'unified_matrix_generated':False,'formal_weight':0},'next_if_pass':'keep score values sealed until C076-C passes and a separate one-shot confirmation contract is frozen'}
 (out/'summary.json').write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 with (out/'identity_manifest.jsonl').open('w',encoding='utf-8') as f:
  for k in sorted(keys): f.write(json.dumps({'identity_key':k},ensure_ascii=False)+'\n')
 print(json.dumps(s,ensure_ascii=False,indent=2)); return 0 if passed else 2
if __name__=='__main__': raise SystemExit(main())
