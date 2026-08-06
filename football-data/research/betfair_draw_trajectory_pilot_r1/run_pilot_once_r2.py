#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, importlib.util, json, math, os, random, re, sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT=Path(__file__).resolve().parents[2]
HELPER=ROOT/'research/betfair_basic_trajectory_r1/ingest_betfair_basic_trajectory_r1.py'
HELPER_CFG=ROOT/'research/betfair_basic_trajectory_r1/preregistration.json'
SOURCE_ROOT=Path('data/dados_historicos/por_evento_id')
PREREG_HEAD='7db94eda443c4285343ffc9bc99de906190bf88a'
PREREG_SHA='033e345348f3f809dd0a5a9e5811e8608655dbeefef6e4f2725f945263dc969b'
SOURCE_COMMIT='90f818e2ad78aa3c624a0fe251c3e60fcfb0ccff'
CANDIDATE='DRAW_T15_PLUS_HALF_T90_MOVE'
NONCE='57a9023352e24188bd2cbcd37bd896d8'
PT_RE=re.compile(r'"pt"\s*:\s*(\d+)')

class PilotError(RuntimeError): pass
@dataclass(frozen=True)
class Obs: t:datetime; p:float
@dataclass(frozen=True)
class Row: identity_hash:str; source_path:str; baseline:float; candidate:float

def load(path:Path)->dict[str,Any]:
    v=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(v,dict): raise PilotError(f'JSON object required: {path}')
    return v

def dump(path:Path,v:dict[str,Any])->None:
    path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_suffix(path.suffix+'.tmp')
    with tmp.open('w',encoding='utf-8',newline='\n') as f:
        f.write(json.dumps(v,ensure_ascii=False,indent=2,sort_keys=True)+'\n'); f.flush(); os.fsync(f.fileno())
    tmp.replace(path)
    if load(path)!=v: raise PilotError(f'persist/reload mismatch: {path}')

def csha(v:Any)->str:
    return hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def lsha(v:Iterable[str])->str: return hashlib.sha256(''.join(x+'\n' for x in v).encode()).hexdigest()
def dtiso(v:str)->datetime:
    d=datetime.fromisoformat(v.replace('Z','+00:00'))
    if d.tzinfo is None: raise PilotError('timezone missing')
    return d.astimezone(timezone.utc)
def epoch(v:int|str)->datetime: return datetime.fromtimestamp(int(v)/1000,tz=timezone.utc)
def norm(v:str)->str: return ' '.join(str(v).casefold().strip().split())
def clip(v:float)->float: return min(.999999,max(.000001,float(v)))
def qdraw(prices:tuple[float,float,float])->float:
    if any(not math.isfinite(x) or x<1.01 for x in prices): raise PilotError('invalid LTP')
    inv=[1/x for x in prices]; return inv[1]/sum(inv)
def helper():
    s=importlib.util.spec_from_file_location('r2helper',HELPER)
    if s is None or s.loader is None: raise PilotError('helper unavailable')
    m=importlib.util.module_from_spec(s); sys.modules[s.name]=m; s.loader.exec_module(m); return m

