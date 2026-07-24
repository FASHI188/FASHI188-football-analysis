#!/usr/bin/env python3
"""V6.17.0 prospective formal-prior multi-line total/score sidecar.

This sidecar reuses immutable V6.8.5.3 prematch market freezes, but only while the source
fixture is still in the future at the moment this sidecar creates its own freeze. It never
backfills a started/settled event.

Arms on the identical frozen market snapshot:
- prior: V5.0.1 formal score prior from current-season history strictly before the source
  market snapshot, using the competition report's frozen selected_parameters_for_live;
- singleline: prior I-projected to the source freeze's de-vigged 1X2 + O/U2.5 targets;
- multiline: prior I-projected to the source freeze's de-vigged 1X2 + every usable ordinary
  full-time half-goal total target already frozen by V6.8.5.3.

Official 90-minute settlement reuses the audited V6.8.5.3 ESPN resolver. Research only.
"""
from __future__ import annotations
import hashlib,json,math,sys
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1];ENGINE=ROOT/'engine';VALID=ROOT/'validation'
for p in (ENGINE,VALID):
    if str(p) not in sys.path:sys.path.insert(0,str(p))
import v6_multiline_research_forward_v6853 as src
import v6_multiline_market_matrix_projection_v682 as ipf
from football_v460_engine import predict_from_history
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import PlatformError,atomic_write_json,load_json,parse_iso_datetime,read_processed_matches,sha256_json

EPOCH=ROOT/'manifests'/'v6170_formalprior_multiline_forward_epoch.json'
FREEZE_DIR=ROOT/'forward'/'v6170_formalprior_multiline_freezes'
RESULT_DIR=ROOT/'evidence'/'v6170_formalprior_multiline_results'
STATUS=ROOT/'manifests'/'v6170_formalprior_multiline_forward_status.json'
SOURCE_FREEZES=ROOT/'forward'/'v6_multiline_research_freezes_v6853'
REPORT_ROOT=ROOT/'validation'/'reports'/'formal_core_v460'
CAL_ROOT=ROOT/'models'/'formal_core_v460'
EPOCH_SCHEMA='V6.17.0-formalprior-multiline-forward-epoch-r1'
FREEZE_SCHEMA='V6.17.0-formalprior-multiline-forward-freeze-r1'
RESULT_SCHEMA='V6.17.0-formalprior-multiline-forward-result-r1'
STATUS_SCHEMA='V6.17.0-formalprior-multiline-forward-status-r1'


def now_utc():return datetime.now(timezone.utc).replace(microsecond=0)
def file_sha(p:Path):return hashlib.sha256(p.read_bytes()).hexdigest()

def ensure_epoch(now):
    if EPOCH.exists():
        x=load_json(EPOCH)
        if x.get('schema_version')!=EPOCH_SCHEMA or x.get('status')!='FROZEN':raise PlatformError('invalid V6.17 epoch')
        return x
    rule_hashes={}
    for rel in ('validation/v6_multiline_market_matrix_projection_v682.py','validation/v6_multiline_research_forward_v6853.py'):
        p=ROOT/rel;rule_hashes[rel]=file_sha(p)
    x={'schema_version':EPOCH_SCHEMA,'status':'FROZEN','epoch_timestamp_utc':now.isoformat(),'formal_current_version':'V5.0.1',
       'rule':{'source_market_freeze':'V6.8.5.3 immutable prematch freeze','source_event_must_be_future_when_v617_freeze_created':True,
               'formal_prior':'selected_parameters_for_live + same-season results strictly before source market snapshot date',
               'formal_calibration':'season calibrator if present else identity',
               'singleline':'frozen 1X2 + frozen OU2.5 targets','multiline':'frozen 1X2 + all frozen ordinary half-goal total targets',
               'postmatch_reprojection':False},'code_sha256':rule_hashes,
       'governance':{'research_only':True,'historical_backfill':False,'automatic_promotion':False,'formal_probability_change':False,'formal_weight_change':False,'runtime_probability_change':False,'current_rule_change':False}}
    atomic_write_json(EPOCH,x);return x

