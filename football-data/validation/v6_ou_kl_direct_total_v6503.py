#!/usr/bin/env python3
"""V6.50.3 prospective OU-KL direct-total challenger.

For half-goal OU lines only, use the immutable V6.49.2 direct-total distribution as prior P(T)
and the synchronized two-way OU price as ONE identifiable binary constraint. De-vig by
multiplicative normalization of implied probabilities, then compute the KL-minimal Q(T):
within UNDER and OVER groups, preserve the prior relative shape and scale each group to the
market target mass. This does not infer a full total distribution from the OU line alone.

Single Kambi provider-group PIT evidence => research challenge only, formal_weight=0 and no
automatic promotion. New events require >=1h actual lead at computation time; no backfill.
"""
from __future__ import annotations
import json, math, sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]; VALIDATION=ROOT/'validation'; ENGINE=ROOT/'engine'
for p in (VALIDATION,ENGINE):
    if str(p) not in sys.path:sys.path.insert(0,str(p))
import evaluate_direct_total_margin_matrix_v6477 as core
from platform_core import sha256_json
SOURCE_MATRIX=ROOT/'forward'/'v6_fresh_matrix_events_v6492.json'
FREEZE=ROOT/'manifests'/'v6_ou_kl_direct_total_v6503_freeze.json'; LEDGER=ROOT/'forward'/'v6_ou_kl_direct_total_events_v6503.json'; STATUS=ROOT/'manifests'/'v6_ou_kl_direct_total_v6503_status.json'
MIN_LEAD=timedelta(hours=1); MIN_RESULT_AGE=timedelta(hours=2); MIN_SETTLED=100; TOL=1e-12; EPS=1e-15
SCHEMA_FREEZE='V6.50.3-ou-kl-direct-total-freeze-r1'; SCHEMA_LEDGER='V6.50.3-ou-kl-direct-total-ledger-r1'; SCHEMA_EVENT='V6.50.3-ou-kl-direct-total-event-r1'

def now_utc()->datetime:return datetime.now(timezone.utc).replace(microsecond=0)
def parse_dt(v:object)->datetime|None:
    try:
        x=datetime.fromisoformat(str(v or '').replace('Z','+00:00'));return x.astimezone(timezone.utc) if x.tzinfo else None
    except Exception:return None
def load(p:Path)->dict[str,Any]:
    x=json.loads(p.read_text(encoding='utf-8'))
    if not isinstance(x,dict):raise RuntimeError(f'not object:{p}')
    return x
def ensure_freeze()->dict[str,Any]:
    if FREEZE.exists():
        x=load(FREEZE)
        if x.get('schema_version')!=SCHEMA_FREEZE or x.get('status')!='FROZEN':raise RuntimeError('invalid V6.50.3 freeze')
        return x
    ts=now_utc();x={'schema_version':SCHEMA_FREEZE,'status':'FROZEN','freeze_timestamp_utc':ts.isoformat(),'formal_current_version':'V5.0.1','classification':'PROSPECTIVE_SINGLE_PROVIDER_OU_KL_TOTAL_CHALLENGE_FORMAL_WEIGHT_0','prior':'V6.49.2 frozen direct-total P(T=0..6,7+)','market_constraint':{'eligible_line':'half-goal only; 0.5 increments with no push','de_vig':'multiplicative normalize 1/over_odds and 1/under_odds','constraint':'Q(UNDER)+Q(OVER)=1 and each group mass equals de-vigged synchronized OU probability','objective':'minimize D_KL(Q||P_prior)','solution':'preserve prior relative probabilities inside UNDER and OVER groups; scale each group to target mass'},'market_quality':{'accepted_for_research':'single provider-group fresh PIT synchronized 1X2/AH/OU','independent_provider_consensus_required_for_formal_market_coordination':True,'current_single_provider_can_auto_promote':False},'prospective_contract':{'fixture_future_at_event_timestamp':True,'minimum_lead_hours':1,'one_immutable_prediction_per_fixture':True,'historical_backfill':False,'settlement_reuses_matching_V6.49.2 official 90m settlement':True},'forward_gate':{'minimum_settled':MIN_SETTLED,'total_log_nonworse':True,'total_rps_nonworse':True,'total_top1_nonworse':True,'total_top2_nonworse':True,'formal_market_source_gate_separate':True},'governance':{'research_only':True,'single_provider_market_challenge':True,'source_probability_mutation':False,'formal_weight':0,'automatic_promotion':False,'current_rule_change':False}}
    FREEZE.parent.mkdir(parents=True,exist_ok=True);FREEZE.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');return x