def verify(prereg_path:Path,auth_path:Path,marker_path:Path,event:str,run_no:int,attempt:int,nonce:str)->tuple[dict[str,Any],str]:
    p=load(prereg_path)
    if csha(p)!=PREREG_SHA or p.get('schema_version')!='BETFAIR-DRAW-TRAJECTORY-PILOT-PREREG-R2' or p.get('status')!='PRE_REGISTERED_NOT_AUTHORIZED_NOT_RUN': raise PilotError('prereg mismatch')
    if p['upstream_evidence'].get('source_commit')!=SOURCE_COMMIT: raise PilotError('source mismatch')
    prob=p['probability_contract']
    if prob.get('candidate_count')!=1 or prob.get('baseline')!={'id':'DRAW_FAIR_T15','formula':'qD_T15'}: raise PilotError('baseline/catalog mismatch')
    if prob.get('fixed_candidate')!={'coefficient':.5,'formula':'clip(qD_T15 + 0.5 * (qD_T15 - qD_T90))','id':CANDIDATE,'source_cutoffs_minutes_before_kickoff':[90,15]}: raise PilotError('candidate mismatch')
    ot=p['one_time_execution_contract']
    if ot.get('manual_workflow_dispatch_only') is not True or ot.get('push_trigger_allowed') is not False or ot.get('pull_request_trigger_allowed') is not False: raise PilotError('trigger boundary mismatch')
    if event!='workflow_dispatch' or run_no!=1 or attempt!=1 or nonce!=NONCE: raise PilotError('one-time guard consumed or invalid')
    a=load(auth_path)
    expected={
      'schema_version':'BETFAIR-DRAW-TRAJECTORY-PILOT-RUN-AUTH-R2','authorization_status':'AUTHORIZED_ONE_TIME_MANUAL_DISPATCH','authorized_user_message':'开始',
      'authorized_at_local':'2026-08-06T18:32:00+08:00','authorized_at_utc':'2026-08-06T10:32:00Z','authorization_nonce':NONCE,
      'preregistration_head':PREREG_HEAD,'preregistration_sha256':PREREG_SHA,'source_commit':SOURCE_COMMIT,'external_raw_data_transient_read_allowed':True,
      'winner_label_access_allowed_after_verified_lock':True,'one_time_run_only':True,'rerun_allowed':False,'retry_after_any_label_access_allowed':False,
      'raw_or_per_market_artifact_upload_allowed':False,'model_fit_allowed':False,'threshold_selection_allowed':False,'formal_weight':0,
      'current_match_use_allowed':False,'formal_ev_allowed':False}
    if any(a.get(k)!=v for k,v in expected.items()): raise PilotError('authorization mismatch')
    ah=csha(a); m=load(marker_path)
    marker={'schema_version':'BETFAIR-DRAW-TRAJECTORY-PILOT-CONSUMED-MARKER-R2','status':'AUTHORIZATION_NONCE_CONSUMED_BEFORE_EXTERNAL_OR_LABEL_ACCESS',
      'authorization_nonce':NONCE,'authorization_sha256':ah,'preregistration_sha256':PREREG_SHA,'only_allowed_event':'workflow_dispatch',
      'only_allowed_run_number':1,'only_allowed_run_attempt':1,'rerun_allowed':False}
    if any(m.get(k)!=v for k,v in marker.items()): raise PilotError('consumed marker mismatch')
    limits=p['hard_limits']; false_keys=('CURRENT_mutation_allowed','formal_config_mutation_allowed','formal_data_mutation_allowed','formal_model_mutation_allowed','formal_promotion_allowed','current_match_probability_allowed','current_match_direction_allowed','exact_score_allowed','ev_allowed','provider_account_or_credentials_access')
    if limits.get('formal_weight')!=0 or any(limits.get(k) is not False for k in false_keys): raise PilotError('hard limit mismatch')
    return p,ah

def candidate_files(checkout:Path)->list[Path]:
    root=checkout/SOURCE_ROOT
    if not root.is_dir(): raise PilotError('source root missing')
    req=(b'"marketType":"MATCH_ODDS"',b'"eventTypeId":"1"',b'"The Draw"'); out=[]
    for path in sorted(root.rglob('*')):
        if not path.is_file(): continue
        with path.open('rb') as f: prefix=f.read(2_000_000)
        if all(x in prefix for x in req): out.append(path)
    return out

def latest(hist:Sequence[Obs],target:datetime)->Obs|None:
    rows=[x for x in hist if x.t<=target]; return max(rows,key=lambda x:x.t) if rows else None