def live_params(cid):
    p=REPORT_ROOT/f'{cid}.json'
    if not p.exists():raise PlatformError(f'formal report missing {cid}')
    rep=load_json(p);params=rep.get('selected_parameters_for_live')
    if not isinstance(params,dict):raise PlatformError(f'live params missing {cid}')
    return dict(params),{'report_path':str(p.relative_to(ROOT)),'report_sha256':file_sha(p),'engine_sha256':rep.get('engine_sha256'),'config_sha256':rep.get('config_sha256'),'selected_candidate_index_for_live':rep.get('selected_candidate_index_for_live')}

def season_temperature(cid,season):
    p=CAL_ROOT/cid/'oof_matrix_calibrator.json'
    if not p.exists():return 1.0,{'status':'IDENTITY_NO_CALIBRATOR_FILE'}
    x=load_json(p);row=(x.get('season_calibrators') or {}).get(season)
    if isinstance(row,dict):return float(row.get('temperature',1.0)),{'status':'SEASON_CALIBRATOR','mode':row.get('mode'),'temperature':float(row.get('temperature',1.0)),'path':str(p.relative_to(ROOT)),'sha256':file_sha(p)}
    return 1.0,{'status':'IDENTITY_NO_SEASON_CALIBRATOR','target_season':x.get('target_season'),'path':str(p.relative_to(ROOT)),'sha256':file_sha(p)}

def formal_prior(freeze):
    ident=freeze['fixture_identity'];cid=str(ident['competition_id']);season=str(ident['season']);home=str(ident['home_team']);away=str(ident['away_team']);observed=parse_iso_datetime(ident['freeze_observed_at_utc'],'freeze_observed_at_utc')
    params,audit=live_params(cid)
    rows=[m for m in read_processed_matches(cid) if str(m.season)==season and m.date.date()<observed.date()]
    rows.sort(key=lambda m:m.date)
    if len(rows)<20:return None,{'status':'INSUFFICIENT_CURRENT_SEASON_HISTORY','strictly_prior_match_count':len(rows),'minimum_required':20,**audit}
    try:pred=predict_from_history(rows,cid,season,home,away,observed,selected_parameters=params,use_team_effects=True)
    except Exception as exc:return None,{'status':'FORMAL_PRIOR_PREDICTION_UNAVAILABLE','error':f'{type(exc).__name__}: {exc}','strictly_prior_match_count':len(rows),**audit}
    matrix=pred.get('probabilities',{}).get('score_matrix')
    if not isinstance(matrix,list) or not matrix:return None,{'status':'FORMAL_SCORE_MATRIX_MISSING',**audit}
    temp,cal=season_temperature(cid,season);matrix=temperature_scale_matrix(matrix,temp);matrix=ipf.renorm(matrix)
    return matrix,{'status':'READY','competition_id':cid,'season':season,'home_team':home,'away_team':away,'snapshot_utc':observed.isoformat(),'same_day_results_excluded':True,'strictly_prior_match_count':len(rows),'selected_parameters_for_live':params,'temperature':temp,'calibrator':cal,'probability_sum_residual':abs(sum(p for _h,_a,p in ipf.rows(matrix))-1.0),'prior_sha256':sha256_json(matrix),**audit}

