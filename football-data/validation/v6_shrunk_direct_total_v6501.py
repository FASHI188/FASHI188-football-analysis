#!/usr/bin/env python3
"""V6.50.1-R2 time-ordered shrunk direct-total challenger.

Candidate P(T)=(1-w)P_comp(T)+wP_strength(T). The global w is selected only on
strictly-pre-fixed1000 development history. Crucially, prospective timestamps are sampled
AFTER historical calibration and AFTER each candidate computation, so slow calibration can
never manufacture an earlier lead time. Research only, formal_weight=0.
"""
from __future__ import annotations
import json, math, sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]; VALIDATION=ROOT/'validation'; ENGINE=ROOT/'engine'
for p in (VALIDATION,ENGINE):
    if str(p) not in sys.path: sys.path.insert(0,str(p))
import evaluate_direct_total_margin_matrix_v6477 as core
import v6_total_margin_forward_v6479 as matrix_forward
from platform_core import sha256_json
BENCHMARK=ROOT/'benchmarks'/'v6_1x2_neutral_fixed1000_v6131.json'
SOURCE_MATRIX=ROOT/'forward'/'v6_fresh_matrix_events_v6492.json'
FREEZE=ROOT/'manifests'/'v6_shrunk_direct_total_v6501_freeze.json'
LEDGER=ROOT/'forward'/'v6_shrunk_direct_total_events_v6501.json'
STATUS=ROOT/'manifests'/'v6_shrunk_direct_total_v6501_status.json'
WEIGHT_GRID=(0.0,0.10,0.20,0.30,0.40,0.50,0.60,0.70,0.80,0.90,1.0)
MIN_DEV_N=3000; MIN_LEAD=timedelta(hours=1); MIN_RESULT_AGE=timedelta(hours=2); MIN_SETTLED=100; EPS=1e-15
SCHEMA_FREEZE='V6.50.1-shrunk-direct-total-freeze-r2'; SCHEMA_LEDGER='V6.50.1-shrunk-direct-total-ledger-r2'; SCHEMA_EVENT='V6.50.1-shrunk-direct-total-event-r2'

def now_utc()->datetime: return datetime.now(timezone.utc).replace(microsecond=0)
def parse_dt(v:object)->datetime|None:
    try:
        x=datetime.fromisoformat(str(v or '').replace('Z','+00:00')); return x.astimezone(timezone.utc) if x.tzinfo else None
    except Exception:return None
def load(p:Path)->dict[str,Any]:
    x=json.loads(p.read_text(encoding='utf-8'))
    if not isinstance(x,dict): raise RuntimeError(f'not object:{p}')
    return x
def mix_internal(comp:dict[Any,float],strength:dict[Any,float],w:float)->dict[Any,float]:
    out={s:(1-w)*float(comp[s])+w*float(strength[s]) for s in core.TOTAL_STATES}; z=sum(out.values())
    if not math.isfinite(z) or z<=0: raise RuntimeError('invalid total mixture')
    return {s:out[s]/z for s in core.TOTAL_STATES}
def metric_rows(rows:list[tuple[dict[Any,float],Any]])->dict[str,Any]:
    n=len(rows)
    if not n:return {'count':0}
    ll=rps=0.0; top1=top2=0; modes=Counter()
    for p,actual in rows:
        report=core.report_total_probs(p); ac=str(actual) if actual!=core.TOTAL_TAIL and int(actual)<=6 else '7+'
        ll-=math.log(max(EPS,float(report[ac]))); rps+=core.rps_total(report,ac); tk=core.topk(report,2)
        top1+=int(tk[0]==ac); top2+=int(ac in tk); modes[str(tk[0])]+=1
    return {'count':n,'total_log_loss':ll/n,'total_rps':rps/n,'total_top1_accuracy':top1/n,'total_top2_accuracy':top2/n,'mode_counts':dict(sorted(modes.items())),'unique_mode_count':len(modes)}

