#!/usr/bin/env python3
"""Settlement-only evaluator for V6.21.7 exact-total point predictions."""
from __future__ import annotations
import json
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PRED=ROOT/'forward'/'v6_team_total_mode_sum_predictions_v6217.json'
SOURCE=ROOT/'forward'/'v6_market_first_events_v651.json'
FREEZE=ROOT/'manifests'/'v6_team_total_mode_sum_forward_freeze_v6217.json'
OUT=ROOT/'manifests'/'v6_team_total_mode_sum_forward_v6217_status.json'
def main():
 if not PRED.exists() or not FREEZE.exists():raise SystemExit('V6.21.7 frozen prediction artifacts missing; evaluator cannot create them')
 preds=json.loads(PRED.read_text(encoding='utf-8')).get('predictions') or [];source=json.loads(SOURCE.read_text(encoding='utf-8'));results={}
 for e in source.get('events') or []:
  if isinstance(e,dict) and e.get('event_type')=='RESULT_SETTLED':results[str(e.get('match_id') or '')]=e
 settled=[]
 for r in preds:
  z=results.get(str(r.get('match_id') or ''))
  if not z or str((z.get('payload') or {}).get('prediction_event_hash') or '')!=str(r.get('prediction_event_hash') or ''):continue
  rs=(z.get('payload') or {}).get('result') or {};actual=min(7,int(rs['home_goals_90'])+int(rs['away_goals_90']));settled.append({'match_id':r['match_id'],'pick':r['total_pick'],'actual':actual,'hit':int(r['total_pick']==actual)})
 n=len(settled);hits=sum(x['hit'] for x in settled);freeze=json.loads(FREEZE.read_text(encoding='utf-8'));payload={'schema_version':'V6.21.7-team-total-mode-sum-forward-status-r2','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','freeze_at_utc':freeze['freeze_at_utc'],'prediction_count':len(preds),'new_prediction_count':0,'settled_count':n,'hits':hits,'accuracy':hits/n if n else None,'review_state':'PENDING_100_SETTLED' if n<100 else 'READY_FOR_100_REVIEW','settled':settled,'governance':{'settlement_only':True,'prediction_generation':False,'prediction_mutation':False,'no_backfill':True,'score_track_used':False,'tail_split':False,'formal_weight':0,'current_rule_change':False,'automatic_promotion':False,'point_ranking_not_full_total_distribution':True,'minimum_settled_review_sample':100}}
 OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({k:payload[k] for k in ('status','prediction_count','settled_count','accuracy','review_state')},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
