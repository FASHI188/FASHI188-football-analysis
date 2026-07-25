#!/usr/bin/env python3
"""V6.22.6 no-backfill prospective Kambi 1X2 de-vig comparison.

Consumes immutable V6.5.1 MARKET_PREDICTION_FROZEN events. For a source fixture that is
still future when first seen by this epoch, freeze TWO deterministic transforms of the
same 1X2 odds:
  A multiplicative de-vig (current benchmark)
  B OO-EPC t=1 from Goto, Takeishi & Yairi 2026 Algorithm 5
No outcome tuning, no market refetch, no formal probability mutation. Result evaluation is
performed from the official V6.5.1 RESULT_SETTLED ledger only.
"""
from __future__ import annotations
import json,math,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];V=ROOT/'validation'
if str(V) not in sys.path:sys.path.insert(0,str(V))
from platform_core import atomic_write_json,parse_iso_datetime,sha256_json
from v6_oo_epc_devig_screen_v6225 import multiplicative,oo_epc

SOURCE=ROOT/'forward'/'v6_market_first_events_v651.json'
EPOCH=ROOT/'manifests'/'v6_oo_epc_market_forward_epoch_v6226.json'
PRED=ROOT/'forward'/'v6_oo_epc_market_predictions_v6226.json'
OUT=ROOT/'manifests'/'v6_oo_epc_market_forward_v6226_status.json'

def now():return datetime.now(timezone.utc).replace(microsecond=0)
def load(path,default=None):return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default

def ensure_epoch(t):
 if EPOCH.exists():return load(EPOCH)
 x={'schema_version':'V6.22.6-oo-epc-market-forward-epoch-r1','status':'FROZEN','epoch_timestamp_utc':t.isoformat(),'formal_current_version':'V5.0.1','rule':{'source':'V6.5.1 MARKET_PREDICTION_FROZEN immutable Kambi 1X2 odds','arms':{'multiplicative':'normalize inverse decimal odds','oo_epc':'Goto Takeishi Yairi 2026 Algorithm 5, t=1, multiplicative fallback if positivity fails'},'source_fixture_must_be_future_when_sidecar_first_created':True,'outcome_tuned_parameters':False,'postmatch_prediction_generation':False},'governance':{'research_only':True,'historical_backfill':False,'automatic_promotion':False,'formal_probability_change':False,'formal_weight_change':False,'runtime_probability_change':False,'current_rule_change':False}}
 atomic_write_json(EPOCH,x);return x

def source_events():
 x=load(SOURCE,{'events':[]});preds={};results={}
 for e in x.get('events') or []:
  if not isinstance(e,dict):continue
  mid=str(e.get('match_id') or '')
  if e.get('event_type')=='MARKET_PREDICTION_FROZEN':preds[mid]=e
  elif e.get('event_type')=='RESULT_SETTLED':results[mid]=e
 return preds,results

def metrics(rows,key):
 n=len(rows);hits=0;brier=ll=rps=0.0
 for r in rows:
  p=r[key];y=r['actual'];hits+=int(max(range(3),key=lambda i:p[i])==y);brier+=sum((p[i]-(1 if i==y else 0))**2 for i in range(3));ll+=-math.log(max(1e-15,p[y]));rps+=((p[0]-(1 if y==0 else 0))**2+((p[0]+p[1])-(1 if y<=1 else 0))**2)/2
 return {'count':n,'hits':hits,'accuracy':hits/n if n else None,'brier':brier/n if n else None,'logloss':ll/n if n else None,'rps':rps/n if n else None}

def main():
 t=now();epoch=ensure_epoch(t);preds,results=source_events();env=load(PRED,{'schema_version':'V6.22.6-oo-epc-market-predictions-r1','predictions':[]});existing={r['match_id'] for r in env['predictions']};new=unavailable=started=0
 for mid,e in sorted(preds.items(),key=lambda kv:int(kv[1].get('sequence') or 0)):
  if mid in existing:continue
  ident=(e.get('payload') or {}).get('fixture_identity') or {}
  try:kick=parse_iso_datetime(str(ident.get('kickoff_at') or ''),'kickoff')
  except Exception:unavailable+=1;continue
  if kick<=t:started+=1;continue
  surf=(e.get('payload') or {}).get('frozen_surfaces') or {};o=surf.get('one_x_two_odds') or {}
  try:od=[float(o['home']),float(o['draw']),float(o['away'])]
  except Exception:unavailable+=1;continue
  if any((not math.isfinite(x) or x<=1) for x in od):unavailable+=1;continue
  pm=multiplicative(od);pe,fb=oo_epc(od)
  row={'schema_version':'V6.22.6-oo-epc-market-prediction-r1','match_id':mid,'source_prediction_event_hash':e.get('event_hash'),'sidecar_created_at_utc':t.isoformat(),'fixture_identity':ident,'source_market':(e.get('payload') or {}).get('market_source'),'odds':{'home':od[0],'draw':od[1],'away':od[2]},'multiplicative':pm,'oo_epc':pe,'oo_epc_fallback':bool(fb)};row['prediction_sha256']=sha256_json({k:v for k,v in row.items() if k!='prediction_sha256'});env['predictions'].append(row);existing.add(mid);new+=1
 env['predictions'].sort(key=lambda r:(str((r.get('fixture_identity') or {}).get('kickoff_at')),r['match_id']));atomic_write_json(PRED,env)
 settled=[]
 for r in env['predictions']:
  z=results.get(r['match_id'])
  if not z:continue
  # V6.5.1 result must bind to the exact source prediction event.
  if str((z.get('payload') or {}).get('prediction_event_hash') or '')!=str(r.get('source_prediction_event_hash') or ''):continue
  rs=(z.get('payload') or {}).get('result') or {};hg=int(rs['home_goals_90']);ag=int(rs['away_goals_90']);actual=0 if hg>ag else 1 if hg==ag else 2;settled.append({'match_id':r['match_id'],'actual':actual,'multiplicative':r['multiplicative'],'oo_epc':r['oo_epc']})
 m=metrics(settled,'multiplicative');o=metrics(settled,'oo_epc');delta={k:(o[k]-m[k]) for k in ('accuracy','brier','logloss','rps') if o[k] is not None and m[k] is not None}
 payload={'schema_version':'V6.22.6-oo-epc-market-forward-status-r1','generated_at_utc':t.isoformat(),'status':'PASS','epoch_timestamp_utc':epoch['epoch_timestamp_utc'],'source_prediction_count':len(preds),'prediction_count':len(env['predictions']),'new_prediction_count':new,'already_started_not_backfilled':started,'unavailable':unavailable,'settled_count':len(settled),'arms':{'multiplicative':m,'oo_epc':o},'delta_oo_minus_multiplicative':delta,'review_state':'READY_FOR_100_REVIEW' if len(settled)>=100 else 'PENDING_100_SETTLED','governance':{'research_only':True,'no_started_event_backfill':True,'official_v651_settlement_only':True,'prediction_generation_postmatch':False,'automatic_promotion':False,'formal_probability_change':False,'formal_weight_change':False,'runtime_probability_change':False,'current_rule_change':False}}
 atomic_write_json(OUT,payload);print(json.dumps(payload,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