def calibration()->dict[str,Any]:
    benchmark=load(BENCHMARK); rows,meta=core.read_rows(); by=defaultdict(list)
    for r in rows: by[str(r['competition_id'])].append(r)
    selected_seasons=benchmark.get('source_meta',{}).get('selected_seasons',{}); bench_keys={core.identity_key(r) for r in benchmark.get('rows',[])}
    dev={w:[] for w in WEIGHT_GRID}; test={w:[] for w in WEIGHT_GRID}; cutoffs={}; model=core.OnlineModel()
    for cid in core.base.TARGET_COMPETITIONS:
        cr=sorted(by.get(cid,[]),key=lambda r:(r['date'],r['home_team'],r['away_team'])); seasons=set(str(x) for x in selected_seasons.get(cid,[]))
        dates=[str(r['date']) for r in cr if str(r['season']) in seasons]; cutoff=min(dates) if dates else None
        if cutoff: cutoffs[cid]=cutoff
        days=defaultdict(list)
        for r in cr: days[str(r['date'])[:10]].append(r)
        for day in sorted(days):
            frozen=[(r,model.features(r)) for r in days[day]]
            for r,feat in frozen:
                comp=model.total_probs(cid,feat,'comp'); strength=model.total_probs(cid,feat,'strength'); actual=core.total_state(int(r['hg']),int(r['ag']))
                is_dev=bool(cutoff and str(r['date'])<cutoff); is_test=core.identity_key(r) in bench_keys
                for w in WEIGHT_GRID:
                    p=mix_internal(comp,strength,w)
                    if is_dev: dev[w].append((p,actual))
                    if is_test: test[w].append((p,actual))
            model.update_batch(frozen)
    curve={str(w):metric_rows(dev[w]) for w in WEIGHT_GRID}; eligible=[(w,curve[str(w)]) for w in WEIGHT_GRID if int(curve[str(w)].get('count') or 0)>=MIN_DEV_N]
    if not eligible: raise RuntimeError('no weight has enough development rows')
    chosen,m=min(eligible,key=lambda z:(float(z[1]['total_log_loss']),float(z[1]['total_rps']),z[0])); fixed=metric_rows(test[chosen]); ref=metric_rows(test[0.0])
    return {'development_design':{'strictly_before_recent_two_season_fixed1000_window_by_domain':True,'same_day_predict_before_update':True,'weight_grid_predeclared':list(WEIGHT_GRID),'minimum_development_n':MIN_DEV_N,'competition_cutoffs':cutoffs,'source_meta':meta},'development_curve':curve,'chosen_weight':chosen,'chosen_development_metrics':m,'selection_rule':'minimum development total log loss, then RPS, then lower weight','fixed1000_diagnostic':{'classification':'RETROSPECTIVE_DIAGNOSTIC_ONLY_NOT_PROMOTION_EVIDENCE','chosen_candidate':fixed,'comp_reference':ref,'candidate_minus_reference':{'total_log_loss':float(fixed['total_log_loss'])-float(ref['total_log_loss']),'total_rps':float(fixed['total_rps'])-float(ref['total_rps']),'total_top1_accuracy':float(fixed['total_top1_accuracy'])-float(ref['total_top1_accuracy']),'total_top2_accuracy':float(fixed['total_top2_accuracy'])-float(ref['total_top2_accuracy'])}}}

def ensure_freeze()->dict[str,Any]:
    if FREEZE.exists():
        x=load(FREEZE)
        if x.get('schema_version')!=SCHEMA_FREEZE or x.get('status')!='FROZEN' or (x.get('timing_audit') or {}).get('calibration_completed_before_freeze_timestamp') is not True:
            raise RuntimeError('invalid/stale V6.50.1 freeze; r2 timing contract required')
        return x
    cal=calibration()                       # heavy historical work finishes FIRST
    freeze_at=now_utc()                    # only now may the prospective epoch timestamp exist
    x={'schema_version':SCHEMA_FREEZE,'status':'FROZEN','freeze_timestamp_utc':freeze_at.isoformat(),'formal_current_version':'V5.0.1','classification':'TIME_ORDERED_SHRUNK_DIRECT_TOTAL_CHALLENGE_FORMAL_WEIGHT_0','formula':'P_candidate(T)=(1-w)*P_comp(T)+w*P_strength(T)','calibration':cal,'timing_audit':{'calibration_completed_before_freeze_timestamp':True,'per_event_timestamp_sampled_after_candidate_computation':True},'prospective_contract':{'source_fixture':'V6.49.2 immutable matrix prediction','history_cutoff':'reuse each source matrix history_cutoff_utc; never use later results','fixture_future_at_candidate_freeze':True,'minimum_lead_hours':1,'one_immutable_prediction_per_fixture':True,'historical_backfill':False,'settlement_reuses_matching_V6.49.2 official 90m settlement':True},'forward_gate':{'minimum_settled':MIN_SETTLED,'candidate_total_log_nonworse':True,'candidate_total_rps_nonworse':True,'candidate_total_top1_nonworse':True,'candidate_total_top2_nonworse':True},'governance':{'research_only':True,'source_probability_mutation':False,'source_matrix_mutation':False,'formal_weight':0,'automatic_promotion':False,'current_rule_change':False}}
    FREEZE.parent.mkdir(parents=True,exist_ok=True); FREEZE.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); return x

