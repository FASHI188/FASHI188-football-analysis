#!/usr/bin/env python3
"""V6.21.5 immutable Correct-Score market ranking forward epoch.

Piggybacks only on V6.5.1 MARKET_PREDICTION_FROZEN events so fixture identity, lead window,
raw hash and observation time are already immutable. The ranking rule is frozen BEFORE
future outcomes: among OPEN numeric full-time Correct Score outcomes, lowest decimal odds
is Top-1 and the three lowest are Top-3. No threshold tuning and no historical backfill.

This is a ranking challenger, not yet a complete exact-score probability distribution;
formal weight remains zero until prospective evidence and probability-exhaustivity issues
are resolved.
"""
from __future__ import annotations
import json,sys
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];V=ROOT/'validation'
if str(V) not in sys.path:sys.path.insert(0,str(V))
import v6_special_market_forward_eval_v6211 as sp

SOURCE=ROOT/'forward'/'v6_market_first_events_v651.json'
FREEZE=ROOT/'manifests'/'v6_correct_score_market_forward_freeze_v6215.json'
PRED=ROOT/'forward'/'v6_correct_score_market_predictions_v6215.json'
OUT=ROOT/'manifests'/'v6_correct_score_market_forward_v6215_status.json'

def now():return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
def load_or(path,default):return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default

def ensure_freeze():
 if FREEZE.exists():return json.loads(FREEZE.read_text(encoding='utf-8'))
 x={'schema_version':'V6.21.5-correct-score-market-forward-freeze-r1','status':'PASS_FROZEN_NO_BACKFILL','freeze_at_utc':now(),'source_epoch':'V6.5.1 MARKET_PREDICTION_FROZEN','rule':{'market':'full-time Correct Score from exact frozen raw Kambi response','eligible_outcome':'OPEN numeric score with decimal odds >1','top1':'lowest decimal odds numeric score','top3':'three lowest decimal odds numeric scores','threshold_tuning':False,'direction_filter':False},'historical_reference_only':{'settled_count':11,'top1_accuracy':0.36363636363636365,'top3_accuracy':0.45454545454545453},'governance':{'no_backfill':True,'formal_weight':0,'current_rule_change':False,'ranking_not_full_probability_distribution':True}}
 FREEZE.parent.mkdir(parents=True,exist_ok=True);FREEZE.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');return x

def main():
 freeze=ensure_freeze();cut=str(freeze['freeze_at_utc']);ledger=json.loads(SOURCE.read_text(encoding='utf-8'));src_preds,results=sp.prediction_and_results(ledger)
 envelope=load_or(PRED,{'schema_version':'V6.21.5-correct-score-market-predictions-r1','predictions':[]});existing={r['match_id'] for r in envelope['predictions']};new=0;unavailable=0
 for mid,e in sorted(src_preds.items(),key=lambda kv:int(kv[1].get('sequence') or 0)):
  if mid in existing or str(e.get('event_timestamp_utc') or '')<cut:continue
  try:_,env=sp.raw_for_prediction(e);cs=sp.correct_score_surface(env)
  except Exception:cs=None
  if not cs or len(cs.get('ranked_scores') or [])<3:unavailable+=1;continue
  rank=cs['ranked_scores'];r={'schema_version':'V6.21.5-correct-score-market-prediction-r1','match_id':mid,'prediction_event_hash':e.get('event_hash'),'frozen_at_utc':e.get('event_timestamp_utc'),'fixture_identity':e['payload']['fixture_identity'],'source_observed_at_utc':e['payload']['market_source']['source_observed_at_utc'],'source_raw_snapshot_sha256':e['payload']['market_source'].get('raw_snapshot_sha256'),'top1':rank[0],'top3':rank[:3],'numeric_market_outcome_count':cs.get('numeric_count'),'offer_price_complete':cs.get('offer_price_complete'),'probability_exhaustive':False}
  envelope['predictions'].append(r);existing.add(mid);new+=1
 envelope['predictions'].sort(key=lambda r:(str(r.get('frozen_at_utc')),str(r.get('match_id'))));PRED.parent.mkdir(parents=True,exist_ok=True);PRED.write_text(json.dumps(envelope,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 settled=[]
 for r in envelope['predictions']:
  rs=results.get(r['match_id'])
  if not rs:continue
  if str((rs.get('payload') or {}).get('prediction_event_hash') or '')!=str(r['prediction_event_hash']):continue
  z=(rs.get('payload') or {}).get('result') or {};actual=[int(z['home_goals_90']),int(z['away_goals_90'])];settled.append({'match_id':r['match_id'],'actual':actual,'top1':r['top1'],'top3':r['top3'],'top1_hit':int(actual==r['top1']),'top3_hit':int(actual in r['top3'])})
 n=len(settled);h1=sum(x['top1_hit'] for x in settled);h3=sum(x['top3_hit'] for x in settled)
 payload={'schema_version':'V6.21.5-correct-score-market-forward-status-r1','generated_at_utc':now(),'status':'PASS','freeze_at_utc':cut,'prediction_count':len(envelope['predictions']),'new_prediction_count':new,'unavailable_new_source_predictions':unavailable,'settled_count':n,'top1_hits':h1,'top1_accuracy':h1/n if n else None,'top3_hits':h3,'top3_accuracy':h3/n if n else None,'review_state':'PENDING_100_SETTLED','settled':settled,'governance':{'no_backfill':True,'prediction_mutation':False,'source_v651_prediction_mutation':False,'threshold_mutation':False,'formal_weight':0,'current_rule_change':False,'automatic_promotion':False,'ranking_not_full_probability_distribution':True,'minimum_settled_review_sample':100}}
 OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({k:payload[k] for k in ('status','freeze_at_utc','prediction_count','new_prediction_count','settled_count','top1_accuracy','top3_accuracy','review_state')},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