def load_ledger()->dict[str,Any]:
    if not LEDGER.exists():return {'schema_version':SCHEMA_LEDGER,'events':[]}
    x=load(LEDGER)
    if x.get('schema_version')!=SCHEMA_LEDGER or not isinstance(x.get('events'),list):raise RuntimeError('bad V6.50.3 ledger')
    return x
def event_hash(x):return sha256_json(x)
def append(ledger,typ,mid,ts,payload):
    es=ledger['events'];e={'schema_version':SCHEMA_EVENT,'sequence':len(es)+1,'event_type':typ,'event_timestamp_utc':ts,'match_id':mid,'previous_event_hash':es[-1]['event_hash'] if es else 'GENESIS','payload':payload};e['event_hash']=event_hash(e);es.append(e);return e
def audit(ledger):
    prev='GENESIS';errors=[]
    for i,e in enumerate(ledger.get('events') or [],1):
        if e.get('sequence')!=i:errors.append(f'sequence:{i}')
        if e.get('previous_event_hash')!=prev:errors.append(f'previous:{i}')
        c=dict(e);r=c.pop('event_hash',None)
        if r!=event_hash(c):errors.append(f'hash:{i}')
        prev=str(r or '')
    return {'status':'PASS' if not errors else 'FAIL','event_count':len(ledger.get('events') or []),'tip_hash':prev,'errors':errors}
def pred_events(x,typ):return {str(e.get('match_id')):e for e in x.get('events') or [] if e.get('event_type')==typ}
def settled_events(x):return {str(e.get('match_id')):e for e in x.get('events') or [] if e.get('event_type')=='RESULT_SETTLED'}
def is_half_line(line:float)->bool:
    twice=round(line*2);return abs(line*2-twice)<=1e-9 and int(twice)%2==1
def devig(over:float,under:float)->tuple[float,float]:
    if over<=1 or under<=1:raise RuntimeError('invalid OU odds')
    io,iu=1/over,1/under;z=io+iu;return io/z,iu/z
def project(prior:dict[str,float],line:float,p_over:float,p_under:float)->tuple[dict[str,float],dict[str,Any]]:
    if not is_half_line(line) or line<0.5 or line>6.5:raise RuntimeError('unsupported OU line')
    floor_line=math.floor(line); under_keys=[str(i) for i in range(0,min(floor_line,6)+1)]; over_keys=[k for k in core.REPORT_TOTAL_STATES if k not in under_keys]
    pu=sum(float(prior[k]) for k in under_keys);po=sum(float(prior[k]) for k in over_keys)
    if pu<=0 or po<=0:raise RuntimeError('infeasible prior OU support')
    q={k:float(prior[k])*(p_under/pu if k in under_keys else p_over/po) for k in core.REPORT_TOTAL_STATES};z=sum(q.values())
    kl=sum(v*math.log(v/max(EPS,float(prior[k]))) for k,v in q.items() if v>0);qu=sum(q[k] for k in under_keys);qo=sum(q[k] for k in over_keys)
    return q,{'under_keys':under_keys,'over_keys':over_keys,'prior_under':pu,'prior_over':po,'target_under':p_under,'target_over':p_over,'projected_under':qu,'projected_over':qo,'probability_sum':z,'max_constraint_residual':max(abs(qu-p_under),abs(qo-p_over),abs(z-1.0)),'objective_kl_q_to_prior':kl}
