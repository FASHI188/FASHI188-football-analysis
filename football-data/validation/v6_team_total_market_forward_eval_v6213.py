#!/usr/bin/env python3
"""V6.21.3 independent total-goals track from home/away Team Total markets.

Uses only immutable raw Kambi snapshots bound to MARKET_PREDICTION_FROZEN events. Each
team's full-time half-goal O/U ladder identifies low exact goal probabilities. Their
convolution identifies low exact total probabilities without using the score track.
An exact total Top-1 is emitted only if the largest identified exact bucket exceeds the
entire unresolved tail mass. No tail split or historical backfill is permitted.
"""
from __future__ import annotations
import json,math
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];V=ROOT/'validation'
if str(V) not in sys.path:sys.path.insert(0,str(V))
import v6_special_market_forward_eval_v6211 as sp

OUT=ROOT/'manifests'/'v6_team_total_market_forward_eval_v6213_status.json'
TOL=1e-9

def team_ladders(env):
 out=defaultdict(dict)
 for off in (env.get('payload') or {}).get('betOffers') or []:
  if not isinstance(off,dict) or sp.lifetime(off)!='FULL_TIME':continue
  lab=sp.label(off);low=lab.casefold()
  if not low.startswith('total goals by '):continue
  team=lab[len('Total Goals by '):].strip();os=sp.outcomes(off);ov=next((o for o in os if o.get('type')=='OT_OVER'),None);un=next((o for o in os if o.get('type')=='OT_UNDER'),None)
  if not isinstance(ov,dict) or not isinstance(un,dict) or str(ov.get('status'))!='OPEN' or str(un.get('status'))!='OPEN':continue
  oo,uo=sp.dec_odds(ov.get('odds')),sp.dec_odds(un.get('odds'))
  if not oo or not uo:continue
  rawline=ov.get('line') if ov.get('line') is not None else un.get('line')
  try:line=float(rawline)/1000.0
  except:continue
  if abs((line-math.floor(line))-0.5)>TOL:continue
  io,iu=1/oo,1/uo;z=io+iu;cand={'line':line,'cdf_under':iu/z,'overround':z-1,'over_odds':oo,'under_odds':uo}
  if line not in out[team] or cand['overround']<out[team][line]['overround']:out[team][line]=cand
 return dict(out)

def marginal(ladder):
 c=[];k=0
 while (k+0.5) in ladder:c.append(float(ladder[k+0.5]['cdf_under']));k+=1
 if not c:return None
 if any(c[i]>c[i+1]+1e-6 for i in range(len(c)-1)):return None
 exact=[c[0]]+[c[i]-c[i-1] for i in range(1,len(c))]
 if any(x < -1e-6 for x in exact):return None
 return {'exact':[max(0.0,x) for x in exact],'tail':max(0.0,1-c[-1]),'max_exact_goal':len(exact)-1,'lines':[i+0.5 for i in range(len(c))]}

def identify_total(home,away):
 h=home['exact'];a=away['exact'];max_t=min(len(h)-1,len(a)-1);known=[]
 for t in range(max_t+1):known.append(sum(h[i]*a[t-i] for i in range(t+1)))
 tail=max(0.0,1-sum(known));order=sorted(range(len(known)),key=lambda i:(-known[i],i));top=order[0]
 return {'known_exact_probabilities':{str(i):known[i] for i in range(len(known))},'unresolved_tail_starts_at':len(known),'unresolved_tail_mass':tail,'top1_identifiable':known[top]>tail+TOL,'top1_bucket':top if known[top]>tail+TOL else None,'top1_probability':known[top] if known[top]>tail+TOL else None,'home_lines':home['lines'],'away_lines':away['lines']}

def main():
 ledger=sp.load(sp.LEDGER);preds,results=sp.prediction_and_results(ledger);rows=[];errors=[];availability=Counter()
 for mid,pred in sorted(preds.items()):
  try:snap,env=sp.raw_for_prediction(pred);tls=team_ladders(env);src=(snap.get('source_adapter') or {}).get('source_display_names') or {};home_src=str(src.get('home') or '');away_src=str(src.get('away') or '')
   # Prefer exact provider display names; fail closed if not present.
  except Exception as e:errors.append({'match_id':mid,'error':str(e)});continue
  hm=marginal(tls.get(home_src,{}) or {});am=marginal(tls.get(away_src,{}) or {});ident=identify_total(hm,am) if hm and am else None
  if hm and am:availability['both_team_marginals']+=1
  if ident and ident['top1_identifiable']:availability['exact_total_top1_identifiable']+=1
  item={'match_id':mid,'home_team':pred['payload']['fixture_identity']['home_team'],'away_team':pred['payload']['fixture_identity']['away_team'],'source_home':home_src,'source_away':away_src,'home_marginal':hm,'away_marginal':am,'identification':ident,'settled':mid in results}
  if mid in results:
   rs=(results[mid].get('payload') or {}).get('result') or {};actual=min(7,int(rs['home_goals_90'])+int(rs['away_goals_90']));item['actual_total_bucket']=actual
   if ident and ident['top1_identifiable']:item['top1_hit']=int(ident['top1_bucket']==actual)
  rows.append(item)
 settled=[r for r in rows if r['settled']];scored=[r for r in settled if r.get('top1_hit') is not None]
 summary={'prediction_count':len(preds),'settled_count':len(settled),'availability':dict(availability),'scored_settled_count':len(scored),'hits':sum(r['top1_hit'] for r in scored),'accuracy':sum(r['top1_hit'] for r in scored)/len(scored) if scored else None}
 payload={'schema_version':'V6.21.3-team-total-market-forward-eval-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','classification':'DERIVED_ONLY_FROM_IMMUTABLE_PREMATCH_TEAM_TOTAL_MARKETS','summary':summary,'rows':rows,'errors':errors,'governance':{'network_refetch':False,'score_track_used':False,'correct_score_market_used':False,'tail_split':False,'formal_weight':0,'current_rule_change':False}}
 OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(summary,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