def blind(path:Path,checkout:Path,h:Any,cfg:dict[str,Any],p:dict[str,Any])->Row|None:
    definition=mapping=None; market_id=event_id=None; market_time=None; sig=None; histories:dict[int,list[Obs]]={}; prev=None; inplay=False
    with path.open('r',encoding='utf-8-sig') as f:
      for raw in f:
        raw=raw.strip()
        if not raw: continue
        mt=PT_RE.search(raw)
        if mt is None: raise PilotError('pt missing')
        n=int(mt.group(1)); t=epoch(n)
        if prev is not None and n<prev: raise PilotError('non-monotonic pt')
        prev=n
        if market_time is not None and t>=market_time: break
        msg=json.loads(raw)
        for ch in msg.get('mc') or []:
          if not isinstance(ch,dict): continue
          mid=ch.get('id')
          if mid is not None:
            mid=str(mid)
            if market_id is not None and mid!=market_id: raise PilotError('market id changed')
            market_id=mid
          inc=ch.get('marketDefinition')
          if isinstance(inc,dict):
            if str(inc.get('eventTypeId'))!='1' or inc.get('marketType')!='MATCH_ODDS': return None
            tm=dtiso(str(inc.get('marketTime'))); eid=str(inc.get('eventId') or '')
            if not eid or t>=tm: raise PilotError('bad candidate identity/time')
            if market_time is not None and tm!=market_time: raise PilotError('market time changed')
            if event_id is not None and eid!=event_id: raise PilotError('event id changed')
            market_time,event_id=tm,eid
            if inc.get('inPlay') is True: inplay=True; break
            definition=inc; mapping=h.runner_map(inc,cfg)
            if len(inc.get('runners') or [])!=3: return None
            ns=(eid,tm.isoformat(),norm(mapping['home_name']),norm(mapping['away_name']))
            if sig is not None and ns!=sig: raise PilotError('team identity changed')
            sig=ns
          for rc in ch.get('rc') or []:
            if not isinstance(rc,dict) or rc.get('id') is None or 'ltp' not in rc: continue
            try: rid=int(rc['id']); price=float(rc['ltp'])
            except (TypeError,ValueError) as e: raise PilotError('invalid explicit LTP') from e
            if not math.isfinite(price) or price<1.01: raise PilotError('invalid explicit LTP')
            histories.setdefault(rid,[]).append(Obs(t,price))
        if inplay: break
    if inplay: return None
    if definition is None or mapping is None or market_id is None or market_time is None or event_id is None: raise PilotError('incomplete identity')
    ids=[int(mapping['home_id']),int(mapping['draw_id']),int(mapping['away_id'])]
    if len(set(ids))!=3: raise PilotError('runner map collision')
    snaps={}; sync=p['synchronization_and_staleness_contract']
    for key in ('T90','T15'):
      g=sync['cutoffs'][key]; target=market_time-timedelta(minutes=int(g['minutes_before_kickoff'])); selected=[]
      for rid in ids:
        o=latest(histories.get(rid,[]),target)
        if o is None: return None
        age=(target-o.t).total_seconds()
        if age<0 or age>int(g['maximum_single_runner_staleness_seconds']): return None
        selected.append(o)
      span=(max(x.t for x in selected)-min(x.t for x in selected)).total_seconds()
      if span>int(g['maximum_home_draw_away_observation_span_seconds']): return None
      snaps[key]=tuple(x.p for x in selected)
    ident='|'.join((market_id,event_id,market_time.isoformat(),norm(mapping['home_name']),norm(mapping['away_name'])))
    ih=hashlib.sha256(ident.encode()).hexdigest(); q90=qdraw(snaps['T90']); q15=qdraw(snaps['T15'])
    return Row(ih,path.relative_to(checkout).as_posix(),clip(q15),clip(q15+.5*(q15-q90)))

def reconstruct(checkout:Path,p:dict[str,Any])->tuple[list[Row],dict[str,int]]:
    h=helper(); cfg=load(HELPER_CFG); files=candidate_files(checkout); rows=[]; sync_bad=parse_bad=0
    for path in files:
      try: row=blind(path,checkout,h,cfg,p)
      except Exception: parse_bad+=1; continue
      if row is None: sync_bad+=1
      else: rows.append(row)
    rows.sort(key=lambda x:x.identity_hash); hs=[x.identity_hash for x in rows]
    if len(hs)!=len(set(hs)): parse_bad+=len(hs)-len(set(hs))
    return rows,{'candidate_files':len(files),'synchronization_ineligible':sync_bad,'parse_or_identity_failures':parse_bad}