def project_targets(prior,one,targets,label):
    prior=ipf.renorm(prior);candidate=prior
    for it in range(1,ipf.MAX_ITER+1):
        candidate=ipf.scale_partition(candidate,ipf.outcome_group,one,'1x2')
        for line,target in targets:candidate=ipf.scale_partition(candidate,ipf.total_group(line),target,f'OU{line:g}')
        ores=ipf.max_residual(ipf.marginal(candidate,ipf.outcome_group),one)
        tres={str(line):ipf.max_residual(ipf.marginal(candidate,ipf.total_group(line)),target) for line,target in targets};worst=max([ores,*tres.values()])
        if worst<=ipf.TOL:
            return {'status':label,'method':'minimum_KL_IPF','objective':'minimize_KL(candidate||formal_prior)_subject_to_frozen_market_marginals','iterations':it,'converged':True,'de_vigged_1x2_target':one,'de_vigged_total_targets':{str(line):target for line,target in targets},'one_x_two_max_residual':ores,'total_line_max_residuals':tres,'max_constraint_residual':worst,'probability_sum_residual':abs(sum(p for _h,_a,p in ipf.rows(candidate))-1.0),'kl_from_prior':ipf.kl(candidate,prior),'total_goals_distribution':ipf.total_distribution(candidate),'score_diagnostics':ipf.score_diagnostics(candidate),'candidate_matrix':candidate}
    return {'status':'IPF_NONCONVERGENCE','iterations':ipf.MAX_ITER}

def new_path(event_id):return FREEZE_DIR/f'event_{event_id}.json'
def result_path(event_id):return RESULT_DIR/f'event_{event_id}.json'

def scan_source(now,epoch):
    stats=Counter();FREEZE_DIR.mkdir(parents=True,exist_ok=True)
    for p in sorted(SOURCE_FREEZES.glob('event_*.json')) if SOURCE_FREEZES.exists() else []:
        stats['source_freezes_seen']+=1
        try:
            f=load_json(p);ident=f['fixture_identity'];eid=str(ident['event_id']);kickoff=parse_iso_datetime(ident['kickoff_utc'],'kickoff_utc')
            if new_path(eid).exists():stats['already_frozen']+=1;continue
            if kickoff<=now:stats['already_started_not_backfilled']+=1;continue
            prior,pa=formal_prior(f)
            if prior is None:stats['formal_prior_unavailable']+=1;continue
            single_src=f.get('singleline_arm') or {};multi_src=f.get('multiline_arm') or {}
            one=multi_src.get('de_vigged_1x2_target') or single_src.get('de_vigged_1x2_target')
            st=single_src.get('de_vigged_total_target') or {};mt=multi_src.get('de_vigged_total_targets') or {}
            if not isinstance(one,dict) or not st or not mt:stats['frozen_targets_missing']+=1;continue
            single_targets=[(float(line),dict(target)) for line,target in st.items()];multi_targets=[(float(line),dict(target)) for line,target in mt.items()]
            single=project_targets(prior,dict(one),single_targets,'SINGLELINE_FORMALPRIOR_READY');multi=project_targets(prior,dict(one),multi_targets,'MULTILINE_FORMALPRIOR_READY')
            if single.get('status')!='SINGLELINE_FORMALPRIOR_READY' or multi.get('status')!='MULTILINE_FORMALPRIOR_READY':stats['projection_not_ready']+=1;continue
            rec={'schema_version':FREEZE_SCHEMA,'status':'FROZEN','recorded_at_utc':now.isoformat(),'research_epoch_timestamp_utc':epoch['epoch_timestamp_utc'],'fixture_identity':dict(ident),'source_v6853_freeze':{'path':str(p.relative_to(ROOT)),'file_sha256':file_sha(p),'freeze_sha256':f.get('freeze_sha256')},'formal_prior':{'audit':pa,'matrix':prior,'total_goals_distribution':ipf.total_distribution(prior),'score_diagnostics':ipf.score_diagnostics(prior)},'singleline_arm':single,'multiline_arm':multi,'governance':{'research_only':True,'source_fixture_future_when_frozen':True,'source_snapshot_reused_without_change':True,'same_formal_prior_both_market_arms':True,'postmatch_reprojection_forbidden':True,'formal_probability_change':False,'formal_weight_change':False,'runtime_probability_change':False,'current_rule_change':False}}
            rec['freeze_sha256']=sha256_json({k:v for k,v in rec.items() if k!='freeze_sha256'});atomic_write_json(new_path(eid),rec);stats['new_freezes']+=1
        except Exception:stats['source_freeze_exception']+=1
    return dict(sorted(stats.items()))

