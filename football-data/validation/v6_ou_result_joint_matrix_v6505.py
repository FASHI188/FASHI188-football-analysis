#!/usr/bin/env python3
"""V6.50.5 prospective coherent joint-matrix challenger.

Prior: immutable V6.49.2 F06 candidate score matrix.
Constraints:
  1) result marginal == immutable V6.49.2 F05 1X2 probabilities;
  2) total marginal == immutable V6.50.3 OU-KL direct-total candidate P(T).
Objective: minimize D_KL(Q||P_prior) on the prior support via IPF.

V6.50.0 (same result constraint but old F06 total) is retained as a coherent reference so the
forward test can isolate whether the OU-adjusted total marginal improves exact-score quality.
Single-provider Kambi market evidence keeps formal_weight=0 and formal market-source gate false.
No backfill; each new event requires >=1h actual lead after computation.
"""
from __future__ import annotations
import json, math, sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1];V=ROOT/'validation';E=ROOT/'engine'
for p in (V,E):
    if str(p) not in sys.path:sys.path.insert(0,str(p))
import v6_kl_joint_projection_v6500 as klbase
import v6_total_margin_forward_v6479 as matrix_forward
from platform_core import sha256_json
SEL=ROOT/'forward'/'v6_fresh_selector_events_v6492.json'; MAT=ROOT/'forward'/'v6_fresh_matrix_events_v6492.json'; OU=ROOT/'forward'/'v6_ou_kl_direct_total_events_v6503.json'; KLREF=ROOT/'forward'/'v6_kl_joint_projection_events_v6500.json'
FREEZE=ROOT/'manifests'/'v6_ou_result_joint_matrix_v6505_freeze.json';LEDGER=ROOT/'forward'/'v6_ou_result_joint_matrix_events_v6505.json';STATUS=ROOT/'manifests'/'v6_ou_result_joint_matrix_v6505_status.json'
D=('home','draw','away');T=('0','1','2','3','4','5','6','7+');MIN_LEAD=timedelta(hours=1);MIN_AGE=timedelta(hours=2);MIN_SETTLED=100;IPF_TOL=1e-12;IPF_MAX=10000;AUDIT_TOL=1e-9
SF='V6.50.5-ou-result-joint-freeze-r1';SL='V6.50.5-ou-result-joint-ledger-r1';SE='V6.50.5-ou-result-joint-event-r1'
def now()->datetime:return datetime.now(timezone.utc).replace(microsecond=0)
def dt(v:object)->datetime|None:
    try:
        x=datetime.fromisoformat(str(v or '').replace('Z','+00:00'));return x.astimezone(timezone.utc) if x.tzinfo else None
    except Exception:return None
def load(p:Path)->dict[str,Any]:
    x=json.loads(p.read_text(encoding='utf-8'))
    if not isinstance(x,dict):raise RuntimeError(str(p))
    return x
def freeze():
    if FREEZE.exists():
        x=load(FREEZE)
        if x.get('schema_version')!=SF or x.get('status')!='FROZEN':raise RuntimeError('bad V6.50.5 freeze')
        return x
    ts=now();x={'schema_version':SF,'status':'FROZEN','freeze_timestamp_utc':ts.isoformat(),'formal_current_version':'V5.0.1','classification':'PROSPECTIVE_OU_RESULT_KL_JOINT_MATRIX_CHALLENGE_FORMAL_WEIGHT_0','prior':'V6.49.2 F06 candidate score matrix','constraints':{'total_marginal':'V6.50.3 frozen OU-KL candidate P(T)','result_marginal':'V6.49.2 frozen F05 1X2','support':'same support as V6.49.2 prior','probability_sum':1.0},'objective':'minimize D_KL(Q||P_prior)','optimizer':{'algorithm':'IPF alternating total and result marginals','tolerance':IPF_TOL,'max_iterations':IPF_MAX},'coherent_reference':'V6.50.0 KL matrix with same F05 result marginal and original F06 total marginal','market_quality':{'source':'single Kambi provider-group PIT OU via V6.50.3','independent_provider_consensus':False,'formal_market_source_gate':False},'prospective_contract':{'all_source_predictions_preexist':True,'minimum_actual_lead_hours':1,'one_immutable_prediction_per_fixture':True,'historical_backfill':False,'settlement_reuses_V6.49.2 official 90m result':True},'forward_gate':{'minimum_settled':MIN_SETTLED,'total_proper_scores_nonworse_vs_raw_F06':True,'exact_score_log_nonworse_vs_V6.50.0':True,'exact_score_top1_nonworse_vs_V6.50.0':True,'all_projection_audits_converged':True,'formal_market_source_gate_separate':True},'governance':{'research_only':True,'source_probability_mutation':False,'source_matrix_mutation':False,'formal_weight':0,'automatic_promotion':False,'current_rule_change':False}}
    FREEZE.parent.mkdir(parents=True,exist_ok=True);FREEZE.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');return x