def sync_params(p:dict[str,Any])->dict[str,Any]:
    s=p['synchronization_and_staleness_contract']; return {'T90':s['cutoffs']['T90'],'T15':s['cutoffs']['T15'],'observation_timestamp_source':s['observation_timestamp_source'],'observation_timestamp_refresh_rule':s['observation_timestamp_refresh_rule'],'selection_rule_per_runner':s['selection_rule_per_runner']}
def make_lock(rows:list[Row],counts:dict[str,int],p:dict[str,Any],ah:str)->dict[str,Any]:
    hs=[x.identity_hash for x in rows]; g=p['sample_and_result_gates']['pre_label_gate']; ok=int(g['eligible_count_minimum'])<=len(rows)<=int(g['eligible_count_maximum']) and counts['parse_or_identity_failures']<=int(g['parse_or_identity_failures_maximum'])
    return {'schema_version':'BETFAIR-DRAW-TRAJECTORY-PILOT-IDENTITY-LOCK-R2','status':'PASS_SYNCHRONIZED_IDENTITY_LOCK_BEFORE_LABEL_ACCESS' if ok else 'STOP_NO_RESULT_SAMPLE_GATE_FAILED','source_commit':SOURCE_COMMIT,'preregistration_head':PREREG_HEAD,'preregistration_sha256':PREREG_SHA,'authorization_sha256':ah,'authorization_nonce_sha256':hashlib.sha256(NONCE.encode()).hexdigest(),'candidate_files':counts['candidate_files'],'synchronization_ineligible':counts['synchronization_ineligible'],'parse_or_identity_failures':counts['parse_or_identity_failures'],'eligible_count':len(rows),'ordered_eligible_identity_hashes':hs,'ordered_identity_hashes_sha256':lsha(hs),'synchronization_gate_parameters':sync_params(p),'pre_label_sample_gate_pass':ok,'external_raw_data_accessed':True,'winner_labels_read':0,'messages_at_or_after_kickoff_parsed_during_eligibility':0,'raw_names_prices_or_stream_messages_persisted':False,'per_market_scores_persisted':False,'model_fits':0,'thresholds_selected':0,'formal_weight':0}
def verify_lock(lock:dict[str,Any],rows:list[Row],p:dict[str,Any],ah:str)->None:
    e=make_lock(rows,{'candidate_files':int(lock.get('candidate_files',-1)),'synchronization_ineligible':int(lock.get('synchronization_ineligible',-1)),'parse_or_identity_failures':int(lock.get('parse_or_identity_failures',-1))},p,ah)
    keys=('status','preregistration_sha256','authorization_sha256','eligible_count','ordered_eligible_identity_hashes','ordered_identity_hashes_sha256','synchronization_gate_parameters','pre_label_sample_gate_pass')
    if any(lock.get(k)!=e.get(k) for k in keys) or lock.get('winner_labels_read')!=0: raise PilotError('persisted lock mismatch')

def label(path:Path,h:Any,cfg:dict[str,Any])->int:
    final=None
    with path.open('r',encoding='utf-8-sig') as f:
      for raw in f:
        raw=raw.strip()
        if not raw: continue
        msg=json.loads(raw)
        for ch in msg.get('mc') or []:
          if isinstance(ch,dict) and isinstance(ch.get('marketDefinition'),dict): final=ch['marketDefinition']
    if not isinstance(final,dict): raise PilotError('settlement missing')
    mapping=h.runner_map(final,cfg); winners=[int(x['id']) for x in final.get('runners') or [] if x.get('status')=='WINNER']
    if len(winners)!=1: raise PilotError('winner invalid')
    return int(winners[0]==int(mapping['draw_id']))
