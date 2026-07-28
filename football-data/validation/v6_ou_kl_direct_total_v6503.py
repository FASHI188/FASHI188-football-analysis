#!/usr/bin/env python3
"""V6.50.3-R2 prospective OU-KL direct-total challenger.

For half-goal OU lines only, use immutable V6.49.2 P(T) as prior and a synchronized two-way
OU price as one identifiable binary constraint. De-vig by normalized implied probabilities;
KL I-projection preserves prior relative shape inside UNDER/OVER groups and rescales group
masses. A single malformed market snapshot fails closed for that fixture only.

Single Kambi provider-group PIT evidence is research-only: formal_weight=0, no backfill,
no automatic promotion, and an independent-provider formal market-source gate remains unmet.
"""
from __future__ import annotations
import json, math, sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]; V=ROOT/'validation'; E=ROOT/'engine'
for p in (V,E):
    if str(p) not in sys.path: sys.path.insert(0,str(p))
import evaluate_direct_total_margin_matrix_v6477 as core
from platform_core import sha256_json
SOURCE=ROOT/'forward'/'v6_fresh_matrix_events_v6492.json'
FREEZE=ROOT/'manifests'/'v6_ou_kl_direct_total_v6503_freeze.json'; LEDGER=ROOT/'forward'/'v6_ou_kl_direct_total_events_v6503.json'; STATUS=ROOT/'manifests'/'v6_ou_kl_direct_total_v6503_status.json'
MIN_LEAD=timedelta(hours=1); MIN_AGE=timedelta(hours=2); MIN_SETTLED=100; EPS=1e-15
SF='V6.50.3-ou-kl-direct-total-freeze-r2'; SL='V6.50.3-ou-kl-direct-total-ledger-r2'; SE='V6.50.3-ou-kl-direct-total-event-r2'

def now()->datetime:return datetime.now(timezone.utc).replace(microsecond=0)
def dt(v:object)->datetime|None:
    try:
        x=datetime.fromisoformat(str(v or '').replace('Z','+00:00'));return x.astimezone(timezone.utc) if x.tzinfo else None
    except Exception:return None
def load(p:Path)->dict[str,Any]:
    x=json.loads(p.read_text(encoding='utf-8'))
    if not isinstance(x,dict):raise RuntimeError(f'not object:{p}')
    return x
def freeze():
    if FREEZE.exists():
        x=load(FREEZE)
        if x.get('schema_version')!=SF or x.get('status')!='FROZEN':raise RuntimeError('invalid/stale V6.50.3 freeze')
        return x
    ts=now();x={'schema_version':SF,'status':'FROZEN','freeze_timestamp_utc':ts.isoformat(),'formal_current_version':'V5.0.1','classification':'PROSPECTIVE_SINGLE_PROVIDER_OU_KL_TOTAL_CHALLENGE_FORMAL_WEIGHT_0','prior':'V6.49.2 frozen direct-total P(T=0..6,7+)','market_constraint':{'eligible_line':'half-goal only; 0.5 increments with no push','de_vig':'multiplicative normalize 1/over_odds and 1/under_odds','constraint':'one binary OU group-mass constraint on an existing P(T) prior','objective':'minimize D_KL(Q||P_prior)','solution':'preserve prior relative probabilities inside UNDER and OVER groups; scale each group to target mass','does_not_identify_full_distribution_from_ou_alone':True},'market_quality':{'required_snapshot_fields':['complete over_under line/over/under','synchronized one_x_two/asian_handicap/over_under timestamps','matching frozen raw_snapshot_sha256'],'accepted_for_research':'single provider-group fresh PIT synchronized 1X2/AH/OU','independent_provider_consensus_required_for_formal_market_coordination':True,'current_single_provider_can_auto_promote':False},'prospective_contract':{'fixture_future_at_event_timestamp':True,'minimum_lead_hours':1,'one_immutable_prediction_per_fixture':True,'historical_backfill':False,'settlement_reuses_matching_V6.49.2 official 90m settlement':True},'forward_gate':{'minimum_settled':MIN_SETTLED,'total_log_nonworse':True,'total_rps_nonworse':True,'total_top1_nonworse':True,'total_top2_nonworse':True,'formal_market_source_gate_separate':True},'governance':{'research_only':True,'single_provider_market_challenge':True,'source_probability_mutation':False,'formal_weight':0,'automatic_promotion':False,'current_rule_change':False}}
    FREEZE.parent.mkdir(parents=True,exist_ok=True);FREEZE.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');return x
