#!/usr/bin/env python3
"""V6.50.4 time-ordered simplex direct-total challenger.

Calibrates a convex mixture of three already-defined direct-total experts:
  COMP      competition-only P(T)
  STRENGTH  competition + pre-match Elo-strength bin
  FULL      strength + each side's pre-match recent-total bin

Weights are chosen only on history strictly before each domain's recent-two-season fixed1000
window. Same-day fixtures are predicted before any same-day update. fixed1000 is diagnostic only.
Prospective event timestamps are sampled AFTER candidate computation and require >=1h lead.
Research only; no source mutation, no backfill, formal_weight=0.
"""
from __future__ import annotations
import json, math, sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]; V=ROOT/'validation'; E=ROOT/'engine'
for p in (V,E):
    if str(p) not in sys.path: sys.path.insert(0,str(p))
import evaluate_direct_total_margin_matrix_v6477 as core
import v6_total_margin_forward_v6479 as matrix_forward
from platform_core import sha256_json
BENCHMARK=ROOT/'benchmarks'/'v6_1x2_neutral_fixed1000_v6131.json'; SOURCE=ROOT/'forward'/'v6_fresh_matrix_events_v6492.json'
FREEZE=ROOT/'manifests'/'v6_simplex_direct_total_v6504_freeze.json'; LEDGER=ROOT/'forward'/'v6_simplex_direct_total_events_v6504.json'; STATUS=ROOT/'manifests'/'v6_simplex_direct_total_v6504_status.json'
MIN_LEAD=timedelta(hours=1); MIN_RESULT_AGE=timedelta(hours=2); MIN_SETTLED=100; EPS=1e-15
SF='V6.50.4-simplex-direct-total-freeze-r1'; SL='V6.50.4-simplex-direct-total-ledger-r1'; SE='V6.50.4-simplex-direct-total-event-r1'
GRID=[(i/10,j/10,k/10) for i in range(11) for j in range(11-i) for k in [10-i-j] if k>=0]

def now()->datetime:return datetime.now(timezone.utc).replace(microsecond=0)
def dt(v:object)->datetime|None:
    try:
        x=datetime.fromisoformat(str(v or '').replace('Z','+00:00'));return x.astimezone(timezone.utc) if x.tzinfo else None
    except Exception:return None
def load(p:Path)->dict[str,Any]:
    x=json.loads(p.read_text(encoding='utf-8'))
    if not isinstance(x,dict):raise RuntimeError(str(p))
    return x
def mix(c,s,f,w):
    wc,ws,wf=w; q={x:wc*float(c[x])+ws*float(s[x])+wf*float(f[x]) for x in core.TOTAL_STATES}; z=sum(q.values())
    if z<=0 or not math.isfinite(z):raise RuntimeError('bad mixture')
    return {x:q[x]/z for x in core.TOTAL_STATES}

def empty_acc():return {'n':0,'ll':0.0,'rps':0.0,'t1':0,'t2':0,'modes':Counter()}
def add_acc(a,p,actual):
    r=core.report_total_probs(p); ac=str(actual) if actual!=core.TOTAL_TAIL and int(actual)<=6 else '7+'; tk=core.topk(r,2)
    a['n']+=1;a['ll']-=math.log(max(EPS,float(r[ac])));a['rps']+=core.rps_total(r,ac);a['t1']+=int(tk[0]==ac);a['t2']+=int(ac in tk);a['modes'][str(tk[0])]+=1
def out_acc(a):
    n=a['n']
    if not n:return {'count':0}
    return {'count':n,'total_log_loss':a['ll']/n,'total_rps':a['rps']/n,'total_top1_accuracy':a['t1']/n,'total_top2_accuracy':a['t2']/n,'mode_counts':dict(sorted(a['modes'].items())),'unique_mode_count':len(a['modes'])}
def key(w):return f'{w[0]:.1f}|{w[1]:.1f}|{w[2]:.1f}'

