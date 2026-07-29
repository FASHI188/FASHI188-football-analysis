#!/usr/bin/env python3
"""V6.21.1 evaluate targeted Kambi markets at the exact frozen prediction snapshots.

No refetch and no post-match odds. Each MARKET_PREDICTION_FROZEN event points to an
immutable normalized snapshot, which points to the parent raw Kambi event-detail response.
This evaluator extracts full-time Correct Score, Total Goals ladder and BTTS from that
SAME raw response. Only ledger RESULT_SETTLED events are scored.

Important identifiability rule: a complete 0-7+ total distribution still requires all
0.5..6.5 half-lines. But a PARTIAL consecutive CDF may legally identify exact Top-1 when
the largest known exact bucket is greater than the entire unresolved tail mass. In that
case no split of the tail can overtake the known bucket, so no missing probability is
fabricated.
"""
from __future__ import annotations
import json,math,re
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
LEDGER=ROOT/'forward'/'v6_market_first_events_v651.json'
OUT=ROOT/'manifests'/'v6_special_market_forward_eval_v6211_status.json'
SCORE_RE=re.compile(r'^\s*(\d+)\s*[-:]\s*(\d+)\s*$')
TOL=1e-9

def load(path):return json.loads(path.read_text(encoding='utf-8'))
def dec_odds(o):
 try:v=float(o)/1000.0;return v if v>1 else None
 except:return None
def label(offer):
 c=offer.get('criterion') or {};return str(c.get('englishLabel') or c.get('label') or '').strip()
def lifetime(offer):return str((offer.get('criterion') or {}).get('lifetime') or '').upper()
def outcomes(offer):return [o for o in offer.get('outcomes') or [] if isinstance(o,dict)]

def prediction_and_results(ledger):
 preds={};results={}
 for e in ledger.get('events') or []:
  if not isinstance(e,dict):continue
  mid=str(e.get('match_id') or '')
  if e.get('event_type')=='MARKET_PREDICTION_FROZEN':preds[mid]=e
  elif e.get('event_type')=='RESULT_SETTLED':results[mid]=e
 return preds,results

def raw_for_prediction(pred):
 ms=pred['payload']['market_source'];norm=ROOT/str(ms['evidence_path']);snap=load(norm);sa=snap.get('source_adapter') or {};raw=ROOT/str(sa['parent_raw_evidence_path']);env=load(raw)
 if str(env.get('observed_at_utc'))!=str(ms.get('source_observed_at_utc')):raise ValueError('raw observation timestamp mismatch')
 if str(env.get('payload_sha256'))!=str(sa.get('parent_raw_response_sha256')):raise ValueError('raw parent hash mismatch')
 return snap,env

def correct_score_surface(env):
 offers=[]
 for off in (env.get('payload') or {}).get('betOffers') or []:
  if not isinstance(off,dict):continue
  if label(off).casefold()!='correct score' or lifetime(off)!='FULL_TIME':continue
  parsed=[]
  for o in outcomes(off):
   nm=str(o.get('englishLabel') or o.get('label') or '').strip();m=SCORE_RE.match(nm);od=dec_odds(o.get('odds'))
   parsed.append({'name':nm,'score':(int(m.group(1)),int(m.group(2))) if m else None,'odds':od,'status':str(o.get('status') or ''),'changedDate':o.get('changedDate')})
  if parsed:offers.append((off,parsed))
 if not offers:return None
 off,rows=max(offers,key=lambda x:(sum(r['score'] is not None for r in x[1]),sum(r['status']=='OPEN' and r['odds'] for r in x[1]),len(x[1])))
 valid=[r for r in rows if r['status']=='OPEN' and r['odds']];numeric=[r for r in valid if r['score'] is not None];ranked=sorted(numeric,key=lambda r:(r['odds'],r['score']))
 offer_price_complete=bool(rows) and len(valid)==len(rows) and all(r['odds'] for r in rows)
 numeric_z=sum(1/r['odds'] for r in numeric);numeric_probs={r['score']:(1/r['odds'])/numeric_z for r in numeric} if numeric_z else {};total_weights=defaultdict(float)
 for sc,p in numeric_probs.items():total_weights[min(7,sc[0]+sc[1])]+=p
 total_rank=sorted(total_weights,key=lambda t:(-total_weights[t],t));json_numeric_probs={f'{sc[0]}-{sc[1]}':p for sc,p in numeric_probs.items()}
 return {'offer_id':off.get('id'),'row_count':len(rows),'valid_open_count':len(valid),'numeric_count':len(numeric),'offer_price_complete':offer_price_complete,'probability_exhaustive':False,'ranked_scores':[r['score'] for r in ranked],'numeric_probabilities_relative_only':json_numeric_probs,'numeric_total_top_relative_only':total_rank,'numeric_total_probs_relative_only':dict(total_weights),'all_changed_dates_present':all(bool(r['changedDate']) for r in rows)}

