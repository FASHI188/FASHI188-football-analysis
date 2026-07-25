#!/usr/bin/env python3
"""V6.22.4 no-backfill prospective coherent special-market matrix challenger.

Consumes V6.17.0 immutable FORMAL-prior freezes.  For a source fixture that is still in
the future when V6.22.4 first records it, this challenger reuses:
  * the exact stored V5.0.1 formal prior matrix;
  * the exact V6.8.5.3 market snapshot and raw Kambi event response already hash-bound to
    the V6.17 source freeze;
  * V6.22.3 deterministic maximal-coherent market constraint admission.

Arms are frozen before kickoff:
  prior      = stored V5.0.1 formal matrix;
  multiline  = stored V6.17 1X2 + all ordinary half-goal OU projection;
  coherent   = V6.22.3 core 1X2+OU plus only special surfaces jointly coherent at 1e-8.

No post-match projection, parameter selection, market refetch or formal weight change.
Official 90-minute settlement reuses the audited V6.8.5.3 ESPN resolver and scores only
stored matrices.
"""
from __future__ import annotations
import hashlib,json,sys
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1];VALID=ROOT/'validation'
if str(VALID) not in sys.path:sys.path.insert(0,str(VALID))
import v6170_formalprior_multiline_forward as v617
import v6_multiline_research_forward_v6853 as src
import v6_multiline_market_matrix_projection_v682 as ipf
from v6_coherent_special_market_projection_v6223 import project_coherent
from platform_core import atomic_write_json,load_json,parse_iso_datetime,sha256_json

EPOCH=ROOT/'manifests'/'v6_coherent_special_market_forward_epoch_v6224.json'
FREEZE_DIR=ROOT/'forward'/'v6_coherent_special_market_freezes_v6224'
RESULT_DIR=ROOT/'evidence'/'v6_coherent_special_market_results_v6224'
STATUS=ROOT/'manifests'/'v6_coherent_special_market_forward_v6224_status.json'
SOURCE=ROOT/'forward'/'v6170_formalprior_multiline_freezes'
LADDER=ROOT/'evidence'/'market_ladders_v680'/'kambi_full_time_ladders.json'
EPOCH_SCHEMA='V6.22.4-coherent-special-market-forward-epoch-r1'
FREEZE_SCHEMA='V6.22.4-coherent-special-market-forward-freeze-r1'
RESULT_SCHEMA='V6.22.4-coherent-special-market-forward-result-r1'
STATUS_SCHEMA='V6.22.4-coherent-special-market-forward-status-r1'

def now_utc():return datetime.now(timezone.utc).replace(microsecond=0)
def file_sha(p:Path):return hashlib.sha256(p.read_bytes()).hexdigest()
def fpath(eid):return FREEZE_DIR/f'event_{eid}.json'
def rpath(eid):return RESULT_DIR/f'event_{eid}.json'

def ensure_epoch(now):
    if EPOCH.exists():
        x=load_json(EPOCH)
        if x.get('schema_version')!=EPOCH_SCHEMA or x.get('status')!='FROZEN':raise RuntimeError('invalid V6.22.4 epoch')
        return x
    code_files=('validation/v6_coherent_special_market_projection_v6223.py','validation/v6_unified_special_market_projection_v6221.py')
    x={'schema_version':EPOCH_SCHEMA,'status':'FROZEN','epoch_timestamp_utc':now.isoformat(),'formal_current_version':'V5.0.1','rule':{'source':'V6.17.0 immutable formal-prior freeze','source_fixture_must_be_future_when_v6224_freeze_created':True,'market_snapshot':'exact hash-bound V6.8.5.3 raw Kambi snapshot','core_constraints':['de-vigged 1X2','all ordinary half-goal Total Goals'],'optional_fixed_order':['Correct Score relative','Team Total low-overround order','BTTS','safe half-goal Asian Handicap low-overround order'],'optional_admission':'accept only if whole accepted set converges with max residual <=1e-8','correct_score':'relative quoted-cell ratios only; no exhaustive probability claim','postmatch_reprojection':False},'code_sha256':{rel:file_sha(ROOT/rel) for rel in code_files},'governance':{'research_only':True,'historical_backfill':False,'automatic_promotion':False,'formal_probability_change':False,'formal_weight_change':False,'runtime_probability_change':False,'current_rule_change':False}}
    atomic_write_json(EPOCH,x);return x

def ladder_map():
    data=load_json(LADDER);return {str(x.get('raw_path') or ''):x for x in data.get('bundles') or [] if isinstance(x,dict) and x.get('raw_path')}