def load_ledger()->dict[str,Any]:
    if not LEDGER.exists(): return {'schema_version':SCHEMA_LEDGER,'events':[]}
    x=load(LEDGER)
    if x.get('schema_version')!=SCHEMA_LEDGER or not isinstance(x.get('events'),list): raise RuntimeError('invalid/stale V6.50.1 ledger; r2 required')
    return x
def event_hash(x:dict[str,Any])->str:return sha256_json(x)
def append(ledger:dict[str,Any],typ:str,mid:str,ts:str,payload:dict[str,Any])->dict[str,Any]:
    es=ledger['events']; e={'schema_version':SCHEMA_EVENT,'sequence':len(es)+1,'event_type':typ,'event_timestamp_utc':ts,'match_id':mid,'previous_event_hash':es[-1]['event_hash'] if es else 'GENESIS','payload':payload}; e['event_hash']=event_hash(e); es.append(e); return e
def audit(ledger:dict[str,Any])->dict[str,Any]:
    prev='GENESIS'; errors=[]
    for i,e in enumerate(ledger.get('events') or [],1):
        if e.get('sequence')!=i:errors.append(f'sequence:{i}')
        if e.get('previous_event_hash')!=prev:errors.append(f'previous_hash:{i}')
        c=dict(e); recorded=c.pop('event_hash',None)
        if recorded!=event_hash(c):errors.append(f'hash:{i}')
        prev=str(recorded or '')
    return {'status':'PASS' if not errors else 'FAIL','event_count':len(ledger.get('events') or []),'tip_hash':prev,'errors':errors}
def pred_events(ledger:dict[str,Any],typ:str)->dict[str,dict[str,Any]]:return {str(e.get('match_id')):e for e in ledger.get('events') or [] if e.get('event_type')==typ}
def settled_events(ledger:dict[str,Any])->dict[str,dict[str,Any]]:return {str(e.get('match_id')):e for e in ledger.get('events') or [] if e.get('event_type')=='RESULT_SETTLED'}