def total_ladder(env):
 byline={}
 for off in (env.get('payload') or {}).get('betOffers') or []:
  if not isinstance(off,dict):continue
  if label(off).casefold()!='total goals' or lifetime(off)!='FULL_TIME':continue
  os=outcomes(off);over=next((o for o in os if o.get('type')=='OT_OVER'),None);under=next((o for o in os if o.get('type')=='OT_UNDER'),None)
  if not isinstance(over,dict) or not isinstance(under,dict):continue
  if str(over.get('status'))!='OPEN' or str(under.get('status'))!='OPEN':continue
  oo,uo=dec_odds(over.get('odds')),dec_odds(under.get('odds'))
  if not oo or not uo:continue
  rawline=over.get('line') if over.get('line') is not None else under.get('line')
  try:ln=float(rawline)/1000.0
  except:continue
  if abs((ln-math.floor(ln))-0.5)>TOL:continue
  inv_o,inv_u=1/oo,1/uo;z=inv_o+inv_u;q_under=inv_u/z;overround=z-1
  cand={'line':ln,'cdf_under':q_under,'over_odds':oo,'under_odds':uo,'overround':overround,'offer_id':off.get('id'),'changedDate_over':over.get('changedDate'),'changedDate_under':under.get('changedDate')}
  if ln not in byline or overround<byline[ln]['overround']:byline[ln]=cand
 lines=sorted(byline);complete=all((k+0.5) in byline for k in range(7));p=None;max_mono=0.0
 if lines:
  cdfs=[byline[x]['cdf_under'] for x in lines];max_mono=max([0.0]+[cdfs[i]-cdfs[i+1] for i in range(len(cdfs)-1)])
 if complete and max_mono<=1e-6:
  c=[byline[k+0.5]['cdf_under'] for k in range(7)];p=[c[0]]+[c[k]-c[k-1] for k in range(1,7)]+[1-c[6]];p=[max(0.0,x) for x in p];z=sum(p);p=[x/z for x in p]
 return {'line_count':len(lines),'lines':lines,'byline':byline,'complete_0_7plus':complete and p is not None,'max_monotonicity_violation':max_mono,'probabilities':p}

def identifiable_total_top(tl):
 if not tl or float(tl.get('max_monotonicity_violation') or 0)>1e-6:return None
 # JSON object keys are strings after a sidecar is serialized. Normalize both
 # in-memory float keys and reloaded string keys to float so prospective sidecar
 # consumers do not silently lose the partial-CDF arm.
 raw_by=tl.get('byline') or {};by={}
 for key,val in raw_by.items():
  try:by[float(key)]=val
  except (TypeError,ValueError):continue
 c=[];k=0
 while (k+0.5) in by:
  c.append(float(by[k+0.5]['cdf_under']));k+=1
 if not c:return None
 exact=[c[0]]+[c[i]-c[i-1] for i in range(1,len(c))]
 if any(x < -1e-6 for x in exact):return None
 exact=[max(0.0,x) for x in exact];tail=max(0.0,1.0-c[-1]);order=sorted(range(len(exact)),key=lambda i:(-exact[i],i));top=order[0]
 top1=bool(exact[top] > tail + TOL)
 top2=bool(len(order)>=2 and exact[order[1]] > tail + TOL)
 return {'contiguous_half_lines':[i+0.5 for i in range(len(c))],'known_exact_probabilities':{str(i):exact[i] for i in range(len(exact))},'unresolved_tail_starts_at':len(c),'unresolved_tail_mass':tail,'top1_identifiable':top1,'top1_bucket':top if top1 else None,'top1_probability':exact[top] if top1 else None,'top2_identifiable':top2,'top2_buckets':order[:2] if top2 else None}

def btts(env):
 for off in (env.get('payload') or {}).get('betOffers') or []:
  if not isinstance(off,dict):continue
  if label(off).casefold()!='both teams to score' or lifetime(off)!='FULL_TIME':continue
  vals=[]
  for o in outcomes(off):
   od=dec_odds(o.get('odds'))
   if str(o.get('status'))=='OPEN' and od:vals.append((str(o.get('englishLabel') or o.get('label') or '').casefold(),od))
  if len(vals)==2:
   inv=[1/x[1] for x in vals];z=sum(inv);return {vals[i][0]:inv[i]/z for i in range(2)}
 return None