def load_ledger():
    if not LEDGER.exists():return {'schema_version':SL,'events':[]}
    x=load(LEDGER)
    if x.get('schema_version')!=SL or not isinstance(x.get('events'),list):raise RuntimeError('invalid/stale V6.50.3 ledger')
    return x
def eh(x):return sha256_json(x)
def append(l,t,mid,ts,p):
    es=l['events'];e={'schema_version':SE,'sequence':len(es)+1,'event_type':t,'event_timestamp_utc':ts,'match_id':mid,'previous_event_hash':es[-1]['event_hash'] if es else 'GENESIS','payload':p};e['event_hash']=eh(e);es.append(e);return e
def audit(l):
    prev='GENESIS';err=[]
    for i,e in enumerate(l.get('events') or [],1):
        if e.get('sequence')!=i:err.append(f'sequence:{i}')
        if e.get('previous_event_hash')!=prev:err.append(f'previous:{i}')
        c=dict(e);r=c.pop('event_hash',None)
        if r!=eh(c):err.append(f'hash:{i}')
        prev=str(r or '')
    return {'status':'PASS' if not err else 'FAIL','event_count':len(l.get('events') or []),'tip_hash':prev,'errors':err}
def preds(x,t):return {str(e.get('match_id')):e for e in x.get('events') or [] if e.get('event_type')==t}
def settles(x):return {str(e.get('match_id')):e for e in x.get('events') or [] if e.get('event_type')=='RESULT_SETTLED'}
def half(line:float)->bool:
    twice=round(line*2);return math.isfinite(line) and abs(line*2-twice)<=1e-9 and int(twice)%2==1
def devig(over:float,under:float):
    if not math.isfinite(over) or not math.isfinite(under) or over<=1 or under<=1:raise ValueError('invalid odds')
    io,iu=1/over,1/under;z=io+iu;return io/z,iu/z
def project(prior,line,p_over,p_under):
    if not half(line) or line<0.5 or line>6.5:raise ValueError('unsupported line')
    fl=math.floor(line);uk=[str(i) for i in range(0,min(fl,6)+1)];ok=[k for k in core.REPORT_TOTAL_STATES if k not in uk];pu=sum(float(prior[k]) for k in uk);po=sum(float(prior[k]) for k in ok)
    if pu<=0 or po<=0:raise ValueError('infeasible prior support')
    q={k:float(prior[k])*(p_under/pu if k in uk else p_over/po) for k in core.REPORT_TOTAL_STATES};z=sum(q.values());qu=sum(q[k] for k in uk);qo=sum(q[k] for k in ok);kl=sum(v*math.log(v/max(EPS,float(prior[k]))) for k,v in q.items() if v>0)
    return q,{'under_keys':uk,'over_keys':ok,'prior_under':pu,'prior_over':po,'target_under':p_under,'target_over':p_over,'projected_under':qu,'projected_over':qo,'probability_sum':z,'max_constraint_residual':max(abs(qu-p_under),abs(qo-p_over),abs(z-1.0)),'objective_kl_q_to_prior':kl}
def parse_market_snapshot(mp:Path,source_market:dict[str,Any]):
    try:s=load(mp)
    except Exception:return None,'market_snapshot_unreadable'
    ou=s.get('over_under')
    if not isinstance(ou,dict):return None,'ou_surface_missing'
    if any(ou.get(k) is None for k in ('line','over','under')):return None,'ou_fields_missing'
    try:line=float(ou['line']);over=float(ou['over']);under=float(ou['under'])
    except Exception:return None,'ou_fields_non_numeric'
    if not all(math.isfinite(x) for x in (line,over,under)):return None,'ou_fields_non_finite'
    surface=s.get('surface_observed_at_utc') or {}; stamps=[surface.get('one_x_two'),surface.get('asian_handicap'),surface.get('over_under')]
    if any(not x for x in stamps) or len(set(str(x) for x in stamps))!=1:return None,'market_surfaces_not_synchronized'
    expected=str(source_market.get('raw_snapshot_sha256') or '');actual=str(s.get('raw_snapshot_sha256') or '')
    if not expected or expected!=actual:return None,'market_snapshot_hash_mismatch'
    if str(s.get('provider_group') or '')!=str(source_market.get('provider_group') or ''):return None,'provider_group_mismatch'
    return {'snapshot':s,'line':line,'over':over,'under':under,'surface_timestamp':str(stamps[0])},None