def freeze_new(freeze:dict[str,Any],ledger:dict[str,Any])->dict[str,int]:
    stats=Counter(); src=load(SOURCE_MATRIX); src_preds=pred_events(src,'MATRIX_PREDICTION_FROZEN'); existing=pred_events(ledger,'TOTAL_PREDICTION_FROZEN'); all_rows,_=core.read_rows(); cache={}; w=float((freeze.get('calibration') or {}).get('chosen_weight'))
    for mid,pe in sorted(src_preds.items()):
        if mid in existing:stats['already_frozen']+=1;continue
        pp=pe.get('payload') or {}; fi=pp.get('fixture_identity') or {}; ko=parse_dt(fi.get('kickoff_at')); cutoff=parse_dt(pp.get('history_cutoff_utc'))
        if ko is None or cutoff is None:stats['bad_time']+=1;continue
        # Cheap precheck only; a second hard check is made AFTER model/prediction computation.
        if ko-now_utc()<MIN_LEAD:stats['not_future_with_minimum_lead']+=1;continue
        ck=cutoff.date().isoformat()
        if ck not in cache:cache[ck]=matrix_forward.model_as_of(cutoff,all_rows)
        model=cache[ck]; row={'competition_id':str(fi.get('competition_id') or ''),'home_team':str(fi.get('home_team') or ''),'away_team':str(fi.get('away_team') or '')}; feat=model.features(row)
        comp=model.total_probs(row['competition_id'],feat,'comp'); strength=model.total_probs(row['competition_id'],feat,'strength'); cand=mix_internal(comp,strength,w); report=core.report_total_probs(cand); source_total={k:float(((pp.get('candidate') or {}).get('total') or {})[k]) for k in core.REPORT_TOTAL_STATES}
        event_now=now_utc()                 # authoritative prospective timestamp after computation
        lead=(ko-event_now).total_seconds()/3600.0
        if ko-event_now<MIN_LEAD:stats['expired_during_candidate_computation']+=1;continue
        append(ledger,'TOTAL_PREDICTION_FROZEN',mid,event_now.isoformat(),{'fixture_identity':fi,'projection_freeze_at_utc':event_now.isoformat(),'actual_lead_hours_at_freeze':lead,'source_matrix_prediction_event_hash':pe.get('event_hash'),'source_history_cutoff_utc':cutoff.isoformat(),'features':{'strength_bin':feat[0],'home_recent_total_bin':feat[1],'away_recent_total_bin':feat[2]},'chosen_weight':w,'candidate_internal_total':{str(k):float(v) for k,v in cand.items()},'candidate_total':report,'source_comp_total':source_total,'candidate_mode':core.topk(report,1)[0],'source_mode':core.topk(source_total,1)[0],'governance':{'fixture_future_with_minimum_lead_at_actual_event_timestamp':True,'historical_backfill':False,'source_probability_mutation':False,'formal_weight':0}});stats['new_predictions']+=1
    return dict(sorted(stats.items()))

def settle_from_source(ledger:dict[str,Any])->dict[str,int]:
    now=now_utc(); stats=Counter(); src=load(SOURCE_MATRIX); src_preds=pred_events(src,'MATRIX_PREDICTION_FROZEN'); src_set=settled_events(src); preds=pred_events(ledger,'TOTAL_PREDICTION_FROZEN'); settled=settled_events(ledger)
    for mid,pe in sorted(preds.items()):
        if mid in settled:continue
        fi=(pe.get('payload') or {}).get('fixture_identity') or {}; ko=parse_dt(fi.get('kickoff_at'))
        if ko is None or now<ko+MIN_RESULT_AGE:stats['not_old_enough']+=1;continue
        se=src_set.get(mid); src_pe=src_preds.get(mid)
        if not se or not src_pe:stats['source_settlement_missing']+=1;continue
        if (se.get('payload') or {}).get('prediction_event_hash')!=src_pe.get('event_hash'):stats['source_hash_mismatch']+=1;continue
        event_now=now_utc(); append(ledger,'RESULT_SETTLED',mid,event_now.isoformat(),{'prediction_event_hash':pe.get('event_hash'),'source_matrix_prediction_event_hash':src_pe.get('event_hash'),'source_matrix_settlement_event_hash':se.get('event_hash'),'result':(se.get('payload') or {}).get('result'),'official_result_receipt_sha256':(se.get('payload') or {}).get('official_result_receipt_sha256'),'official_result_source':(se.get('payload') or {}).get('official_result_source')});stats['new_settlements']+=1
    return dict(sorted(stats.items()))