def calibrate():
    b=load(BENCHMARK); rows,meta=core.read_rows(); by=defaultdict(list)
    for r in rows:by[str(r['competition_id'])].append(r)
    seasons=b.get('source_meta',{}).get('selected_seasons',{}); bench={core.identity_key(r) for r in b.get('rows',[])}; dev={w:empty_acc() for w in GRID}; test={w:empty_acc() for w in GRID}; cut={}; model=core.OnlineModel()
    for cid in core.base.TARGET_COMPETITIONS:
        cr=sorted(by.get(cid,[]),key=lambda r:(r['date'],r['home_team'],r['away_team'])); ss=set(str(x) for x in seasons.get(cid,[])); dates=[str(r['date']) for r in cr if str(r['season']) in ss]; co=min(dates) if dates else None
        if co:cut[cid]=co
        days=defaultdict(list)
        for r in cr:days[str(r['date'])[:10]].append(r)
        for day in sorted(days):
            frozen=[(r,model.features(r)) for r in days[day]]
            for r,feat in frozen:
                c=model.total_probs(cid,feat,'comp');s=model.total_probs(cid,feat,'strength');f=model.total_probs(cid,feat,'full');actual=core.total_state(int(r['hg']),int(r['ag']));is_dev=bool(co and str(r['date'])<co);is_test=core.identity_key(r) in bench
                if is_dev or is_test:
                    for w in GRID:
                        p=mix(c,s,f,w)
                        if is_dev:add_acc(dev[w],p,actual)
                        if is_test:add_acc(test[w],p,actual)
            model.update_batch(frozen)
    curve={key(w):out_acc(dev[w]) for w in GRID}; eligible=[(w,curve[key(w)]) for w in GRID if int(curve[key(w)].get('count') or 0)>=3000]
    if not eligible:raise RuntimeError('no eligible simplex weight')
    # Complexity tie-break: less FULL first, then less STRENGTH, because both add finer cells.
    chosen,m=min(eligible,key=lambda z:(float(z[1]['total_log_loss']),float(z[1]['total_rps']),z[0][2],z[0][1]))
    fixed=out_acc(test[chosen]); ref=out_acc(test[(1.0,0.0,0.0)])
    return {'development_design':{'strictly_before_recent_two_season_fixed1000_window_by_domain':True,'same_day_predict_before_update':True,'simplex_step':0.1,'candidate_weight_count':len(GRID),'competition_cutoffs':cut,'source_meta':meta},'chosen_weights':{'comp':chosen[0],'strength':chosen[1],'full':chosen[2]},'chosen_development_metrics':m,'selection_rule':'minimum development total log loss, then RPS, then lower FULL weight, then lower STRENGTH weight','development_curve':curve,'fixed1000_diagnostic':{'classification':'RETROSPECTIVE_DIAGNOSTIC_ONLY_NOT_PROMOTION_EVIDENCE','candidate':fixed,'comp_reference':ref,'candidate_minus_reference':{'total_log_loss':fixed['total_log_loss']-ref['total_log_loss'],'total_rps':fixed['total_rps']-ref['total_rps'],'total_top1_accuracy':fixed['total_top1_accuracy']-ref['total_top1_accuracy'],'total_top2_accuracy':fixed['total_top2_accuracy']-ref['total_top2_accuracy']}}}
