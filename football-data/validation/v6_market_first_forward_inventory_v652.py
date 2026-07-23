#!/usr/bin/env python3
"""Read-only inventory for the immutable V6.5.1 market-first forward ledger.

Reports frozen prediction coverage before outcomes arrive: counts by competition, provider, pick,
selection arm, confidence bucket, frozen lead-time bucket, kickoff horizon and settlement readiness.
It also audits the PRE-OUTCOME directional structure so a temporary home-heavy sample is not confused
with an executable home-only rule. No result/outcome field is used for this structure audit.
"""
from __future__ import annotations
import json,statistics
from collections import Counter
from datetime import datetime,timedelta,timezone
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1];LEDGER=ROOT/'forward'/'v6_market_first_events_v651.json';OUT=ROOT/'manifests'/'v6_market_first_forward_inventory_v652_status.json';THRESHOLD=0.35
def dt(v:Any)->datetime:
 x=datetime.fromisoformat(str(v or '').replace('Z','+00:00'))
 if x.tzinfo is None:raise ValueError('timezone missing')
 return x.astimezone(timezone.utc)
def lead_bucket(hours:float)->str:
 if hours<1:return 'LT_1H_INVALID'
 if hours<=12:return 'H1_12'
 if hours<=24:return 'H12_24'
 if hours<=48:return 'H24_48'
 if hours<=72:return 'H48_72'
 return 'GT_72_INVALID'
def confidence_bucket(v:float)->str:
 if v<0.10:return 'LT_0.10'
 if v<0.20:return '0.10_0.20'
 if v<0.30:return '0.20_0.30'
 if v<0.40:return '0.30_0.40'
 return 'GE_0.40'
def summary(vals:list[float])->dict[str,Any]:
 return {'count':len(vals),'min':min(vals) if vals else None,'median':statistics.median(vals) if vals else None,'mean':sum(vals)/len(vals) if vals else None,'max':max(vals) if vals else None,'at_or_above_0_35':sum(1 for v in vals if v>=THRESHOLD)}