def evaluate(ledger:dict[str,Any])->dict[str,Any]:
    preds=pred_events(ledger,'TOTAL_PREDICTION_FROZEN'); settled=settled_events(ledger); cr=[]; rr=[]
    for mid,se in settled.items():
        pe=preds.get(mid)
        if not pe or (se.get('payload') or {}).get('prediction_event_hash')!=pe.get('event_hash'):continue
        pp=pe.get('payload') or {}; r=(se.get('payload') or {}).get('result') or {}; ac=core.total_cat(int(r['home_goals_90']),int(r['away_goals_90'])); cr.append(({k:float((pp.get('candidate_total') or {})[k]) for k in core.REPORT_TOTAL_STATES},ac)); rr.append(({k:float((pp.get('source_comp_total') or {})[k]) for k in core.REPORT_TOTAL_STATES},ac))
    def calc(rows):
        n=len(rows)
        if not n:return {'count':0}
        ll=rps=0.0; t1=t2=0
        for p,ac in rows:ll-=math.log(max(EPS,p[ac]));rps+=core.rps_total(p,ac);tk=core.topk(p,2);t1+=int(tk[0]==ac);t2+=int(ac in tk)
        return {'count':n,'total_log_loss':ll/n,'total_rps':rps/n,'total_top1_accuracy':t1/n,'total_top2_accuracy':t2/n}
    c=calc(cr);r=calc(rr);n=int(c.get('count') or 0);g={'minimum_settled':n>=MIN_SETTLED,'total_log_nonworse':bool(n) and c['total_log_loss']<=r['total_log_loss']+1e-12,'total_rps_nonworse':bool(n) and c['total_rps']<=r['total_rps']+1e-12,'total_top1_nonworse':bool(n) and c['total_top1_accuracy']>=r['total_top1_accuracy']-1e-12,'total_top2_nonworse':bool(n) and c['total_top2_accuracy']>=r['total_top2_accuracy']-1e-12};return {'candidate':c,'reference':r,'forward_gate':{'results':g,'all_pass':all(g.values())},'decision':'SHRUNK_TOTAL_REVIEW_REQUIRED' if all(g.values()) else 'PENDING_SHRUNK_TOTAL_FORWARD_SAMPLE_OR_QUALITY'}
def diagnostics(ledger:dict[str,Any])->dict[str,Any]:
    preds=list(pred_events(ledger,'TOTAL_PREDICTION_FROZEN').values())
    if not preds:return {'count':0}
    cm=Counter(str((e.get('payload') or {}).get('candidate_mode')) for e in preds);sm=Counter(str((e.get('payload') or {}).get('source_mode')) for e in preds);changed=sum(int((e.get('payload') or {}).get('candidate_mode')!=(e.get('payload') or {}).get('source_mode')) for e in preds);l1=[];leads=[]
    for e in preds:
        p=e.get('payload') or {};c=p.get('candidate_total') or {};r=p.get('source_comp_total') or {};l1.append(sum(abs(float(c[k])-float(r[k])) for k in core.REPORT_TOTAL_STATES));leads.append(float(p.get('actual_lead_hours_at_freeze') or 0.0))
    return {'count':len(preds),'candidate_mode_counts':dict(sorted(cm.items())),'source_mode_counts':dict(sorted(sm.items())),'mode_changed_count':changed,'mode_changed_rate':changed/len(preds),'mean_l1_from_comp':sum(l1)/len(l1),'max_l1_from_comp':max(l1),'minimum_actual_lead_hours_at_freeze':min(leads)}
def main()->int:
    freeze=ensure_freeze(); ledger=load_ledger(); before=audit(ledger)
    if before['status']!='PASS':raise RuntimeError('pre-run ledger audit failed')
    scan=freeze_new(freeze,ledger);settle=settle_from_source(ledger);after=audit(ledger)
    if after['status']!='PASS':raise RuntimeError('post-run ledger audit failed')
    LEDGER.parent.mkdir(parents=True,exist_ok=True);LEDGER.write_text(json.dumps(ledger,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');diag=diagnostics(ledger);ev=evaluate(ledger);generated=now_utc()
    status={'schema_version':'V6.50.1-shrunk-direct-total-status-r2','generated_at_utc':generated.isoformat(),'formal_current_version':'V5.0.1','status':'PASS','freeze':freeze,'prediction_scan':scan,'settlement_scan':settle,'ledger_audit':after,'prospective_diagnostics':diag,'evaluation':ev,'governance':{'research_only':True,'source_probability_mutation':False,'source_matrix_mutation':False,'historical_backfill':False,'formal_weight':0,'automatic_promotion':False,'current_rule_change':False}}
    STATUS.write_text(json.dumps(status,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'status':status['status'],'chosen_weight':(freeze.get('calibration') or {}).get('chosen_weight'),'development':(freeze.get('calibration') or {}).get('chosen_development_metrics'),'fixed1000':(freeze.get('calibration') or {}).get('fixed1000_diagnostic'),'prediction_scan':scan,'prospective_diagnostics':diag,'evaluation':ev},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