def freeze():
    if FREEZE.exists():
        x=load(FREEZE)
        if x.get('schema_version')!=SF or x.get('status')!='FROZEN':raise RuntimeError('bad freeze')
        return x
    cal=calibrate();ts=now();x={'schema_version':SF,'status':'FROZEN','freeze_timestamp_utc':ts.isoformat(),'formal_current_version':'V5.0.1','classification':'TIME_ORDERED_SIMPLEX_DIRECT_TOTAL_CHALLENGE_FORMAL_WEIGHT_0','formula':'P(T)=w_comp*P_comp+w_strength*P_strength+w_full*P_full','calibration':cal,'timing_audit':{'calibration_completed_before_freeze_timestamp':True,'per_event_timestamp_sampled_after_candidate_computation':True},'prospective_contract':{'minimum_lead_hours':1,'one_immutable_prediction_per_fixture':True,'history_cutoff_reuses_source_V6.49.2':True,'historical_backfill':False,'settlement_reuses_V6.49.2_official_90m':True},'forward_gate':{'minimum_settled':MIN_SETTLED,'total_log_nonworse':True,'total_rps_nonworse':True,'total_top1_nonworse':True,'total_top2_nonworse':True},'governance':{'research_only':True,'source_probability_mutation':False,'source_matrix_mutation':False,'formal_weight':0,'automatic_promotion':False,'current_rule_change':False}}
    FREEZE.parent.mkdir(parents=True,exist_ok=True);FREEZE.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');return x
def load_ledger():
    if not LEDGER.exists():return {'schema_version':SL,'events':[]}
    x=load(LEDGER)
    if x.get('schema_version')!=SL or not isinstance(x.get('events'),list):raise RuntimeError('bad ledger')
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
def freeze_new(fr,l):
    stats=Counter();src=load(SOURCE);sp=preds(src,'MATRIX_PREDICTION_FROZEN');done=preds(l,'TOTAL_PREDICTION_FROZEN');all_rows,_=core.read_rows();cache={};cw=(fr['calibration']['chosen_weights']['comp'],fr['calibration']['chosen_weights']['strength'],fr['calibration']['chosen_weights']['full'])
    for mid,pe in sorted(sp.items()):
        if mid in done:stats['already_frozen']+=1;continue
        pp=pe.get('payload') or {};fi=pp.get('fixture_identity') or {};ko=dt(fi.get('kickoff_at'));co=dt(pp.get('history_cutoff_utc'))
        if ko is None or co is None:stats['bad_time']+=1;continue
        if ko-now()<MIN_LEAD:stats['not_future_with_minimum_lead']+=1;continue
        ck=co.isoformat()
        if ck not in cache:cache[ck]=matrix_forward.model_as_of(co,all_rows)
        model=cache[ck];row={'competition_id':str(fi.get('competition_id') or ''),'home_team':str(fi.get('home_team') or ''),'away_team':str(fi.get('away_team') or '')};feat=model.features(row);c=model.total_probs(row['competition_id'],feat,'comp');s=model.total_probs(row['competition_id'],feat,'strength');f=model.total_probs(row['competition_id'],feat,'full');q=mix(c,s,f,cw);report=core.report_total_probs(q);ref={k:float(((pp.get('candidate') or {}).get('total') or {})[k]) for k in core.REPORT_TOTAL_STATES};ts=now();lead=(ko-ts).total_seconds()/3600
        if ko-ts<MIN_LEAD:stats['expired_during_candidate_computation']+=1;continue
        append(l,'TOTAL_PREDICTION_FROZEN',mid,ts.isoformat(),{'fixture_identity':fi,'prediction_freeze_at_utc':ts.isoformat(),'actual_lead_hours_at_freeze':lead,'source_matrix_prediction_event_hash':pe.get('event_hash'),'source_history_cutoff_utc':co.isoformat(),'features':{'strength_bin':feat[0],'home_recent_total_bin':feat[1],'away_recent_total_bin':feat[2]},'chosen_weights':fr['calibration']['chosen_weights'],'candidate_total':report,'source_comp_total':ref,'candidate_mode':core.topk(report,1)[0],'source_mode':core.topk(ref,1)[0],'governance':{'fixture_future_with_minimum_lead_at_actual_event_timestamp':True,'historical_backfill':False,'source_probability_mutation':False,'formal_weight':0}});stats['new_predictions']+=1
    return dict(sorted(stats.items()))
