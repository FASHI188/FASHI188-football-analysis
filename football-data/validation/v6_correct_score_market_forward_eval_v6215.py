#!/usr/bin/env python3
"""Settlement-only evaluator for V6.21.5 Correct-Score market predictions.

Reads the immutable V6.21.5 prediction sidecar and V6.5.1 official settlement ledger.
It cannot create, modify or delete predictions. This prevents post-outcome backfill.
"""
from __future__ import annotations
import json
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PRED=ROOT/'forward'/'v6_correct_score_market_predictions_v6215.json'
SOURCE=ROOT/'forward'/'v6_market_first_events_v651.json'
FREEZE=ROOT/'manifests'/'v6_correct_score_market_forward_freeze_v6215.json'
OUT=ROOT/'manifests'/'v6_correct_score_market_forward_v6215_status.json'

def main():
 if not PRED.exists() or not FREEZE.exists():
  raise SystemExit('V6.21.5 frozen prediction artifacts missing; settlement evaluator cannot create them')
 preds=json.loads(PRED.read_text(encoding='utf-8')).get('predictions') or []
 source=json.loads(SOURCE.read_text(encoding='utf-8'));results={}
 for e in source.get('events') or []:
  if isinstance(e,dict) and e.get('event_type')=='RESULT_SETTLED':results[str(e.get('match_id') or '')]=e
 settled=[]
 for r in preds:
  rs=results.get(str(r.get('match_id') or ''))
  if not rs:continue
  if str((rs.get('payload') or {}).get('prediction_event_hash') or '')!=str(r.get('prediction_event_hash') or ''):continue
  z=(rs.get('payload') or {}).get('result') or {};actual=[int(z['home_goals_90']),int(z['away_goals_90'])]
  settled.append({'match_id':r['match_id'],'actual':actual,'top1':r['top1'],'top3':r['top3'],'top1_hit':int(actual==r['top1']),'top3_hit':int(actual in r['top3'])})
 n=len(settled);h1=sum(x['top1_hit'] for x in settled);h3=sum(x['top3_hit'] for x in settled);freeze=json.loads(FREEZE.read_text(encoding='utf-8'))
 payload={'schema_version':'V6.21.5-correct-score-market-forward-status-r2','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','freeze_at_utc':freeze['freeze_at_utc'],'prediction_count':len(preds),'new_prediction_count':0,'settled_count':n,'top1_hits':h1,'top1_accuracy':h1/n if n else None,'top3_hits':h3,'top3_accuracy':h3/n if n else None,'review_state':'PENDING_100_SETTLED' if n<100 else 'READY_FOR_100_REVIEW','settled':settled,'governance':{'settlement_only':True,'prediction_generation':False,'prediction_mutation':False,'no_backfill':True,'formal_weight':0,'current_rule_change':False,'automatic_promotion':False,'ranking_not_full_probability_distribution':True,'minimum_settled_review_sample':100}}
 OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({k:payload[k] for k in ('status','prediction_count','settled_count','top1_accuracy','top3_accuracy','review_state')},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
