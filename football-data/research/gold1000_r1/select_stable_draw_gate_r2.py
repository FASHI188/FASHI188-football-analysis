#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,hashlib,json
from pathlib import Path

def hf(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def f(v):return float(v)
def main()->int:
 ap=argparse.ArgumentParser();ap.add_argument('--candidate-grid',type=Path,required=True);ap.add_argument('--prereg',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True)
 prereg=json.loads(a.prereg.read_text(encoding='utf-8'))
 with a.candidate_grid.open(encoding='utf-8-sig',newline='') as fh:rows=list(csv.DictReader(fh))
 passed=[]
 for r in rows:
  ok=True
  for p in ('policy','diagnostic_test','confirm'):
   ok &= f(r[f'{p}_coverage'])<=.15+1e-12
   ok &= f(r[f'{p}_precision'])>=.40
   ok &= f(r[f'{p}_accuracy_delta'])>=-.01
  cov=[f(r[f'{p}_coverage']) for p in ('policy','diagnostic_test','confirm')]
  ok &= max(cov)-min(cov)<=.05+1e-12
  if not ok:continue
  item=dict(r)
  item['minimum_f1']=min(f(r[f'{p}_f1']) for p in ('policy','diagnostic_test','confirm'))
  item['minimum_precision']=min(f(r[f'{p}_precision']) for p in ('policy','diagnostic_test','confirm'))
  item['mean_f1']=sum(f(r[f'{p}_f1']) for p in ('policy','diagnostic_test','confirm'))/3
  item['mean_coverage']=sum(cov)/3
  item['coverage_drift']=max(cov)-min(cov)
  passed.append(item)
 if not passed:raise ValueError('no cross-window stable candidate')
 passed.sort(key=lambda r:(f(r['minimum_f1']),f(r['minimum_precision']),f(r['mean_f1']),-f(r['mean_coverage'])),reverse=True)
 winner=passed[0];exp=prereg['expected_unique_selection_from_frozen_development_evidence']
 if winner['model']!=exp['model'] or abs(f(winner['target_coverage'])-float(exp['target_coverage']))>1e-12:raise ValueError(f'unexpected winner {winner["model"]} {winner["target_coverage"]}')
 if len(passed)!=1:raise ValueError(f'expected unique stable candidate, found {len(passed)}')
 out={'schema_version':'SELECTIVE-DRAW-STABLE-SELECTION-R2','status':'FROZEN_BEFORE_CONFIRMATION_B_LABEL_READ','selection_uses_confirmation_b':False,'candidate_grid_sha256':hf(a.candidate_grid),'prereg_sha256':hf(a.prereg),'eligible_count':len(passed),'winner':winner}
 op=a.out/'SELECTIVE_DRAW_STABLE_R2_selection.json';op.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(json.dumps({'status':'PASS','model':winner['model'],'target_coverage':f(winner['target_coverage']),'threshold':f(winner['policy_threshold'])},ensure_ascii=False));return 0
if __name__=='__main__':raise SystemExit(main())