def load_ledger():
    if not LEDGER.exists():return {'schema_version':SL,'events':[]}
    x=load(LEDGER)
    if x.get('schema_version')!=SL or not isinstance(x.get('events'),list):raise RuntimeError('bad V6.50.5 ledger')
    return x
def eh(x):return sha256_json(x)
def append(l,t,mid,ts,p):
    es=l['events'];e={'schema_version':SE,'sequence':len(es)+1,'event_type':t,'event_timestamp_utc':ts,'match_id':mid,'previous_event_hash':es[-1]['event_hash'] if es else 'GENESIS','payload':p};e['event_hash']=eh(e);es.append(e);return e
def audit(l):
    prev='GENESIS';err=[]
    for i,e in enumerate(l.get('events') or [],1):
        if e.get('sequence')!=i:err.append(f'seq:{i}')
        if e.get('previous_event_hash')!=prev:err.append(f'prev:{i}')
        c=dict(e);r=c.pop('event_hash',None)
        if r!=eh(c):err.append(f'hash:{i}')
        prev=str(r or '')
    return {'status':'PASS' if not err else 'FAIL','event_count':len(l.get('events') or []),'tip_hash':prev,'errors':err}
def preds(l,t):return {str(e.get('match_id')):e for e in l.get('events') or [] if e.get('event_type')==t}
def settles(l):return {str(e.get('match_id')):e for e in l.get('events') or [] if e.get('event_type')=='RESULT_SETTLED'}
def project(surface,target_total,target_result):
    states,_=klbase.build_prior_states(surface);prior=[float(s['p']) for s in states];tt={k:float(target_total[k]) for k in T};tr={d:float(target_result[d]) for d in D}
    if abs(sum(tt.values())-1)>AUDIT_TOL or abs(sum(tr.values())-1)>AUDIT_TOL:raise ValueError('target marginal sum')
    q=prior[:];conv=False;res=math.inf;iters=0
    for iters in range(1,IPF_MAX+1):
        tm,_,_=klbase.marginals(states,q)
        for k in T:
            if tt[k]>0 and tm[k]<=0:raise ValueError(f'infeasible total {k}')
            fac=tt[k]/tm[k] if tm[k]>0 else 1.0
            for i,s in enumerate(states):
                if s['total']==k:q[i]*=fac
        _,rm,_=klbase.marginals(states,q)
        for d in D:
            if tr[d]>0 and rm[d]<=0:raise ValueError(f'infeasible result {d}')
            fac=tr[d]/rm[d] if rm[d]>0 else 1.0
            for i,s in enumerate(states):
                if s['result']==d:q[i]*=fac
        tm,rm,ps=klbase.marginals(states,q);res=max(max(abs(tm[k]-tt[k]) for k in T),max(abs(rm[d]-tr[d]) for d in D),abs(ps-1))
        if res<=IPF_TOL:conv=True;break
    if not conv:raise RuntimeError(f'IPF nonconvergence {res}')
    sm={};tail={d:0.0 for d in D}
    for s,v in zip(states,q):
        if s['visible']:sm[s['key']]=v
        else:tail[s['result']]+=v
    top=sorted(sm.items(),key=lambda z:(-z[1],z[0]))[:10];kl=sum(qv*math.log(qv/pv) for qv,pv in zip(q,prior) if qv>0)
    tm,rm,ps=klbase.marginals(states,q)
    return {'total':tt,'result':tr,'score_top10':[{'score':k,'probability':v} for k,v in top],'score_matrix':sm,'tail15plus_probability':sum(tail.values()),'tail15plus_by_result':tail,'audit':{'converged':conv,'iterations':iters,'termination_residual':res,'probability_sum_residual':abs(ps-1),'max_total_constraint_residual':max(abs(tm[k]-tt[k]) for k in T),'max_result_constraint_residual':max(abs(rm[d]-tr[d]) for d in D),'objective_kl_q_to_prior':kl,'support_state_count':len(states)}}
