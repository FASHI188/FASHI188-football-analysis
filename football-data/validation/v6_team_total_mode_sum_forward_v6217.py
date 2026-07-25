#!/usr/bin/env python3
"""V6.21.7 immutable exact-total point forward epoch from Team Total markets.

Piggybacks only on V6.5.1 MARKET_PREDICTION_FROZEN events. For each team, recover its
low exact-goal marginal probabilities from the same raw full-time Team Total half-lines.
A team mode is eligible only when the largest known exact bucket exceeds the entire
unresolved marginal tail. The match exact-total point prediction is home_mode+away_mode.
No score market, no tail split, no historical backfill and no threshold tuning.
"""
from __future__ import annotations
import json,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];V=ROOT/'validation'
if str(V) not in sys.path:sys.path.insert(0,str(V))
import v6_special_market_forward_eval_v6211 as sp
import v6_team_total_market_forward_eval_v6213 as tt
import v6_team_total_mode_sum_forward_eval_v6216 as ms
SOURCE=ROOT/'forward'/'v6_market_first_events_v651.json'
FREEZE=ROOT/'manifests'/'v6_team_total_mode_sum_forward_freeze_v6217.json'
PRED=ROOT/'forward'/'v6_team_total_mode_sum_predictions_v6217.json'
OUT=ROOT/'manifests'/'v6_team_total_mode_sum_forward_v6217_status.json'

def now():return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
def ensure_freeze():
 if FREEZE.exists():return json.loads(FREEZE.read_text(encoding='utf-8'))
 x={'schema_version':'V6.21.7-team-total-mode-sum-forward-freeze-r1','status':'PASS_FROZEN_NO_BACKFILL','freeze_at_utc':now(),'source_epoch':'V6.5.1 MARKET_PREDICTION_FROZEN','rule':{'home_goal_mode':'largest known exact Team Total marginal bucket only if it exceeds entire unresolved home tail','away_goal_mode':'same rule for away team','exact_total_point':'home_goal_mode + away_goal_mode capped to 7+','tail_split':False,'score_market_used':False,'threshold_tuning':False},'historical_reference_only':{'scored_settled_count':10,'hits':3,'accuracy':0.3},'governance':{'no_backfill':True,'formal_weight':0,'current_rule_change':False,'point_ranking_not_full_total_distribution':True}}
 FREEZE.parent.mkdir(parents=True,exist_ok=True);FREEZE.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');return x

def prediction_for(e):
 snap,env=sp.raw_for_prediction(e);tls=tt.team_ladders(env);src=(snap.get('source_adapter') or {}).get('source_display_names') or {};hm=tt.marginal(tls.get(str(src.get('home') or ''),{}) or {});am=tt.marginal(tls.get(str(src.get('away') or ''),{}) or {});h=ms.exact_mode(hm);a=ms.exact_mode(am)
 if h is None or a is None:return None
 return {'home_mode':h,'away_mode':a,'total_pick':min(7,h+a),'home_marginal':hm,'away_marginal':am}

def main():
 freeze=ensure_freeze();cut=freeze['freeze_at_utc'];ledger=json.loads(SOURCE.read_text(encoding='utf-8'));src_preds,results=sp.prediction_and_results(ledger);env=json.loads(PRED.read_text(encoding='utf-8')) if PRED.exists() else {'schema_version':'V6.21.7-team-total-mode-sum-predictions-r1','predictions':[]};existing={r['match_id'] for r in env['predictions']};new=unavailable=0
 for mid,e in sorted(src_preds.items(),key=lambda kv:int(kv[1].get('sequence') or 0)):
  if mid in existing or str(e.get('event_timestamp_utc') or '')<cut:continue
  try:q=prediction_for(e)
  except Exception:q=None
  if q is None:unavailable+=1;continue
  r={'schema_version':'V6.21.7-team-total-mode-sum-prediction-r1','match_id':mid,'prediction_event_hash':e.get('event_hash'),'frozen_at_utc':e.get('event_timestamp_utc'),'fixture_identity':e['payload']['fixture_identity'],'source_observed_at_utc':e['payload']['market_source']['source_observed_at_utc'],**q};env['predictions'].append(r);existing.add(mid);new+=1
 env['predictions'].sort(key=lambda r:(str(r.get('frozen_at_utc')),str(r.get('match_id'))));PRED.parent.mkdir(parents=True,exist_ok=True);PRED.write_text(json.dumps(env,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 settled=[]
 for r in env['predictions']:
  z=results.get(r['match_id'])
  if not z or str((z.get('payload') or {}).get('prediction_event_hash') or '')!=str(r['prediction_event_hash']):continue
  rs=(z.get('payload') or {}).get('result') or {};actual=min(7,int(rs['home_goals_90'])+int(rs['away_goals_90']));settled.append({'match_id':r['match_id'],'pick':r['total_pick'],'actual':actual,'hit':int(r['total_pick']==actual)})
 n=len(settled);hits=sum(x['hit'] for x in settled);payload={'schema_version':'V6.21.7-team-total-mode-sum-forward-status-r1','generated_at_utc':now(),'status':'PASS','freeze_at_utc':cut,'prediction_count':len(env['predictions']),'new_prediction_count':new,'unavailable_new_source_predictions':unavailable,'settled_count':n,'hits':hits,'accuracy':hits/n if n else None,'review_state':'PENDING_100_SETTLED','settled':settled,'governance':{'no_backfill':True,'prediction_mutation':False,'score_track_used':False,'tail_split':False,'formal_weight':0,'current_rule_change':False,'automatic_promotion':False,'point_ranking_not_full_total_distribution':True,'minimum_settled_review_sample':100}}
 OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({k:payload[k] for k in ('status','freeze_at_utc','prediction_count','new_prediction_count','settled_count','accuracy','review_state')},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