def main():
 ledger=load(LEDGER);preds,results=prediction_and_results(ledger);rows=[];availability=Counter();errors=[]
 for mid,pred in sorted(preds.items()):
  try:snap,env=raw_for_prediction(pred)
  except Exception as e:errors.append({'match_id':mid,'error':str(e)});continue
  cs=correct_score_surface(env);tl=total_ladder(env);idt=identifiable_total_top(tl);bt=btts(env)
  if cs:availability['correct_score']+=1
  if tl and tl['line_count']:availability['total_ladder']+=1
  if tl and tl['complete_0_7plus']:availability['complete_total_distribution']+=1
  if idt and idt['top1_identifiable']:availability['partial_cdf_exact_top1_identifiable']+=1
  if bt:availability['btts']+=1
  item={'match_id':mid,'competition_id':pred['payload']['fixture_identity']['competition_id'],'kickoff_at':pred['payload']['fixture_identity']['kickoff_at'],'home_team':pred['payload']['fixture_identity']['home_team'],'away_team':pred['payload']['fixture_identity']['away_team'],'prediction_event_hash':pred.get('event_hash'),'raw_observed_at_utc':env.get('observed_at_utc'),'correct_score':cs,'total_ladder':tl,'partial_cdf_identification':idt,'btts':bt,'settled':mid in results}
  if mid in results:
   rs=(results[mid].get('payload') or {}).get('result') or {};hg=int(rs['home_goals_90']);ag=int(rs['away_goals_90']);actual=(hg,ag);actual_total=min(7,hg+ag);item['actual']={'home':hg,'away':ag,'total_bucket':actual_total}
   if cs and cs['ranked_scores']:
    item['score_top1_hit']=int(cs['ranked_scores'][0]==actual);item['score_top3_hit']=int(actual in cs['ranked_scores'][:3])
   if idt and idt['top1_identifiable']:item['partial_cdf_total_top1_hit']=int(idt['top1_bucket']==actual_total)
   if tl and tl['probabilities']:
    probs=tl['probabilities'];order=sorted(range(8),key=lambda i:(-probs[i],i));item['ladder_total_top1_hit']=int(order[0]==actual_total);item['ladder_total_top2_hit']=int(actual_total in order[:2]);item['ladder_total_rps']=sum((sum(probs[:k+1])-(1.0 if actual_total<=k else 0.0))**2 for k in range(7))/7.0
  rows.append(item)
 settled=[r for r in rows if r['settled']];score=[r for r in settled if r.get('score_top1_hit') is not None];partial=[r for r in settled if r.get('partial_cdf_total_top1_hit') is not None];ladder=[r for r in settled if r.get('ladder_total_top1_hit') is not None]
 summary={'prediction_count':len(preds),'settled_count':len(settled),'availability':dict(availability),'score_rank_settled_count':len(score),'score_top1_hits':sum(r['score_top1_hit'] for r in score),'score_top1_accuracy':sum(r['score_top1_hit'] for r in score)/len(score) if score else None,'score_top3_hits':sum(r['score_top3_hit'] for r in score),'score_top3_accuracy':sum(r['score_top3_hit'] for r in score)/len(score) if score else None,'partial_cdf_identifiable_settled_count':len(partial),'partial_cdf_total_top1_hits':sum(r['partial_cdf_total_top1_hit'] for r in partial),'partial_cdf_total_top1_accuracy':sum(r['partial_cdf_total_top1_hit'] for r in partial)/len(partial) if partial else None,'complete_ladder_settled_count':len(ladder),'ladder_total_top1_accuracy':sum(r['ladder_total_top1_hit'] for r in ladder)/len(ladder) if ladder else None,'ladder_total_top2_accuracy':sum(r['ladder_total_top2_hit'] for r in ladder)/len(ladder) if ladder else None,'ladder_total_mean_rps':sum(r['ladder_total_rps'] for r in ladder)/len(ladder) if ladder else None}
 payload={'schema_version':'V6.21.1-special-market-forward-eval-r3','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','classification':'DERIVED_ONLY_FROM_IMMUTABLE_PREMATCH_RAW_AT_FROZEN_PREDICTION_TIME','summary':summary,'rows':rows,'errors':errors,'governance':{'network_refetch':False,'postmatch_odds_used':False,'exact_prediction_snapshot_binding':True,'raw_hash_and_observation_time_verified':True,'partial_cdf_top1_only_when_identifiable_without_tail_split':True,'formal_weight':0,'current_rule_change':False,'probability_promotion':False}}
 OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(summary,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