def freeze_new(ledger)->dict[str,int]:
    stats=Counter();src=load(SOURCE_MATRIX);preds=pred_events(src,'MATRIX_PREDICTION_FROZEN');existing=pred_events(ledger,'TOTAL_PREDICTION_FROZEN')
    for mid,pe in sorted(preds.items()):
        if mid in existing:stats['already_frozen']+=1;continue
        p=pe.get('payload') or {};fi=p.get('fixture_identity') or {};ko=parse_dt(fi.get('kickoff_at'))
        if ko is None:stats['bad_kickoff']+=1;continue
        market=p.get('market_source') or {};rel=str(market.get('path') or '');mp=ROOT/rel
        if not rel or not mp.exists():stats['market_snapshot_missing']+=1;continue
        snap=load(mp);ou=snap.get('over_under') or {};line=float(ou.get('line'));over=float(ou.get('over'));under=float(ou.get('under'))
        if not is_half_line(line) or line<0.5 or line>6.5:stats['non_half_or_unsupported_line']+=1;continue
        po,pu=devig(over,under);prior={k:float(((p.get('candidate') or {}).get('total') or {})[k]) for k in core.REPORT_TOTAL_STATES};q,a=project(prior,line,po,pu)
        event_now=now_utc();lead=(ko-event_now).total_seconds()/3600
        if ko-event_now<MIN_LEAD:stats['not_future_with_minimum_lead']+=1;continue
        append(ledger,'TOTAL_PREDICTION_FROZEN',mid,event_now.isoformat(),{'fixture_identity':fi,'projection_freeze_at_utc':event_now.isoformat(),'actual_lead_hours_at_freeze':lead,'source_matrix_prediction_event_hash':pe.get('event_hash'),'source_market_snapshot_path':rel,'source_market_snapshot_sha256':market.get('raw_snapshot_sha256'),'market_observed_at_utc':market.get('observed_at_utc'),'provider_name':market.get('provider_name'),'provider_group':market.get('provider_group'),'over_under_raw':{'line':line,'over':over,'under':under},'over_under_devig':{'over':po,'under':pu,'method':'multiplicative_normalized_implied_probability'},'source_prior_total':prior,'candidate_total':q,'source_mode':core.topk(prior,1)[0],'candidate_mode':core.topk(q,1)[0],'optimization':{'prior':'source_prior_total','constraint':'binary OU group mass','objective':'D_KL(Q||P_prior)','closed_form_group_scaling':True,**a},'governance':{'fixture_future_with_minimum_lead_at_actual_event_timestamp':True,'single_provider_market_challenge':True,'historical_backfill':False,'source_probability_mutation':False,'formal_weight':0}});stats['new_predictions']+=1
    return dict(sorted(stats.items()))
def settle(ledger)->dict[str,int]:
    now=now_utc();stats=Counter();src=load(SOURCE_MATRIX);srcp=pred_events(src,'MATRIX_PREDICTION_FROZEN');srcs=settled_events(src);preds=pred_events(ledger,'TOTAL_PREDICTION_FROZEN');done=settled_events(ledger)
    for mid,pe in sorted(preds.items()):
        if mid in done:continue
        fi=(pe.get('payload') or {}).get('fixture_identity') or {};ko=parse_dt(fi.get('kickoff_at'))
        if ko is None or now<ko+MIN_RESULT_AGE:stats['not_old_enough']+=1;continue
        se=srcs.get(mid);sp=srcp.get(mid)
        if not se or not sp:stats['source_settlement_missing']+=1;continue
        if (se.get('payload') or {}).get('prediction_event_hash')!=sp.get('event_hash'):stats['source_hash_mismatch']+=1;continue
        ts=now_utc();append(ledger,'RESULT_SETTLED',mid,ts.isoformat(),{'prediction_event_hash':pe.get('event_hash'),'source_matrix_prediction_event_hash':sp.get('event_hash'),'source_matrix_settlement_event_hash':se.get('event_hash'),'result':(se.get('payload') or {}).get('result'),'official_result_receipt_sha256':(se.get('payload') or {}).get('official_result_receipt_sha256'),'official_result_source':(se.get('payload') or {}).get('official_result_source')});stats['new_settlements']+=1
    return dict(sorted(stats.items()))
def metric(rows):
    n=len(rows)
    if not n:return {'count':0}
    ll=rps=0.0;t1=t2=0
    for p,ac in rows:ll-=math.log(max(EPS,float(p[ac])));rps+=core.rps_total(p,ac);tk=core.topk(p,2);t1+=int(tk[0]==ac);t2+=int(ac in tk)
    return {'count':n,'total_log_loss':ll/n,'total_rps':rps/n,'total_top1_accuracy':t1/n,'total_top2_accuracy':t2/n}