def source_chain(v617freeze:dict[str,Any]):
    rel=str((v617freeze.get('source_v6853_freeze') or {}).get('path') or '')
    if not rel:raise ValueError('V617 source V6853 path missing')
    p=ROOT/rel
    if not p.exists() or file_sha(p)!=str((v617freeze.get('source_v6853_freeze') or {}).get('file_sha256') or ''):raise ValueError('V6853 source file hash mismatch')
    sf=load_json(p)
    if str(sf.get('freeze_sha256') or '')!=str((v617freeze.get('source_v6853_freeze') or {}).get('freeze_sha256') or ''):raise ValueError('V6853 freeze hash mismatch')
    raw_rel=str((sf.get('source_ladder') or {}).get('raw_path') or '')
    if not raw_rel:raise ValueError('raw path missing')
    raw=ROOT/raw_rel
    if not raw.exists() or file_sha(raw)!=str((sf.get('source_ladder') or {}).get('raw_file_sha256') or ''):raise ValueError('raw file hash mismatch')
    return p,sf,raw_rel,raw,load_json(raw)

def scan(now,epoch):
    stats=Counter();FREEZE_DIR.mkdir(parents=True,exist_ok=True)
    bundles=ladder_map() if LADDER.exists() else {}
    for p in sorted(SOURCE.glob('event_*.json')) if SOURCE.exists() else []:
        stats['source_v617_freezes_seen']+=1
        try:
            f=load_json(p);ident=f.get('fixture_identity') or {};eid=str(ident.get('event_id') or '');kick=parse_iso_datetime(str(ident.get('kickoff_utc') or ''),'kickoff')
            if not eid:stats['invalid_identity']+=1;continue
            if fpath(eid).exists():stats['already_frozen']+=1;continue
            if kick<=now:stats['already_started_not_backfilled']+=1;continue
            v685p,v685,raw_rel,rawp,raw=source_chain(f);bundle=bundles.get(raw_rel)
            if not bundle:stats['bundle_missing']+=1;continue
            expected=str((v685.get('source_ladder') or {}).get('bundle_sha256') or '')
            if expected and sha256_json(bundle)!=expected:stats['bundle_hash_mismatch']+=1;continue
            prior=((f.get('formal_prior') or {}).get('matrix'))
            old_multi=((f.get('multiline_arm') or {}).get('candidate_matrix'))
            if not isinstance(prior,list) or not prior or not isinstance(old_multi,list) or not old_multi:stats['matrix_missing']+=1;continue
            result=project_coherent(prior,bundle,raw,1.0)
            if result.get('status')!='COHERENT_SPECIAL_MARKET_MATRIX_READY':stats['coherent_projection_not_ready']+=1;continue
            coherent=result.get('candidate_matrix') or []
            rec={'schema_version':FREEZE_SCHEMA,'status':'FROZEN','recorded_at_utc':now.isoformat(),'research_epoch_timestamp_utc':epoch['epoch_timestamp_utc'],'fixture_identity':dict(ident),'source_v617':{'path':str(p.relative_to(ROOT)),'file_sha256':file_sha(p),'freeze_sha256':f.get('freeze_sha256'),'formal_prior_sha256':sha256_json(prior),'old_multiline_sha256':sha256_json(old_multi)},'source_v6853':{'path':str(v685p.relative_to(ROOT)),'file_sha256':file_sha(v685p),'freeze_sha256':v685.get('freeze_sha256'),'raw_path':raw_rel,'raw_file_sha256':file_sha(rawp),'bundle_sha256':sha256_json(bundle),'raw_observed_at_utc':(v685.get('fixture_identity') or {}).get('freeze_observed_at_utc')},'arms':{'prior':{'matrix':prior,'matrix_sha256':sha256_json(prior),'score_diagnostics':ipf.score_diagnostics(prior),'total_goals_distribution':ipf.total_distribution(prior)},'multiline':{'matrix':old_multi,'matrix_sha256':sha256_json(old_multi),'score_diagnostics':ipf.score_diagnostics(old_multi),'total_goals_distribution':ipf.total_distribution(old_multi)},'coherent':{'matrix':coherent,'matrix_sha256':sha256_json(coherent),'score_diagnostics':result.get('score_diagnostics'),'total_goals_distribution':result.get('total_goals_distribution'),'projection_audit':{k:v for k,v in result.items() if k not in {'candidate_matrix','score_diagnostics','total_goals_distribution'}}}},'governance':{'research_only':True,'source_fixture_future_when_v6224_frozen':True,'exact_preexisting_market_snapshot_only':True,'market_refetch':False,'postmatch_reprojection_forbidden':True,'settlement_scores_stored_matrices_only':True,'historical_backfill':False,'automatic_promotion':False,'formal_probability_change':False,'formal_weight_change':False,'runtime_probability_change':False,'current_rule_change':False}}
            rec['freeze_sha256']=sha256_json({k:v for k,v in rec.items() if k!='freeze_sha256'});atomic_write_json(fpath(eid),rec);stats['new_freezes']+=1
            a=result.get('accepted') or {};stats['accepted_correct_score']+=int(bool(a.get('correct_score_relative')));stats['accepted_btts']+=int(bool(a.get('btts')));stats['accepted_team_total_lines']+=len(a.get('team_total_lines') or []);stats['accepted_ah_lines']+=len(a.get('asian_handicap_half_lines') or [])
        except Exception:
            stats['freeze_exception']+=1
    return dict(sorted(stats.items()))