def validate_inputs(y:Sequence[int],s:Sequence[float])->None:
    if len(y)!=len(s) or not y or any(x not in (0,1) for x in y) or any(not math.isfinite(x) or not(.000001<=x<=.999999) for x in s): raise PilotError('invalid scoring inputs')
def ap(y:Sequence[int],s:Sequence[float])->float:
    validate_inputs(y,s); pos=sum(y)
    if pos==0: raise PilotError('no positives')
    groups={}
    for a,b in zip(y,s): groups.setdefault(float(b),[]).append(int(a))
    tp=seen=0; prev=area=0.0
    for score in sorted(groups,reverse=True):
      g=groups[score]; tp+=sum(g); seen+=len(g); rec=tp/pos; area+=(rec-prev)*(tp/seen); prev=rec
    return area
def auc(y:Sequence[int],s:Sequence[float])->float:
    validate_inputs(y,s); pos=[b for a,b in zip(y,s) if a==1]; neg=[b for a,b in zip(y,s) if a==0]
    if not pos or not neg: raise PilotError('AUC single class')
    return sum(1 if p>n else .5 if p==n else 0 for p in pos for n in neg)/(len(pos)*len(neg))
def metrics(y:Sequence[int],s:Sequence[float])->dict[str,float]:
    validate_inputs(y,s); n=len(y)
    return {'average_precision':ap(y,s),'roc_auc':auc(y,s),'binary_brier':sum((b-a)**2 for a,b in zip(y,s))/n,'binary_log_loss':-sum(a*math.log(b)+(1-a)*math.log(1-b) for a,b in zip(y,s))/n}
def quantile(v:Sequence[float],q:float)->float:
    a=sorted(map(float,v)); h=(len(a)-1)*q; lo=math.floor(h); hi=math.ceil(h); return a[lo] if lo==hi else a[lo]*(hi-h)+a[hi]*(h-lo)
def bootstrap(y:Sequence[int],base:Sequence[float],cand:Sequence[float],reps:int,seed:int)->dict[str,float|int]:
    validate_inputs(y,base); validate_inputs(y,cand); rng=random.Random(seed); d=[]; zero=allp=0; n=len(y)
    for _ in range(reps):
      idx=[rng.randrange(n) for _ in range(n)]; yy=[y[i] for i in idx]; total=sum(yy)
      if total==0: zero+=1; d.append(0.); continue
      if total==n: allp+=1; d.append(0.); continue
      d.append(ap(yy,[cand[i] for i in idx])-ap(yy,[base[i] for i in idx]))
    return {'repetitions':reps,'seed':seed,'no_positive_replicate_count':zero,'all_positive_replicate_count':allp,'discarded_or_redrawn_replicates':0,'p05':quantile(d,.05),'median':quantile(d,.5),'p95':quantile(d,.95)}
def selftest()->None:
    y=[1,0,1,0]; s=[.6,.6,.2,.1]
    if not math.isclose(ap(y,s),.5*.5+.5*(2/3),abs_tol=1e-15) or not math.isclose(auc(y,s),.625,abs_tol=1e-15): raise PilotError('metric selftest')
    b=bootstrap(y,s,s,100,51103)
    if any(float(b[k])!=0 for k in ('p05','median','p95')): raise PilotError('bootstrap selftest')