def evaluate(ledger):
    preds=pred_events(ledger,'TOTAL_PREDICTION_FROZEN');done=settled_events(ledger);cr=[];rr=[]
    for mid,se in done.items():
        pe=preds.get(mid)
        if not pe or (se.get('payload') or {}).get('prediction_event_hash')!=pe.get('event_hash'):continue
        p=pe.get('payload') or {};r=(se.get('payload') or {}).get('result') or {};ac=core.total_cat(int(r['home_goals_90']),int(r['away_goals_90']));cr.append((p['candidate_total'],ac));rr.append((p['source_prior_total'],ac))
    c=metric(cr);r=metric(rr);n=int(c.get('count') or 0);g={'minimum_settled':n>=MIN_SETTLED,'total_log_nonworse':bool(n) and c['total_log_loss']<=r['total_log_loss']+1e-12,'total_rps_nonworse':bool(n) and c['total_rps']<=r['total_rps']+1e-12,'total_top1_nonworse':bool(n) and c['total_top1_accuracy']>=r['total_top1_accuracy']-1e-12,'total_top2_nonworse':bool(n) and c['total_top2_accuracy']>=r['total_top2_accuracy']-1e-12,'formal_market_source_gate':False}
    return {'candidate':c,'reference':r,'forward_gate':{'results':g,'all_statistical_gates_pass':all(v for k,v in g.items() if k!='formal_market_source_gate'),'formal_gate_pass':all(g.values())},'decision':'PENDING_OU_KL_FORWARD_SAMPLE_OR_QUALITY_AND_PROVIDER_DIVERSITY'}
def diagnostics(ledger):
    preds=list(pred_events(ledger,'TOTAL_PREDICTION_FROZEN').values())
    if not preds:return {'count':0}
    cm=Counter(str((e.get('payload') or {}).get('candidate_mode')) for e in preds);sm=Counter(str((e.get('payload') or {}).get('source_mode')) for e in preds);lines=Counter(str((e.get('payload') or {}).get('over_under_raw',{}).get('line')) for e in preds);changed=sum(int((e.get('payload') or {}).get('candidate_mode')!=(e.get('payload') or {}).get('source_mode')) for e in preds);res=[float((e.get('payload') or {}).get('optimization',{}).get('max_constraint_residual') or 0) for e in preds];leads=[float((e.get('payload') or {}).get('actual_lead_hours_at_freeze') or 0) for e in preds];kls=[float((e.get('payload') or {}).get('optimization',{}).get('objective_kl_q_to_prior') or 0) for e in preds]
    return {'count':len(preds),'source_mode_counts':dict(sorted(sm.items())),'candidate_mode_counts':dict(sorted(cm.items())),'mode_changed_count':changed,'mode_changed_rate':changed/len(preds),'ou_line_counts':dict(sorted(lines.items())),'max_constraint_residual':max(res),'mean_kl_objective':sum(kls)/len(kls),'max_kl_objective':max(kls),'minimum_actual_lead_hours_at_freeze':min(leads),'provider_groups':sorted(set(str((e.get('payload') or {}).get('provider_group')) for e in preds))}
def main()->int:
    freeze=ensure_freeze();ledger=load_ledger();before=audit(ledger)
    if before['status']!='PASS':raise RuntimeError('pre ledger audit failed')
    scan=freeze_new(ledger);sett=settle(ledger);after=audit(ledger)
    if after['status']!='PASS':raise RuntimeError('post ledger audit failed')
    LEDGER.parent.mkdir(parents=True,exist_ok=True);LEDGER.write_text(json.dumps(ledger,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');diag=diagnostics(ledger);ev=evaluate(ledger);ts=now_utc();status={'schema_version':'V6.50.3-ou-kl-direct-total-status-r1','generated_at_utc':ts.isoformat(),'formal_current_version':'V5.0.1','status':'PASS','freeze':freeze,'prediction_scan':scan,'settlement_scan':sett,'ledger_audit':after,'prospective_diagnostics':diag,'evaluation':ev,'governance':{'research_only':True,'single_provider_market_challenge':True,'independent_provider_consensus':False,'source_probability_mutation':False,'historical_backfill':False,'formal_weight':0,'automatic_promotion':False,'current_rule_change':False}}
    STATUS.write_text(json.dumps(status,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'status':'PASS','prediction_scan':scan,'prospective_diagnostics':diag,'evaluation':ev},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