def freeze_new(l):
    stats=Counter();sel=load(SEL);mat=load(MAT);ou=load(OU);ref=load(KLREF);sp=preds(sel,'SELECTOR_PREDICTION_FROZEN');mp=preds(mat,'MATRIX_PREDICTION_FROZEN');op=preds(ou,'TOTAL_PREDICTION_FROZEN');rp=preds(ref,'KL_MATRIX_PREDICTION_FROZEN');done=preds(l,'JOINT_MATRIX_PREDICTION_FROZEN')
    for mid in sorted(set(sp)&set(mp)&set(op)&set(rp)):
        if mid in done:stats['already_frozen']+=1;continue
        se,me,oe,re=sp[mid],mp[mid],op[mid],rp[mid];m=me.get('payload') or {};fi=m.get('fixture_identity') or {};ko=dt(fi.get('kickoff_at'))
        if ko is None:stats['bad_kickoff']+=1;continue
        s=se.get('payload') or {};o=oe.get('payload') or {};rr=re.get('payload') or {};prior=m.get('candidate') or {};tr=(s.get('prediction') or {}).get('probabilities') or {};tt=o.get('candidate_total') or {}
        try:q=project(prior,tt,tr)
        except Exception:stats['projection_rejected']+=1;continue
        ts=now();lead=(ko-ts).total_seconds()/3600
        if ko-ts<MIN_LEAD:stats['not_future_with_minimum_lead']+=1;continue
        append(l,'JOINT_MATRIX_PREDICTION_FROZEN',mid,ts.isoformat(),{'fixture_identity':fi,'projection_freeze_at_utc':ts.isoformat(),'actual_lead_hours_at_freeze':lead,'source_f05_selector_event_hash':se.get('event_hash'),'source_f06_matrix_event_hash':me.get('event_hash'),'source_v6503_total_event_hash':oe.get('event_hash'),'source_v6500_reference_event_hash':re.get('event_hash'),'source_f05_1x2':{d:float(tr[d]) for d in D},'source_v6503_total':{k:float(tt[k]) for k in T},'raw_f06_candidate':prior,'coherent_reference':rr.get('projected'),'projected':q,'market_source':{'provider_group':o.get('provider_group'),'source_market_snapshot_path':o.get('source_market_snapshot_path'),'source_market_snapshot_sha256':o.get('source_market_snapshot_sha256'),'over_under_raw':o.get('over_under_raw'),'over_under_devig':o.get('over_under_devig')},'governance':{'historical_backfill':False,'source_probability_mutation':False,'source_matrix_mutation':False,'formal_weight':0}});stats['new_predictions']+=1
    stats['source_v6503_prediction_count']=len(op);stats['source_intersection_count']=len(set(sp)&set(mp)&set(op)&set(rp));return dict(sorted(stats.items()))
def settle(l):
    stats=Counter();src=load(MAT);sp=preds(src,'MATRIX_PREDICTION_FROZEN');ss=settles(src);ps=preds(l,'JOINT_MATRIX_PREDICTION_FROZEN');done=settles(l);ts0=now()
    for mid,pe in sorted(ps.items()):
        if mid in done:continue
        fi=(pe.get('payload') or {}).get('fixture_identity') or {};ko=dt(fi.get('kickoff_at'))
        if ko is None or ts0<ko+MIN_AGE:stats['not_old_enough']+=1;continue
        se=ss.get(mid);spe=sp.get(mid)
        if not se or not spe:stats['source_settlement_missing']+=1;continue
        if (se.get('payload') or {}).get('prediction_event_hash')!=spe.get('event_hash'):stats['source_hash_mismatch']+=1;continue
        ts=now();append(l,'RESULT_SETTLED',mid,ts.isoformat(),{'prediction_event_hash':pe.get('event_hash'),'source_matrix_prediction_event_hash':spe.get('event_hash'),'source_matrix_settlement_event_hash':se.get('event_hash'),'result':(se.get('payload') or {}).get('result'),'official_result_receipt_sha256':(se.get('payload') or {}).get('official_result_receipt_sha256'),'official_result_source':(se.get('payload') or {}).get('official_result_source')});stats['new_settlements']+=1
    return dict(sorted(stats.items()))