def freeze_new(l):
    stats=Counter();src=load(SOURCE);sp=preds(src,'MATRIX_PREDICTION_FROZEN');done=preds(l,'TOTAL_PREDICTION_FROZEN')
    for mid,pe in sorted(sp.items()):
        if mid in done:stats['already_frozen']+=1;continue
        p=pe.get('payload') or {};fi=p.get('fixture_identity') or {};ko=dt(fi.get('kickoff_at'))
        if ko is None:stats['bad_kickoff']+=1;continue
        market=p.get('market_source') or {};rel=str(market.get('path') or '');mp=ROOT/rel
        if not rel or not mp.exists():stats['market_snapshot_missing']+=1;continue
        parsed,reason=parse_market_snapshot(mp,market)
        if reason:stats[reason]+=1;continue
        line,over,under=parsed['line'],parsed['over'],parsed['under']
        if not half(line) or line<0.5 or line>6.5:stats['non_half_or_unsupported_line']+=1;continue
        try:po,pu=devig(over,under)
        except Exception:stats['invalid_ou_odds']+=1;continue
        try:prior={k:float(((p.get('candidate') or {}).get('total') or {})[k]) for k in core.REPORT_TOTAL_STATES};q,a=project(prior,line,po,pu)
        except Exception:stats['projection_rejected']+=1;continue
        ts=now();lead=(ko-ts).total_seconds()/3600
        if ko-ts<MIN_LEAD:stats['not_future_with_minimum_lead']+=1;continue
        append(l,'TOTAL_PREDICTION_FROZEN',mid,ts.isoformat(),{'fixture_identity':fi,'projection_freeze_at_utc':ts.isoformat(),'actual_lead_hours_at_freeze':lead,'source_matrix_prediction_event_hash':pe.get('event_hash'),'source_market_snapshot_path':rel,'source_market_snapshot_sha256':market.get('raw_snapshot_sha256'),'market_observed_at_utc':market.get('observed_at_utc'),'synchronized_surface_observed_at_utc':parsed['surface_timestamp'],'provider_name':market.get('provider_name'),'provider_group':market.get('provider_group'),'over_under_raw':{'line':line,'over':over,'under':under},'over_under_devig':{'over':po,'under':pu,'method':'multiplicative_normalized_implied_probability'},'source_prior_total':prior,'candidate_total':q,'source_mode':core.topk(prior,1)[0],'candidate_mode':core.topk(q,1)[0],'optimization':{'prior':'source_prior_total','constraint':'binary OU group mass','objective':'D_KL(Q||P_prior)','closed_form_group_scaling':True,**a},'governance':{'fixture_future_with_minimum_lead_at_actual_event_timestamp':True,'single_provider_market_challenge':True,'historical_backfill':False,'source_probability_mutation':False,'formal_weight':0}});stats['new_predictions']+=1
    return dict(sorted(stats.items()))
def settle(l):
    ts0=now();stats=Counter();src=load(SOURCE);sp=preds(src,'MATRIX_PREDICTION_FROZEN');ss=settles(src);ps=preds(l,'TOTAL_PREDICTION_FROZEN');done=settles(l)
    for mid,pe in sorted(ps.items()):
        if mid in done:continue
        fi=(pe.get('payload') or {}).get('fixture_identity') or {};ko=dt(fi.get('kickoff_at'))
        if ko is None or ts0<ko+MIN_AGE:stats['not_old_enough']+=1;continue
        se=ss.get(mid);spe=sp.get(mid)
        if not se or not spe:stats['source_settlement_missing']+=1;continue
        if (se.get('payload') or {}).get('prediction_event_hash')!=spe.get('event_hash'):stats['source_hash_mismatch']+=1;continue
        ts=now();append(l,'RESULT_SETTLED',mid,ts.isoformat(),{'prediction_event_hash':pe.get('event_hash'),'source_matrix_prediction_event_hash':spe.get('event_hash'),'source_matrix_settlement_event_hash':se.get('event_hash'),'result':(se.get('payload') or {}).get('result'),'official_result_receipt_sha256':(se.get('payload') or {}).get('official_result_receipt_sha256'),'official_result_source':(se.get('payload') or {}).get('official_result_source')});stats['new_settlements']+=1
    return dict(sorted(stats.items()))
def metric(rows):
    n=len(rows)
    if not n:return {'count':0}
    ll=rps=0.0;t1=t2=0
    for p,ac in rows:ll-=math.log(max(EPS,float(p[ac])));rps+=core.rps_total(p,ac);tk=core.topk(p,2);t1+=int(tk[0]==ac);t2+=int(ac in tk)
    return {'count':n,'total_log_loss':ll/n,'total_rps':rps/n,'total_top1_accuracy':t1/n,'total_top2_accuracy':t2/n}
