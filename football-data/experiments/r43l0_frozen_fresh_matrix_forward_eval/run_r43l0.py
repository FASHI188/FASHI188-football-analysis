#!/usr/bin/env python3
from __future__ import annotations
import json, math, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE=Path(__file__).resolve().parent; ROOT=HERE.parents[1]; ENGINE=ROOT/'engine'
if str(ENGINE) not in sys.path: sys.path.insert(0,str(ENGINE))
from platform_core import normalize_team_token

TRUTH=ROOT/'forward'/'v6_market_first_events_v651.json'; FRESH=ROOT/'forward'/'v6_fresh_matrix_events_v6492.json'; OUT=HERE/'results'/'summary_r43l0_frozen_fresh_matrix_forward_eval.json'; C=('home','draw','away')
def load(p): return json.loads(p.read_text(encoding='utf-8'))
def dt(x):
 d=datetime.fromisoformat(str(x).replace('Z','+00:00')); return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
def key(f): return (str(f['competition_id']),dt(f['kickoff_at']).astimezone(timezone.utc).replace(microsecond=0).isoformat(),normalize_team_token(str(f['home_team'])),normalize_team_token(str(f['away_team'])))
def devig(o):
 z={k:1/float(o[k]) for k in C}; s=sum(z.values()); return {k:z[k]/s for k in C}
def norm(p):
 z={k:float(p[k]) for k in C}; s=sum(z.values()); return {k:z[k]/s for k in C}
def pick(p): return max(C,key=lambda k:(p[k],-C.index(k)))
def metrics(rows,field):
 eps=1e-15; hits=0; ll=br=rps=0.0; picks=Counter(); hitc=Counter(); actual=Counter(); dtruth=[]
 for r in rows:
  p=r[field]; y=r['truth']; idx=C.index(y); pk=pick(p); hits+=pk==y; picks[pk]+=1; hitc[pk]+=pk==y; actual[y]+=1
  ll+=-math.log(max(eps,min(1-eps,p[y]))); br+=sum((p[k]-(1 if k==y else 0))**2 for k in C)
  o1=1.0 if idx<=0 else 0.0; o2=1.0 if idx<=1 else 0.0; c1=p['home']; c2=c1+p['draw']; rps+=((c1-o1)**2+(c2-o2)**2)/2
  if y=='draw': dtruth.append(p['draw'])
 n=len(rows); return {'count':n,'hits':int(hits),'top1_accuracy':hits/n if n else None,'logloss':ll/n if n else None,'brier':br/n if n else None,'rps':rps/n if n else None,'top1_picks':dict(picks),'top1_hits':dict(hitc),'actuals':dict(actual),'mean_draw_probability_on_actual_draws':sum(dtruth)/len(dtruth) if dtruth else None}
def run():
 t=load(TRUTH); pk={}; settles={}
 for e in t['events']:
  if e.get('event_type')=='MARKET_PREDICTION_FROZEN': pk[str(e['match_id'])]=key(e['payload']['fixture_identity'])
  elif e.get('event_type')=='RESULT_SETTLED': settles[str(e['match_id'])]=str(e['payload']['result']['actual_result'])
 truth_by_key={pk[mid]:y for mid,y in settles.items() if mid in pk}
 x=load(FRESH); rows=[]; rej=Counter(); transitions=Counter()
 for e in x['events']:
  if e.get('event_type')!='MATRIX_PREDICTION_FROZEN': continue
  try:
   f=e['payload']['fixture_identity']; k=key(f); y=truth_by_key.get(k)
   if y is None: rej['no_settled_overlap']+=1; continue
   freeze=dt(e['payload']['decision_freeze_at_utc']); kick=dt(f['kickoff_at'])
   if freeze>=kick: rej['not_prematch']+=1; continue
   cand=norm(e['payload']['candidate']['result']); mp=ROOT/str(e['payload']['market_source']['path'])
   if not mp.exists(): rej['market_source_missing']+=1; continue
   mx=load(mp); market=devig(mx['one_x_two']); cp,mpk=pick(cand),pick(market); transitions[f'{mpk}->{cp}']+=int(mpk!=cp)
   rows.append({'truth':y,'market':market,'candidate':cand})
  except Exception: rej['parse_error']+=1
 mm=metrics(rows,'market'); cm=metrics(rows,'candidate'); delta={'hits':cm['hits']-mm['hits'],'accuracy_pp':100*(cm['top1_accuracy']-mm['top1_accuracy']) if rows else None,'logloss':cm['logloss']-mm['logloss'] if rows else None,'brier':cm['brier']-mm['brier'] if rows else None,'rps':cm['rps']-mm['rps'] if rows else None,'draw_pick_delta':cm['top1_picks'].get('draw',0)-mm['top1_picks'].get('draw',0)}
 signal=bool(len(rows)>=15 and delta['hits']>=1 and delta['logloss']<0 and delta['brier']<0 and delta['rps']<0)
 out={'schema_version':'football3-r43l0-frozen-fresh-matrix-forward-eval-v1','status':'COMPLETE','formal_weight':0,'classification':'POST_OUTCOME_EVALUATION_OF_ALREADY_FROZEN_FORWARD_CHALLENGER','question':'Did the prospectively frozen V6.49.2 fresh score-matrix result probabilities beat their same-time Kambi market probabilities on naturally settled overlap?',
 'governance':{'fresh_matrix_recomputed':False,'market_recomputed_from_frozen_source_only':True,'existing_settlements_only':True,'parameter_search':False,'threshold_search':False,'model_training':False,'formal_promotion_allowed':False},'coverage':{'fresh_prediction_count':sum(1 for e in x['events'] if e.get('event_type')=='MATRIX_PREDICTION_FROZEN'),'settled_overlap':len(rows),'rejects':dict(sorted(rej.items()))},'metrics':{'same_time_market':mm,'frozen_fresh_matrix':cm},'fresh_minus_market':delta,'top1_transitions':dict(sorted(transitions.items())),'evaluation_gate':{'minimum_overlap':15,'minimum_hit_gain':1,'proper_scores_must_all_improve':True,'passed':signal,'action':'REOPEN_FRESH_MATRIX_STRUCTURE_FOR_PREREGISTERED_REPLICATION' if signal else 'FRESH_MATRIX_NOT_A_BREAKTHROUGH_ON_FORWARD_OVERLAP'}}
 OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(json.dumps(out,ensure_ascii=False,indent=2)); return out
def verify():
 x=load(OUT); assert x['status']=='COMPLETE' and x['formal_weight']==0; g=x['governance']; assert g['fresh_matrix_recomputed'] is False and g['parameter_search'] is False and g['model_training'] is False; print('R43L0 contract verified')
if __name__=='__main__':
 cmd=sys.argv[1] if len(sys.argv)>1 else 'run'
 if cmd=='run': run()
 elif cmd=='verify': verify()
 else: raise SystemExit(cmd)