def main()->int:
 now=datetime.now(timezone.utc).replace(microsecond=0);x=json.loads(LEDGER.read_text(encoding='utf-8')) if LEDGER.exists() else {'schema_version':'V6.5.1-market-first-forward-ledger-r1','events':[]}
 if x.get('schema_version')!='V6.5.1-market-first-forward-ledger-r1':raise SystemExit('invalid market-first ledger schema')
 preds={};settled=set();by_comp=Counter();selected_by_comp=Counter();pick=Counter();selected_pick=Counter();providers=Counter();leads=Counter();conf=Counter();rows=[];semantic=[];confidence_by_pick={'home':[],'draw':[],'away':[]};draw_probs=[];top1_probs=[]
 for event in x.get('events') or []:
  if not isinstance(event,dict):continue
  mid=str(event.get('match_id') or '')
  if event.get('event_type')=='RESULT_SETTLED' and mid:settled.add(mid);continue
  if event.get('event_type')!='MARKET_PREDICTION_FROZEN' or not mid:continue
  if mid in preds:semantic.append({'match_id':mid,'error':'duplicate_prediction_event'});continue
  p=event.get('payload') or {};identity=p.get('fixture_identity') or {};source=p.get('market_source') or {};prediction=p.get('prediction') or {};probs=prediction.get('probabilities') or {}
  try:
   kickoff=dt(identity.get('kickoff_at'));observed=dt(source.get('source_observed_at_utc'));hours=(kickoff-observed).total_seconds()/3600.0
   probability={k:float(probs[k]) for k in ('home','draw','away')};total=sum(probability.values())
   if any(v<0 or v>1 for v in probability.values()) or abs(total-1.0)>1e-8:raise ValueError(f'probability_sum={total}')
  except Exception as exc:semantic.append({'match_id':mid,'error':f'time_or_probability:{type(exc).__name__}:{exc}'});continue
  cid=str(identity.get('competition_id') or '');choice=str(prediction.get('pick') or '');selected=bool(prediction.get('selected_arm_a'));confidence=float(prediction.get('confidence') or 0.0);provider=str(source.get('provider_group') or source.get('provider_name') or '')
  if not cid or choice not in {'home','draw','away'} or not (1<=hours<=72):semantic.append({'match_id':mid,'error':'identity_pick_or_lead_invalid','competition_id':cid,'pick':choice,'lead_hours':hours});continue
  ranked=sorted(probability.values(),reverse=True);recomputed_conf=ranked[0]-ranked[1];recomputed_pick=max(('home','draw','away'),key=lambda k:probability[k])
  if recomputed_pick!=choice or abs(recomputed_conf-confidence)>1e-10:semantic.append({'match_id':mid,'error':'stored_pick_or_confidence_mismatch','stored_pick':choice,'recomputed_pick':recomputed_pick,'stored_confidence':confidence,'recomputed_confidence':recomputed_conf});continue
  expected_selected=choice!='draw' and confidence>=THRESHOLD
  if selected!=expected_selected:semantic.append({'match_id':mid,'error':'stored_selection_flag_mismatch','stored_selected':selected,'expected_selected':expected_selected});continue
  preds[mid]=event;by_comp[cid]+=1;pick[choice]+=1;providers[provider]+=1;leads[lead_bucket(hours)]+=1;conf[confidence_bucket(confidence)]+=1;confidence_by_pick[choice].append(confidence);draw_probs.append(probability['draw']);top1_probs.append(probability[choice])
  if selected:selected_by_comp[cid]+=1;selected_pick[choice]+=1
  rows.append({'match_id':mid,'competition_id':cid,'kickoff_at':kickoff.isoformat(),'result_eligible_at':(kickoff+timedelta(hours=2)).isoformat(),'home_team':identity.get('home_team'),'away_team':identity.get('away_team'),'provider_group':provider,'source_observed_at_utc':observed.isoformat(),'lead_hours':hours,'probabilities':probability,'pick':choice,'top1_probability':probability[choice],'draw_probability':probability['draw'],'confidence':confidence,'selected_arm_a':selected,'settled':mid in settled})
 rows.sort(key=lambda r:(r['kickoff_at'],r['competition_id'],r['home_team'] or ''));open_rows=[r for r in rows if not r['settled']];selected_rows=[r for r in rows if r['selected_arm_a']];eligible_now=[r for r in open_rows if dt(r['result_eligible_at'])<=now]
 directional={'threshold':THRESHOLD,'draw_top1_count':int(pick.get('draw',0)),'confidence_by_pick':{k:summary(v) for k,v in confidence_by_pick.items()},'draw_probability':summary(draw_probs),'top1_probability':summary(top1_probs),'away_top1_above_threshold_count':sum(1 for r in rows if r['pick']=='away' and r['confidence']>=THRESHOLD),'home_top1_above_threshold_count':sum(1 for r in rows if r['pick']=='home' and r['confidence']>=THRESHOLD),'structural_interpretation':'PRE_OUTCOME_ONLY_NO_THRESHOLD_CHANGE'}
 payload={'schema_version':'V6.5.2-market-first-forward-inventory-r2','generated_at_utc':now.isoformat(),'status':'PASS' if not semantic else 'WARN_SEMANTIC_ERRORS','prediction_count':len(rows),'settled_count':sum(1 for r in rows if r['settled']),'open_prediction_count':len(open_rows),'selected_arm_a_frozen_count':len(selected_rows),'selected_arm_a_rate':len(selected_rows)/len(rows) if rows else None,'by_competition':dict(sorted(by_comp.items())),'selected_by_competition':dict(sorted(selected_by_comp.items())),'pick_counts':dict(sorted(pick.items())),'selected_pick_counts':dict(sorted(selected_pick.items())),'provider_counts':dict(sorted(providers.items())),'lead_time_buckets':dict(sorted(leads.items())),'confidence_buckets':dict(sorted(conf.items())),'directional_structure_audit':directional,'earliest_kickoff_at':rows[0]['kickoff_at'] if rows else None,'earliest_open_result_eligible_at':min((r['result_eligible_at'] for r in open_rows),default=None),'open_results_eligible_now':len(eligible_now),'next_20_predictions':rows[:20],'semantic_errors':semantic,'governance':{'read_only':True,'prediction_mutation':False,'settlement_mutation':False,'outcome_data_used_for_selection_inventory':False,'directional_structure_is_pre_outcome_only':True,'selected_arm_count_is_pre_outcome_frozen_flag_only':True,'threshold_changed':False,'promotion_authority':False,'formal_weight_change':False,'runtime_probability_change':False,'current_rule_change':False}}
 OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(payload,ensure_ascii=False,indent=2));return 0 if not semantic else 2
if __name__=='__main__':raise SystemExit(main())