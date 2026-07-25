#!/usr/bin/env python3
"""V6.21.6 exact-total point challenger from identifiable team-goal marginal modes.

For each frozen match, recover home and away exact-goal masses from each Team Total O/U
ladder. A team's exact goal mode is usable only when its largest known exact bucket exceeds
the entire unresolved marginal tail. The match exact-total point pick is the sum of those
two independently identifiable team modes. No score market, no tail split, no postmatch
odds and no manual adjustment.
"""
from __future__ import annotations
import json,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];V=ROOT/'validation'
if str(V) not in sys.path:sys.path.insert(0,str(V))
import v6_special_market_forward_eval_v6211 as sp
import v6_team_total_market_forward_eval_v6213 as tt
OUT=ROOT/'manifests'/'v6_team_total_mode_sum_forward_eval_v6216_status.json'
TOL=1e-9

def exact_mode(m):
 if not m:return None
 e=m['exact'];order=sorted(range(len(e)),key=lambda i:(-e[i],i));top=order[0]
 return top if e[top] > float(m['tail'])+TOL else None

def main():
 ledger=sp.load(sp.LEDGER);preds,results=sp.prediction_and_results(ledger);rows=[];available=0
 for mid,pred in sorted(preds.items()):
  try:snap,env=sp.raw_for_prediction(pred);tls=tt.team_ladders(env);src=(snap.get('source_adapter') or {}).get('source_display_names') or {};hm=tt.marginal(tls.get(str(src.get('home') or ''),{}) or {});am=tt.marginal(tls.get(str(src.get('away') or ''),{}) or {})
  except Exception:hm=am=None
  hmode,amode=exact_mode(hm),exact_mode(am);pick=min(7,hmode+amode) if hmode is not None and amode is not None else None
  if pick is not None:available+=1
  item={'match_id':mid,'home_mode':hmode,'away_mode':amode,'total_pick':pick,'settled':mid in results}
  if mid in results:
   r=(results[mid].get('payload') or {}).get('result') or {};actual=min(7,int(r['home_goals_90'])+int(r['away_goals_90']));item['actual_total']=actual
   if pick is not None:item['hit']=int(pick==actual)
  rows.append(item)
 settled=[r for r in rows if r['settled']];scored=[r for r in settled if r.get('hit') is not None];hits=sum(r['hit'] for r in scored)
 payload={'schema_version':'V6.21.6-team-total-mode-sum-forward-eval-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','classification':'DERIVED_ONLY_FROM_IMMUTABLE_PREMATCH_TEAM_TOTAL_MARKETS','summary':{'prediction_count':len(preds),'available_count':available,'settled_count':len(settled),'scored_settled_count':len(scored),'hits':hits,'accuracy':hits/len(scored) if scored else None},'rows':rows,'governance':{'score_track_used':False,'correct_score_market_used':False,'tail_split':False,'team_mode_must_dominate_entire_tail':True,'network_refetch':False,'formal_weight':0,'current_rule_change':False}}
 OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(payload['summary'],ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