def settle(now):
    # Reuse the audited V6.8.5.3 ESPN resolver with this sidecar's directories/schemas.
    old=(src.FREEZE_DIR,src.RESULT_DIR,src.RESULT_SCHEMA)
    src.FREEZE_DIR,src.RESULT_DIR,src.RESULT_SCHEMA=FREEZE_DIR,RESULT_DIR,RESULT_SCHEMA
    try:return src.settle(now)
    finally:src.FREEZE_DIR,src.RESULT_DIR,src.RESULT_SCHEMA=old

def evaluate(now,freeze_stats,settle_stats,epoch):
    arms={'prior':[],'singleline':[],'multiline':[]};by_comp={};invalid=[];freeze_count=open_count=0
    for fp in sorted(FREEZE_DIR.glob('event_*.json')) if FREEZE_DIR.exists() else []:
        freeze_count+=1;fr=load_json(fp);eid=str(fr['fixture_identity']['event_id']);rp=result_path(eid)
        if not rp.exists():open_count+=1;continue
        rr=load_json(rp)
        if rr.get('freeze_sha256')!=fr.get('freeze_sha256'):invalid.append(eid);continue
        hg,ag=int(rr['home_goals_90']),int(rr['away_goals_90']);mats={'prior':fr['formal_prior']['matrix'],'singleline':fr['singleline_arm']['candidate_matrix'],'multiline':fr['multiline_arm']['candidate_matrix']};cid=str(fr['fixture_identity']['competition_id']);by_comp.setdefault(cid,{k:[] for k in arms})
        for name,mat in mats.items():
            z=src.matrix_metrics(mat,hg,ag);arms[name].append(z);by_comp[cid][name].append(z)
    summary={k:src.summarize(v) for k,v in arms.items()};n=summary['multiline'].get('count',0);delta={}
    if n:
        s,m=summary['singleline'],summary['multiline'];delta={'multiline_minus_singleline_score_top1_pp':100*(m['score_top1']-s['score_top1']),'multiline_minus_singleline_score_top3_pp':100*(m['score_top3']-s['score_top3']),'multiline_minus_singleline_total_top1_pp':100*(m['total_top1']-s['total_top1']),'multiline_minus_singleline_total_top2_pp':100*(m['total_top2']-s['total_top2']),'multiline_minus_singleline_joint_log':m['joint_log']-s['joint_log'],'multiline_minus_singleline_total_rps':m['total_rps']-s['total_rps']}
    return {'schema_version':STATUS_SCHEMA,'generated_at_utc':now.isoformat(),'status':'WARN_INVALID_FREEZE_RESULT_LINKS' if invalid else 'PASS','formal_current_version':'V5.0.1','research_epoch_timestamp_utc':epoch['epoch_timestamp_utc'],'freeze_scan':freeze_stats,'settlement_scan':settle_stats,'freeze_count':freeze_count,'settled_count':n,'open_count':open_count,'invalid_freeze_result_event_ids':invalid,'arms':summary,'multiline_minus_singleline':delta,'by_competition':{cid:{k:src.summarize(v) for k,v in g.items()} for cid,g in sorted(by_comp.items())},'fast100':{'minimum_settled':100,'ready':n>=100,'screen_rule':'multiline must improve total/score hit metric while joint log and total RPS are nonworse','role':'research_screen_only_not_promotion'},'governance':{'research_only':True,'source_v6853_market_freeze_reused':True,'new_sidecar_no_started_event_backfill':True,'official_90m_result_settlement':True,'postmatch_reprojection_forbidden':True,'automatic_promotion':False,'formal_probability_change':False,'formal_weight_change':False,'runtime_probability_change':False,'current_rule_change':False}}

def main():
    now=now_utc();epoch=ensure_epoch(now);fs=scan_source(now,epoch);ss=settle(now);rep=evaluate(now,fs,ss,epoch);atomic_write_json(STATUS,rep);print(json.dumps(rep,ensure_ascii=False,indent=2));return 0 if rep['status']=='PASS' else 2
if __name__=='__main__':raise SystemExit(main())