def final(rows:list[Row],y:list[int],lock:dict[str,Any],p:dict[str,Any],ah:str)->dict[str,Any]:
    pg=p['sample_and_result_gates']['post_label_sample_gate']; draws=sum(y); nd=len(y)-draws; ok=len(y)==len(rows) and draws>=int(pg['minimum_draws']) and nd>=int(pg['minimum_non_draws'])
    base={'schema_version':'BETFAIR-DRAW-TRAJECTORY-PILOT-FINAL-R2','source_commit':SOURCE_COMMIT,'preregistration_head':PREREG_HEAD,'preregistration_sha256':PREREG_SHA,'authorization_sha256':ah,'authorization_nonce_sha256':hashlib.sha256(NONCE.encode()).hexdigest(),'eligible_count':len(rows),'eligible_identity_set_sha256':lock['ordered_identity_hashes_sha256'],'winner_labels_read':len(y),'draws':draws,'non_draws':nd,'pre_label_sample_gate_pass':lock['pre_label_sample_gate_pass'],'post_label_sample_gate_pass':ok,'external_raw_data_accessed':True,'model_fits':0,'thresholds_selected':0,'candidate_count':1,'candidate_id':CANDIDATE,'candidate_selection_performed':False,'raw_or_per_market_data_persisted_or_uploaded':False,'basic_ltp_treated_as_executable_price':False,'formal_weight':0,'formal_model_changes':0,'formal_data_changes':0,'formal_config_changes':0,'CURRENT_changes':0,'current_match_use_allowed':False,'formal_ev_allowed':False,'rerun_allowed':False}
    if not ok: return {**base,'status':'STOP_NO_RESULT_SAMPLE_GATE_FAILED','baseline_metrics':None,'candidate_metrics':None,'candidate_minus_baseline_deltas':None,'average_precision_delta_bootstrap':None,'research_pass_gates':None,'research_gate_pass':False}
    bs=[x.baseline for x in rows]; cs=[x.candidate for x in rows]; bm=metrics(y,bs); cm=metrics(y,cs); d={'average_precision':cm['average_precision']-bm['average_precision'],'roc_auc':cm['roc_auc']-bm['roc_auc'],'binary_brier':cm['binary_brier']-bm['binary_brier'],'binary_log_loss':cm['binary_log_loss']-bm['binary_log_loss']}
    bc=p['bootstrap_contract']; boot=bootstrap(y,bs,cs,int(bc['repetitions']),int(bc['seed'])); g=p['sample_and_result_gates']['research_pass_gate_all_required']
    gates={'average_precision_delta_strictly_positive':d['average_precision']>float(g['average_precision_delta_strictly_greater_than']),'average_precision_bootstrap_p05_strictly_positive':float(boot['p05'])>float(g['average_precision_bootstrap_p05_strictly_greater_than']),'roc_auc_delta_nonnegative':d['roc_auc']>=float(g['roc_auc_delta_greater_than_or_equal_to']),'brier_delta_nonpositive':d['binary_brier']<=float(g['brier_delta_less_than_or_equal_to']),'log_loss_delta_nonpositive':d['binary_log_loss']<=float(g['log_loss_delta_less_than_or_equal_to'])}
    passed=all(gates.values()); sg=p['sample_and_result_gates']
    return {**base,'status':sg['pass_status'] if passed else sg['candidate_not_above_all_gates_status'],'baseline_metrics':bm,'candidate_metrics':cm,'candidate_minus_baseline_deltas':d,'average_precision_delta_bootstrap':boot,'research_pass_gates':gates,'research_gate_pass':passed}