def evaluate(l):
    ps=preds(l,'TOTAL_PREDICTION_FROZEN');ss=settles(l);cr=[];rr=[]
    for mid,se in ss.items():
        pe=ps.get(mid)
        if not pe or (se.get('payload') or {}).get('prediction_event_hash')!=pe.get('event_hash'):continue
        p=pe.get('payload') or {};r=(se.get('payload') or {}).get('result') or {};ac=core.total_cat(int(r['home_goals_90']),int(r['away_goals_90']));cr.append((p['candidate_total'],ac));rr.append((p['source_prior_total'],ac))
    c=metric(cr);r=metric(rr);n=int(c.get('count') or 0);g={'minimum_settled':n>=MIN_SETTLED,'total_log_nonworse':bool(n) and c['total_log_loss']<=r['total_log_loss']+1e-12,'total_rps_nonworse':bool(n) and c['total_rps']<=r['total_rps']+1e-12,'total_top1_nonworse':bool(n) and c['total_top1_accuracy']>=r['total_top1_accuracy']-1e-12,'total_top2_nonworse':bool(n) and c['total_top2_accuracy']>=r['total_top2_accuracy']-1e-12,'formal_market_source_gate':False}
    return {'candidate':c,'reference':r,'forward_gate':{'results':g,'all_statistical_gates_pass':all(v for k,v in g.items() if k!='formal_market_source_gate'),'formal_gate_pass':all(g.values())},'decision':'PENDING_OU_KL_FORWARD_SAMPLE_OR_QUALITY_AND_PROVIDER_DIVERSITY'}
def diagnostic(l):
    ps=list(preds(l,'TOTAL_PREDICTION_FROZEN').values())
    if not ps:return {'count':0}
    cm=Counter(str((e.get('payload') or {}).get('candidate_mode')) for e in ps);sm=Counter(str((e.get('payload') or {}).get('source_mode')) for e in ps);lines=Counter(str((e.get('payload') or {}).get('over_under_raw',{}).get('line')) for e in ps);changed=sum(int((e.get('payload') or {}).get('candidate_mode')!=(e.get('payload') or {}).get('source_mode')) for e in ps);res=[float((e.get('payload') or {}).get('optimization',{}).get('max_constraint_residual') or 0) for e in ps];leads=[float((e.get('payload') or {}).get('actual_lead_hours_at_freeze') or 0) for e in ps];kls=[float((e.get('payload') or {}).get('optimization',{}).get('objective_kl_q_to_prior') or 0) for e in ps]
    return {'count':len(ps),'source_mode_counts':dict(sorted(sm.items())),'candidate_mode_counts':dict(sorted(cm.items())),'mode_changed_count':changed,'mode_changed_rate':changed/len(ps),'ou_line_counts':dict(sorted(lines.items())),'max_constraint_residual':max(res),'mean_kl_objective':sum(kls)/len(kls),'max_kl_objective':max(kls),'minimum_actual_lead_hours_at_freeze':min(leads),'provider_groups':sorted(set(str((e.get('payload') or {}).get('provider_group')) for e in ps))}
def main():
    fr=freeze();l=load_ledger();a0=audit(l)
    if a0['status']!='PASS':raise RuntimeError('pre ledger audit failed')
    scan=freeze_new(l);st=settle(l);a1=audit(l)
    if a1['status']!='PASS':raise RuntimeError('post ledger audit failed')
    LEDGER.parent.mkdir(parents=True,exist_ok=True);LEDGER.write_text(json.dumps(l,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');dg=diagnostic(l);ev=evaluate(l);ts=now();x={'schema_version':'V6.50.3-ou-kl-direct-total-status-r2','generated_at_utc':ts.isoformat(),'formal_current_version':'V5.0.1','status':'PASS','freeze':fr,'prediction_scan':scan,'settlement_scan':st,'ledger_audit':a1,'prospective_diagnostics':dg,'evaluation':ev,'governance':{'research_only':True,'single_provider_market_challenge':True,'independent_provider_consensus':False,'source_probability_mutation':False,'historical_backfill':False,'formal_weight':0,'automatic_promotion':False,'current_rule_change':False}}
    STATUS.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'status':'PASS','prediction_scan':scan,'prospective_diagnostics':dg,'evaluation':ev},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
