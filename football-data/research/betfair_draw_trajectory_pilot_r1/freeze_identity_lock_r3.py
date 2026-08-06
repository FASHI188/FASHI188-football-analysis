#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, importlib.util, json, os, sys
from pathlib import Path
from typing import Any

R3A=Path(__file__).with_name('diagnose_coverage_r3a.py')
CONFIG={
 'start':{'cutoff_minutes':30,'maximum_single_runner_staleness_seconds':1800,'maximum_home_draw_away_observation_span_seconds':600},
 'end':{'cutoff_minutes':5,'maximum_single_runner_staleness_seconds':600,'maximum_home_draw_away_observation_span_seconds':600}}

def module()->Any:
 s=importlib.util.spec_from_file_location('r3_lock_helper',R3A)
 if s is None or s.loader is None: raise RuntimeError('R3A unavailable')
 m=importlib.util.module_from_spec(s); sys.modules[s.name]=m; s.loader.exec_module(m); return m

def passes(f:dict[str,Any],g:dict[str,int])->bool:
 return bool(f['complete'] and f['max_age_seconds']<=g['maximum_single_runner_staleness_seconds'] and f['span_seconds']<=g['maximum_home_draw_away_observation_span_seconds'])

def lsha(values:list[str])->str:
 return hashlib.sha256(''.join(v+'\n' for v in values).encode()).hexdigest()

def dump(path:Path,v:dict[str,Any])->None:
 path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_suffix(path.suffix+'.tmp')
 with tmp.open('w',encoding='utf-8',newline='\n') as f:
  f.write(json.dumps(v,ensure_ascii=False,indent=2,sort_keys=True)+'\n'); f.flush(); os.fsync(f.fileno())
 tmp.replace(path)

def run(checkout:Path,output:Path)->None:
 m=module(); h=m.helper(); cfg=m.load(m.HELPER_CFG); files=m.candidate_files(checkout); hashes=[]; parse=0
 for path in files:
  try: p=m.parse_market(path,h,cfg)
  except Exception: parse+=1; continue
  sf=m.snapshot_features(p,CONFIG['start']['cutoff_minutes']); ef=m.snapshot_features(p,CONFIG['end']['cutoff_minutes'])
  if passes(sf,CONFIG['start']) and passes(ef,CONFIG['end']):
   rel=path.relative_to(checkout).as_posix(); hashes.append(hashlib.sha256(rel.encode()).hexdigest())
 hashes=sorted(hashes)
 if len(hashes)!=63 or len(set(hashes))!=63: raise RuntimeError(f'expected 63 unique identities, got {len(hashes)}/{len(set(hashes))}')
 lock={
  'schema_version':'BETFAIR-DRAW-TRAJECTORY-IDENTITY-LOCK-R3',
  'status':'LOCKED_BEFORE_WINNER_LABEL_ACCESS',
  'source_commit':m.SOURCE_COMMIT,
  'identity_contract':'sha256(source-relative-path)',
  'eligible_count':63,
  'ordered_identity_hashes':hashes,
  'ordered_identity_hashes_sha256':lsha(hashes),
  'snapshot_contract':CONFIG,
  'baseline':{'id':'DRAW_FAIR_T5','formula':'qD_T5'},
  'candidate':{'id':'DRAW_T5_PLUS_HALF_T30_MOVE','coefficient':0.5,'formula':'clip(qD_T5 + 0.5 * (qD_T5 - qD_T30))'},
  'candidate_files':len(files),'parse_or_identity_exclusions':parse,
  'winner_labels_read':0,'post_kickoff_messages_parsed':0,'raw_paths_names_prices_or_stream_messages_persisted':False,
  'model_fits':0,'thresholds_selected':0,'formal_weight':0,'formal_model_changes':0,'formal_data_changes':0,'formal_config_changes':0,'CURRENT_changes':0}
 dump(output,lock); print(json.dumps(lock,ensure_ascii=False,sort_keys=True))

def main()->int:
 p=argparse.ArgumentParser(); p.add_argument('--source-checkout',type=Path,required=True); p.add_argument('--output',type=Path,required=True); a=p.parse_args(); run(a.source_checkout,a.output); return 0
if __name__=='__main__': raise SystemExit(main())