def run(args:argparse.Namespace)->None:
    p,ah=verify(args.prereg,args.authorization,args.consumed_marker,args.event_name,args.run_number,args.run_attempt,args.authorization_nonce); selftest(); rows,counts=reconstruct(args.source_checkout,p); lk=make_lock(rows,counts,p,ah); dump(args.lock_out,lk); lk=load(args.lock_out); verify_lock(lk,rows,p,ah)
    if not lk['pre_label_sample_gate_pass']:
      out={'schema_version':'BETFAIR-DRAW-TRAJECTORY-PILOT-FINAL-R2','status':'STOP_NO_RESULT_SAMPLE_GATE_FAILED','source_commit':SOURCE_COMMIT,'preregistration_head':PREREG_HEAD,'preregistration_sha256':PREREG_SHA,'authorization_sha256':ah,'authorization_nonce_sha256':hashlib.sha256(NONCE.encode()).hexdigest(),'eligible_count':len(rows),'eligible_identity_set_sha256':lk['ordered_identity_hashes_sha256'],'candidate_files':counts['candidate_files'],'synchronization_ineligible':counts['synchronization_ineligible'],'parse_or_identity_failures':counts['parse_or_identity_failures'],'winner_labels_read':0,'external_raw_data_accessed':True,'model_fits':0,'thresholds_selected':0,'formal_weight':0,'formal_model_changes':0,'formal_data_changes':0,'formal_config_changes':0,'CURRENT_changes':0,'rerun_allowed':False,'failure_stage':'PRE_LABEL_SAMPLE_AND_IDENTITY_LOCK_GATE'}; dump(args.final_out,out); print(json.dumps(out,ensure_ascii=False,sort_keys=True)); return
    h=helper(); cfg=load(HELPER_CFG); y=[]; errors=0
    for row in rows:
      try: y.append(label(args.source_checkout/row.source_path,h,cfg))
      except Exception: errors+=1
    if errors:
      out={'schema_version':'BETFAIR-DRAW-TRAJECTORY-PILOT-FINAL-R2','status':'STOP_NO_RESULT_SAMPLE_GATE_FAILED','source_commit':SOURCE_COMMIT,'preregistration_head':PREREG_HEAD,'preregistration_sha256':PREREG_SHA,'authorization_sha256':ah,'authorization_nonce_sha256':hashlib.sha256(NONCE.encode()).hexdigest(),'eligible_count':len(rows),'eligible_identity_set_sha256':lk['ordered_identity_hashes_sha256'],'winner_labels_read':len(rows),'valid_labels':len(y),'invalid_or_missing_labels':errors,'external_raw_data_accessed':True,'model_fits':0,'thresholds_selected':0,'formal_weight':0,'formal_model_changes':0,'formal_data_changes':0,'formal_config_changes':0,'CURRENT_changes':0,'rerun_allowed':False,'failure_stage':'POST_LOCK_LABEL_VALIDATION_GATE'}; dump(args.final_out,out); print(json.dumps(out,ensure_ascii=False,sort_keys=True)); return
    out=final(rows,y,lk,p,ah); dump(args.final_out,out); print(json.dumps(out,ensure_ascii=False,sort_keys=True))
def preflight(args:argparse.Namespace)->None:
    p,ah=verify(args.prereg,args.authorization,args.consumed_marker,args.event_name,args.run_number,args.run_attempt,args.authorization_nonce); selftest(); out={'status':'PASS_R2_ONE_TIME_EXECUTION_PREFLIGHT_NO_EXTERNAL_DATA','preregistration_sha256':csha(p),'authorization_sha256':ah,'authorization_nonce_sha256':hashlib.sha256(NONCE.encode()).hexdigest(),'event_name':args.event_name,'run_number':args.run_number,'run_attempt':args.run_attempt,'external_data_accessed':False,'winner_labels_read':0,'model_fits':0,'thresholds_selected':0,'formal_weight':0};
    if args.preflight_out: dump(args.preflight_out,out)
    print(json.dumps(out,ensure_ascii=False,sort_keys=True))
def main()->None:
    apx=argparse.ArgumentParser(); apx.add_argument('mode',choices=['preflight','run']); apx.add_argument('--prereg',type=Path,required=True); apx.add_argument('--authorization',type=Path,required=True); apx.add_argument('--consumed-marker',type=Path,required=True); apx.add_argument('--event-name',required=True); apx.add_argument('--run-number',type=int,required=True); apx.add_argument('--run-attempt',type=int,required=True); apx.add_argument('--authorization-nonce',required=True); apx.add_argument('--source-checkout',type=Path); apx.add_argument('--lock-out',type=Path); apx.add_argument('--final-out',type=Path); apx.add_argument('--preflight-out',type=Path); args=apx.parse_args()
    if args.mode=='preflight': preflight(args)
    elif args.source_checkout is None or args.lock_out is None or args.final_out is None: raise PilotError('run paths required')
    else: run(args)
if __name__=='__main__': main()