def evaluate(l):
    ps=preds(l,'JOINT_MATRIX_PREDICTION_FROZEN');ss=settles(l);rows=[]
    for mid,se in ss.items():
        pe=ps.get(mid)
        if not pe or (se.get('payload') or {}).get('prediction_event_hash')!=pe.get('event_hash'):continue
        p=pe.get('payload') or {};r=(se.get('payload') or {}).get('result') or {};rows.append({'projected':p['projected'],'coherent_reference':p['coherent_reference'],'raw_f06_candidate':p['raw_f06_candidate'],'hg':int(r['home_goals_90']),'ag':int(r['away_goals_90'])})
    c=matrix_forward.arm_metric(rows,'projected');ref=matrix_forward.arm_metric(rows,'coherent_reference');raw=matrix_forward.arm_metric(rows,'raw_f06_candidate');n=int(c.get('count') or 0)
    g={'minimum_settled':n>=MIN_SETTLED,'total_log_nonworse_vs_raw':bool(n) and float(c['total_log_loss'])<=float(raw['total_log_loss'])+1e-12,'total_rps_nonworse_vs_raw':bool(n) and float(c['total_rps'])<=float(raw['total_rps'])+1e-12,'total_top1_nonworse_vs_raw':bool(n) and float(c['total_top1_accuracy'])>=float(raw['total_top1_accuracy'])-1e-12,'exact_score_log_nonworse_vs_v6500':bool(n) and float(c['exact_score_log_loss'])<=float(ref['exact_score_log_loss'])+1e-12,'exact_score_top1_nonworse_vs_v6500':bool(n) and float(c['exact_score_top1_accuracy'])>=float(ref['exact_score_top1_accuracy'])-1e-12,'formal_market_source_gate':False}
    return {'candidate':c,'v6500_coherent_reference':ref,'raw_f06_reference':raw,'forward_gate':{'results':g,'all_statistical_gates_pass':all(v for k,v in g.items() if k!='formal_market_source_gate'),'formal_gate_pass':all(g.values())},'decision':'PENDING_OU_RESULT_JOINT_FORWARD_SAMPLE_OR_QUALITY_AND_PROVIDER_DIVERSITY'}
def diagnostic(l):
    ps=list(preds(l,'JOINT_MATRIX_PREDICTION_FROZEN').values())
    if not ps:return {'count':0}
    audits=[(e.get('payload') or {}).get('projected',{}).get('audit',{}) for e in ps];tops=Counter();reftops=Counter();changed=0;one=0;leads=[];tmodes=Counter()
    for e in ps:
        p=e.get('payload') or {};q=p['projected'];r=p['coherent_reference'];qt=(q.get('score_top10') or [{}])[0].get('score');rt=(r.get('score_top10') or [{}])[0].get('score');tops[str(qt)]+=1;reftops[str(rt)]+=1;changed+=int(qt!=rt);one+=int(qt=='1-1');leads.append(float(p.get('actual_lead_hours_at_freeze') or 0));tmodes[str(max(T,key=lambda k:float(q['total'][k])))]+=1
    return {'count':len(ps),'all_converged':all(bool(a.get('converged')) for a in audits),'max_iterations':max(int(a.get('iterations') or 0) for a in audits),'max_termination_residual':max(float(a.get('termination_residual') or 0) for a in audits),'max_total_constraint_residual':max(float(a.get('max_total_constraint_residual') or 0) for a in audits),'max_result_constraint_residual':max(float(a.get('max_result_constraint_residual') or 0) for a in audits),'max_probability_sum_residual':max(float(a.get('probability_sum_residual') or 0) for a in audits),'mean_kl_objective':sum(float(a.get('objective_kl_q_to_prior') or 0) for a in audits)/len(audits),'candidate_total_mode_counts':dict(sorted(tmodes.items())),'candidate_score_top1_counts':dict(sorted(tops.items())),'candidate_unique_score_top1':len(tops),'candidate_one_one_top1_count':one,'candidate_one_one_top1_rate':one/len(ps),'v6500_score_top1_counts':dict(sorted(reftops.items())),'score_top1_changed_vs_v6500':changed,'score_top1_changed_rate_vs_v6500':changed/len(ps),'minimum_actual_lead_hours_at_freeze':min(leads)}
def main():
    fr=freeze();l=load_ledger();a0=audit(l)
    if a0['status']!='PASS':raise RuntimeError('pre audit failed')
    scan=freeze_new(l);st=settle(l);a1=audit(l)
    if a1['status']!='PASS':raise RuntimeError('post audit failed')
    LEDGER.parent.mkdir(parents=True,exist_ok=True);LEDGER.write_text(json.dumps(l,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');dg=diagnostic(l);ev=evaluate(l);ts=now();x={'schema_version':'V6.50.5-ou-result-joint-status-r1','generated_at_utc':ts.isoformat(),'formal_current_version':'V5.0.1','status':'PASS' if (not dg.get('count') or dg.get('all_converged')) else 'FAIL','freeze':fr,'prediction_scan':scan,'settlement_scan':st,'ledger_audit':a1,'projection_diagnostics':dg,'evaluation':ev,'governance':{'research_only':True,'single_provider_market_challenge':True,'independent_provider_consensus':False,'source_probability_mutation':False,'source_matrix_mutation':False,'historical_backfill':False,'formal_weight':0,'automatic_promotion':False,'current_rule_change':False}};STATUS.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'status':x['status'],'scan':scan,'diagnostic':dg,'evaluation':ev},ensure_ascii=False,indent=2));return 0 if x['status']=='PASS' else 1
if __name__=='__main__':raise SystemExit(main())
