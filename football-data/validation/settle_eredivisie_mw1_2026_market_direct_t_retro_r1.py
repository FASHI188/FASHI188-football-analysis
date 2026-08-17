#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
from pathlib import Path

if len(sys.argv) != 3:
    raise SystemExit('usage: settle.py FROZEN_PREDICTIONS.csv LABELS.json')

pred_path=Path(sys.argv[1]); labels_path=Path(sys.argv[2])
EXPECTED_PRED_SHA='11f3c4d07fcfd8953eb1c5002109a1061a90469093b9ebc77cea110ae1625b18'
if hashlib.sha256(pred_path.read_bytes()).hexdigest()!=EXPECTED_PRED_SHA:
    raise SystemExit('immutable prediction SHA mismatch')
labels=json.loads(labels_path.read_text(encoding='utf-8'))
if labels['status']!='LABELS_OPENED_ONLY_AFTER_IMMUTABLE_PREDICTION_FREEZE':
    raise SystemExit('label chronology status mismatch')
if labels['prediction_freeze_artifact_id']!=9278817453 or labels['frozen_predictions_sha256']!=EXPECTED_PRED_SHA:
    raise SystemExit('label-to-freeze binding mismatch')

pred=list(csv.DictReader(pred_path.open('r',encoding='utf-8',newline='')))
lab={int(r['fixture_no']):r for r in labels['rows']}
if len(pred)!=9 or len(lab)!=9:
    raise SystemExit('expected exactly nine rows')

K=8
EPS=1e-15

def probs(row,prefix):
    p=[float(row[f'{prefix}_pT{j}']) for j in range(K)]
    if abs(sum(p)-1.0)>1e-9: raise SystemExit(f'probability sum mismatch {row["fixture_no"]} {prefix}')
    return p

def row_metrics(p,y):
    ll=-math.log(max(EPS,p[y]))
    brier=sum((p[k]-(1.0 if k==y else 0.0))**2 for k in range(K))
    cp=0.0; cy=0.0; rs=0.0
    for k in range(K-1):
        cp += p[k]
        cy += 1.0 if y==k else 0.0
        rs += (cp-cy)**2
    rps=rs/(K-1)
    ranking=sorted(range(K),key=lambda k:(-p[k],k))
    return {'logloss':ll,'brier':brier,'rps':rps,'top1':int(ranking[0]==y),'top2':int(y in ranking[:2]),'ranking':ranking}

rows=[]
for r in sorted(pred,key=lambda x:int(x['fixture_no'])):
    no=int(r['fixture_no']); l=lab[no]
    if str(r['home']).strip()!=str(l['home']).strip() or str(r['away']).strip()!=str(l['away']).strip():
        raise SystemExit(f'identity mismatch fixture {no}')
    total=int(l['total_goals']); y=min(total,7)
    pb=probs(r,'baseline'); pc=probs(r,'candidate')
    mb=row_metrics(pb,y); mc=row_metrics(pc,y)
    rows.append({
      'fixture_no':no,'home':r['home'],'away':r['away'],'score':f"{l['home_goals']}-{l['away_goals']}",
      'actual_total':total,'actual_class':'7+' if y==7 else y,
      'baseline_top1':int(r['baseline_top1_T']),'candidate_top1':int(r['candidate_top1_T']),
      'baseline_top2':r['baseline_top2_T'],'candidate_top2':r['candidate_top2_T'],
      'baseline_p_ge4':float(r['baseline_pT_ge_4']),'candidate_p_ge4':float(r['candidate_pT_ge_4']),
      'baseline_logloss':mb['logloss'],'candidate_logloss':mc['logloss'],
      'delta_logloss':mc['logloss']-mb['logloss'],
      'baseline_top1_hit':bool(mb['top1']),'candidate_top1_hit':bool(mc['top1']),
      'baseline_top2_hit':bool(mb['top2']),'candidate_top2_hit':bool(mc['top2']),
    })

summary={}
for prefix in ('baseline','candidate'):
    vals=[]
    for r in sorted(pred,key=lambda x:int(x['fixture_no'])):
        no=int(r['fixture_no']); y=min(int(lab[no]['total_goals']),7); p=probs(r,prefix); vals.append(row_metrics(p,y))
    summary[prefix]={
      'logloss':sum(x['logloss'] for x in vals)/9,
      'brier':sum(x['brier'] for x in vals)/9,
      'rps':sum(x['rps'] for x in vals)/9,
      'top1_hits':sum(x['top1'] for x in vals),'top1_rate':sum(x['top1'] for x in vals)/9,
      'top2_hits':sum(x['top2'] for x in vals),'top2_rate':sum(x['top2'] for x in vals)/9,
    }
summary['delta_candidate_minus_baseline']={k:summary['candidate'][k]-summary['baseline'][k] for k in ('logloss','brier','rps','top1_rate','top2_rate')}
low=[r['delta_logloss'] for r in rows if r['actual_total']<=3]
high=[r['delta_logloss'] for r in rows if r['actual_total']>=4]
summary['descriptive_regime']={
  'actual_0_to_3':{'n':len(low),'mean_delta_logloss':sum(low)/len(low)},
  'actual_4_plus':{'n':len(high),'mean_delta_logloss':sum(high)/len(high)},
  'candidate_improved_all_4plus_rows':all(x<0 for x in high),
}
# Binary P(T>=4) logloss as an extra tail diagnostic.
for prefix in ('baseline','candidate'):
    losses=[]
    for r in sorted(pred,key=lambda x:int(x['fixture_no'])):
        no=int(r['fixture_no']); z=1 if int(lab[no]['total_goals'])>=4 else 0
        q=float(r[f'{prefix}_pT_ge_4']); losses.append(-(z*math.log(max(EPS,q))+(1-z)*math.log(max(EPS,1-q))))
    summary[prefix]['binary_ge4_logloss']=sum(losses)/9
summary['delta_candidate_minus_baseline']['binary_ge4_logloss']=summary['candidate']['binary_ge4_logloss']-summary['baseline']['binary_ge4_logloss']

out={
 'schema_version':'EREDIVISIE-MW1-2026-MARKET-DIRECT-T-RETRO-SETTLEMENT-R1',
 'status':'SETTLED_DESCRIPTIVE_NO_PROMOTION',
 'classification':'RETROSPECTIVE_PREMATCH_REFERENCE_EXPERIMENT_ONLY',
 'n':9,
 'prediction_freeze':{'run_id':32002283026,'artifact_id':9278817453,'artifact_digest':'sha256:cf34b188661144fd0bdac9ea8572886a0a46f458dd77c7a239a4e002a538fc51','frozen_predictions_sha256':EXPECTED_PRED_SHA},
 'timing_caveat':'Public archived odds are verified pre-match reference prices but not exact quote-timestamped T-60 snapshots for every fixture; strict_T60_PIT_claim=false.',
 'metrics':summary,
 'rows':rows,
 'interpretation':{
   'confirmatory_claim':False,'promotion_allowed':False,
   'key_descriptive_question':'Does the previously observed 4+ upper-tail correction pattern reappear on these nine out-of-sample-by-date matches?',
   'post_result_parameter_search_allowed':False
 },
 'governance':{'formal_weight':0,'CURRENT_mutation':False,'main_mutation':False,'formal_model_mutation':False}
}
out_path=Path('settlement_summary.json')
out_path.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(out,ensure_ascii=False,indent=2))
