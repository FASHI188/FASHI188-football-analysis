#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

R3A = Path(__file__).with_name('diagnose_coverage_r3a.py')

CONFIGS = {
    'T45_S30_P10__T10_S10_P10': {'start': {'cutoff':45,'stale':1800,'span':600}, 'end': {'cutoff':10,'stale':600,'span':600}},
    'T45_S30_P5__T10_S10_P5': {'start': {'cutoff':45,'stale':1800,'span':300}, 'end': {'cutoff':10,'stale':600,'span':300}},
    'T45_S20_P10__T10_S10_P10': {'start': {'cutoff':45,'stale':1200,'span':600}, 'end': {'cutoff':10,'stale':600,'span':600}},
    'T45_S15_P10__T10_S10_P10': {'start': {'cutoff':45,'stale':900,'span':600}, 'end': {'cutoff':10,'stale':600,'span':600}},
    'T60_S30_P10__T10_S10_P10': {'start': {'cutoff':60,'stale':1800,'span':600}, 'end': {'cutoff':10,'stale':600,'span':600}},
    'T60_S45_P15__T10_S10_P10': {'start': {'cutoff':60,'stale':2700,'span':900}, 'end': {'cutoff':10,'stale':600,'span':600}},
    'T60_S60_P10__T15_S15_P10': {'start': {'cutoff':60,'stale':3600,'span':600}, 'end': {'cutoff':15,'stale':900,'span':600}},
    'T45_S30_P10__T15_S15_P10': {'start': {'cutoff':45,'stale':1800,'span':600}, 'end': {'cutoff':15,'stale':900,'span':600}},
    'T30_S30_P10__T5_S10_P10': {'start': {'cutoff':30,'stale':1800,'span':600}, 'end': {'cutoff':5,'stale':600,'span':600}},
}

def module() -> Any:
    spec=importlib.util.spec_from_file_location('r3a_refined_helper',R3A)
    if spec is None or spec.loader is None: raise RuntimeError('R3A module unavailable')
    mod=importlib.util.module_from_spec(spec); sys.modules[spec.name]=mod; spec.loader.exec_module(mod); return mod

def passes(features:dict[str,Any], gate:dict[str,int])->bool:
    return bool(features['complete'] and features['max_age_seconds']<=gate['stale'] and features['span_seconds']<=gate['span'])

def dump(path:Path,value:dict[str,Any])->None:
    path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_suffix(path.suffix+'.tmp')
    with tmp.open('w',encoding='utf-8',newline='\n') as f:
        f.write(json.dumps(value,ensure_ascii=False,indent=2,sort_keys=True)+'\n'); f.flush(); os.fsync(f.fileno())
    tmp.replace(path)

def run(checkout:Path,output:Path)->None:
    m=module(); h=m.helper(); cfg=m.load(m.HELPER_CFG); files=m.candidate_files(checkout)
    counts=Counter(); valid=0; parse=Counter()
    for path in files:
        try: parsed=m.parse_market(path,h,cfg)
        except Exception as exc: parse[str(exc) or exc.__class__.__name__]+=1; continue
        valid+=1
        for name,c in CONFIGS.items():
            if passes(m.snapshot_features(parsed,c['start']['cutoff']),c['start']) and passes(m.snapshot_features(parsed,c['end']['cutoff']),c['end']): counts[name]+=1
    report={
      'schema_version':'BETFAIR-DRAW-TRAJECTORY-REFINED-JOINT-COVERAGE-DIAGNOSTIC-R3C',
      'status':'COMPLETE_NO_LABEL_REFINED_JOINT_COVERAGE_DIAGNOSTIC',
      'source_commit':m.SOURCE_COMMIT,'candidate_files':len(files),'valid_identity_markets':valid,
      'parse_or_identity_failure_reasons':dict(sorted(parse.items())),'configurations':CONFIGS,'joint_eligible_counts':dict(sorted(counts.items())),
      'winner_labels_read':0,'post_kickoff_messages_parsed':0,'raw_names_prices_or_stream_messages_persisted':False,'per_market_rows_persisted':False,
      'model_fits':0,'thresholds_selected':0,'formal_weight':0,'formal_model_changes':0,'formal_data_changes':0,'formal_config_changes':0,'CURRENT_changes':0}
    dump(output,report); print(json.dumps(report,ensure_ascii=False,sort_keys=True))

def main()->int:
    p=argparse.ArgumentParser(); p.add_argument('--source-checkout',type=Path,required=True); p.add_argument('--output',type=Path,required=True); a=p.parse_args(); run(a.source_checkout,a.output); return 0
if __name__=='__main__': raise SystemExit(main())