def settle(l):
    stats=Counter();src=load(SOURCE);sp=preds(src,'MATRIX_PREDICTION_FROZEN');ss=settles(src);ps=preds(l,'TOTAL_PREDICTION_FROZEN');done=settles(l);ts0=now()
    for mid,pe in sorted(ps.items()):
        if mid in done:continue
        fi=(pe.get('payload') or {}).get('fixture_identity') or {};ko=dt(fi.get('kickoff_at'))
        if ko is None or ts0<ko+MIN_RESULT_AGE:stats['not_old_enough']+=1;continue
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
        p=pe.get('payload') or {};r=(se.get('payload') or {}).get('result') or {};ac=core.total_cat(int(r['home_goals_90']),int(r['away_goals_90']));cr.append((p['candidate_total'],ac));rr.append((p['source_comp_total'],ac))
    c=metric(cr);r=metric(rr);n=int(c.get('count') or 0);g={'minimum_settled':n>=MIN_SETTLED,'total_log_nonworse':bool(n) and c['total_log_loss']<=r['total_log_loss']+1e-12,'total_rps_nonworse':bool(n) and c['total_rps']<=r['total_rps']+1e-12,'total_top1_nonworse':bool(n) and c['total_top1_accuracy']>=r['total_top1_accuracy']-1e-12,'total_top2_nonworse':bool(n) and c['total_top2_accuracy']>=r['total_top2_accuracy']-1e-12};return {'candidate':c,'reference':r,'forward_gate':{'results':g,'all_pass':all(g.values())},'decision':'SIMPLEX_TOTAL_REVIEW_REQUIRED' if all(g.values()) else 'PENDING_SIMPLEX_TOTAL_FORWARD_SAMPLE_OR_QUALITY'}
def diagnostic(l):
    ps=list(preds(l,'TOTAL_PREDICTION_FROZEN').values())
    if not ps:return {'count':0}
    cm=Counter(str((e.get('payload') or {}).get('candidate_mode')) for e in ps);sm=Counter(str((e.get('payload') or {}).get('source_mode')) for e in ps);changed=sum(int((e.get('payload') or {}).get('candidate_mode')!=(e.get('payload') or {}).get('source_mode')) for e in ps);leads=[float((e.get('payload') or {}).get('actual_lead_hours_at_freeze') or 0) for e in ps]
    return {'count':len(ps),'candidate_mode_counts':dict(sorted(cm.items())),'source_mode_counts':dict(sorted(sm.items())),'mode_changed_count':changed,'mode_changed_rate':changed/len(ps),'minimum_actual_lead_hours_at_freeze':min(leads)}
def main():
    fr=freeze();l=load_ledger();a0=audit(l)
    if a0['status']!='PASS':raise RuntimeError('pre audit failed')
    scan=freeze_new(fr,l);sett=settle(l);a1=audit(l)
    if a1['status']!='PASS':raise RuntimeError('post audit failed')
    LEDGER.parent.mkdir(parents=True,exist_ok=True);LEDGER.write_text(json.dumps(l,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');dg=diagnostic(l);ev=evaluate(l);ts=now();x={'schema_version':'V6.50.4-simplex-direct-total-status-r1','generated_at_utc':ts.isoformat(),'formal_current_version':'V5.0.1','status':'PASS','freeze':fr,'prediction_scan':scan,'settlement_scan':sett,'ledger_audit':a1,'prospective_diagnostics':dg,'evaluation':ev,'governance':{'research_only':True,'source_probability_mutation':False,'source_matrix_mutation':False,'historical_backfill':False,'formal_weight':0,'automatic_promotion':False,'current_rule_change':False}};STATUS.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'status':'PASS','chosen_weights':fr['calibration']['chosen_weights'],'development':fr['calibration']['chosen_development_metrics'],'fixed1000':fr['calibration']['fixed1000_diagnostic'],'scan':scan,'prospective':dg,'evaluation':ev},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