def settle(now):
    old=(src.FREEZE_DIR,src.RESULT_DIR,src.RESULT_SCHEMA)
    src.FREEZE_DIR,src.RESULT_DIR,src.RESULT_SCHEMA=FREEZE_DIR,RESULT_DIR,RESULT_SCHEMA
    try:return src.settle(now)
    finally:src.FREEZE_DIR,src.RESULT_DIR,src.RESULT_SCHEMA=old

def evaluate(now,fs,ss,epoch):
    arms={'prior':[],'multiline':[],'coherent':[]};by_comp={};invalid=[];freeze_count=open_count=0;admission=Counter()
    for fp in sorted(FREEZE_DIR.glob('event_*.json')) if FREEZE_DIR.exists() else []:
        freeze_count+=1;fr=load_json(fp);coh=((fr.get('arms') or {}).get('coherent') or {}).get('projection_audit') or {};acc=coh.get('accepted') or {};rej=coh.get('rejected') or []
        admission['freezes']+=1;admission['correct_score_accepted']+=int(bool(acc.get('correct_score_relative')));admission['btts_accepted']+=int(bool(acc.get('btts')));admission['team_total_lines_accepted']+=len(acc.get('team_total_lines') or []);admission['ah_lines_accepted']+=len(acc.get('asian_handicap_half_lines') or []);admission['optional_rejections']+=len(rej)
        eid=str((fr.get('fixture_identity') or {}).get('event_id') or '');rp=rpath(eid)
        if not rp.exists():open_count+=1;continue
        rr=load_json(rp)
        if rr.get('freeze_sha256')!=fr.get('freeze_sha256'):invalid.append(eid);continue
        hg,ag=int(rr['home_goals_90']),int(rr['away_goals_90']);cid=str(fr['fixture_identity']['competition_id']);by_comp.setdefault(cid,{k:[] for k in arms})
        for name in arms:
            m=fr['arms'][name]['matrix'];z=src.matrix_metrics(m,hg,ag);arms[name].append(z);by_comp[cid][name].append(z)
    summary={k:src.summarize(v) for k,v in arms.items()};n=summary['coherent'].get('count',0);deltas={}
    if n:
        b,c=summary['multiline'],summary['coherent'];deltas={'coherent_minus_multiline_score_top1_pp':100*(c['score_top1']-b['score_top1']),'coherent_minus_multiline_score_top3_pp':100*(c['score_top3']-b['score_top3']),'coherent_minus_multiline_total_top1_pp':100*(c['total_top1']-b['total_top1']),'coherent_minus_multiline_total_top2_pp':100*(c['total_top2']-b['total_top2']),'coherent_minus_multiline_joint_log':c['joint_log']-b['joint_log'],'coherent_minus_multiline_total_rps':c['total_rps']-b['total_rps']}
    return {'schema_version':STATUS_SCHEMA,'generated_at_utc':now.isoformat(),'status':'WARN_INVALID_FREEZE_RESULT_LINKS' if invalid else 'PASS','formal_current_version':'V5.0.1','research_epoch_timestamp_utc':epoch['epoch_timestamp_utc'],'freeze_scan':fs,'settlement_scan':ss,'freeze_count':freeze_count,'settled_count':n,'open_count':open_count,'invalid_freeze_result_event_ids':invalid,'admission_summary':dict(admission),'arms':summary,'coherent_minus_multiline':deltas,'by_competition':{cid:{k:src.summarize(v) for k,v in g.items()} for cid,g in sorted(by_comp.items())},'fast100':{'minimum_settled':100,'ready':n>=100,'screen_rule':'coherent must improve score/total hit metric while joint log and total RPS are nonworse versus stored V6.17 multiline','role':'research_screen_only_not_promotion'},'governance':{'research_only':True,'real_v501_formal_prior':True,'exact_hash_bound_frozen_kambi_snapshot':True,'new_sidecar_no_started_event_backfill':True,'official_90m_result_settlement':True,'postmatch_reprojection_forbidden':True,'automatic_promotion':False,'formal_probability_change':False,'formal_weight_change':False,'runtime_probability_change':False,'current_rule_change':False}}

def main():
    now=now_utc();epoch=ensure_epoch(now);fs=scan(now,epoch);ss=settle(now);rep=evaluate(now,fs,ss,epoch);atomic_write_json(STATUS,rep);print(json.dumps(rep,ensure_ascii=False,indent=2));return 0 if rep['status']=='PASS' else 2
if __name__=='__main__':raise SystemExit(main())
