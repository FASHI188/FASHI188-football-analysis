#!/usr/bin/env python3
from __future__ import annotations
import argparse,hashlib,importlib.util,json,subprocess,sys
from datetime import datetime,timezone,date
from pathlib import Path

HERE=Path(__file__).resolve().parent
R39L=HERE.parent/'lineup_attack_r39l'

def load_module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def modules():
    rl=load_module('r39l_eval',R39L/'evaluate_lineup_attack_r39l.py')
    rm=load_module('r39m_feat',HERE/'audit_recency_features_r39m.py')
    return rl,rm

def sha_text(s):return hashlib.sha256(s.encode()).hexdigest()
def git_blob(path):return subprocess.check_output(['git','rev-parse',f'HEAD:{path}'],text=True).strip()
def rows_hash(rows):
    lines=[]
    for r in sorted(rows,key=lambda z:z['identity']):
        lines.append(r['identity']+'|'+','.join(format(float(v),'.17g') for v in r['x']))
    return sha_text('\n'.join(lines)+'\n')

def build_all(a,pre,src,r39l_src,r39i):
    rl,rm=modules()
    f,static=rl.build_rows(a.market_dir,a.games,a.lineups,a.appearances,r39l_src,r39i)
    games,starters,complete,mapped,_,_=rm.lfeat.rebuild_mapping(a.market_dir,a.games,a.lineups,r39i)
    pidx,cidx,app_rows,bad=rm.load_recency_appearances(a.appearances,set(src['recency_contract']['eligible_appearance_competitions']))
    if bad:raise RuntimeError(f'bad appearance rows {bad}')
    dynamic={}
    for half in pre['recency_weighted_player_attack_rate']['half_life_days_candidates']:
        cache={};rr=[]
        for m in mapped:
            z,reason=rm.feature_row(m,starters,pidx,cidx,int(half),src,cache)
            if z is None:raise RuntimeError(f'dynamic feature coverage drift h={half} {m["identity"]}: {reason}')
            if z['strict_lag_violation']:raise RuntimeError(f'strict lag violation h={half} {m["identity"]}')
            rr.append({'identity':m['identity'],'season':m['season'],'div':m['div'],'date':m['target_date'],'x':[float(v) for v in z['x']],'q':m['qclose']})
        rr.sort(key=lambda r:(r['date'],r['div'],r['identity']));dynamic[int(half)]=rr
        expected=pre['coverage_binding']['feature_sha256_by_half_life'][str(half)]
        got=rm.feature_hash({r['identity']:{'x':r['x']} for r in rr})
        if got!=expected:raise RuntimeError(f'coverage feature hash drift h={half}: {got}')
    static.sort(key=lambda r:(r['date'],r['div'],r['identity']))
    ids=[r['identity'] for r in static]
    if len(static)!=9434 or any([r['identity'] for r in dynamic[h]]!=ids for h in dynamic):raise RuntimeError('static/dynamic identity alignment drift')
    return rl,rm,f,static,dynamic

def subset(rows,seasons):
    s=set(seasons);return [r for r in rows if r['season'] in s]
def by_ids(rows,ids):
    ids=set(ids);return [r for r in rows if r['identity'] in ids]
def metric(rl,rows,pred,labels):return rl.metric_pack(rows,pred,labels)

def gate(rl,rows,market,context,static,dynamic,labels,cfg):
    mm=metric(rl,rows,market,labels);cm=metric(rl,rows,context,labels);sm=metric(rl,rows,static,labels);dm=metric(rl,rows,dynamic,labels);wins,detail=rl.per_div_wins(rows,dynamic,static,labels)
    ok=(dm['HDA_LogLoss']<mm['HDA_LogLoss'] and dm['binary_Draw_LogLoss']<mm['binary_Draw_LogLoss'] and dm['HDA_Brier']<=mm['HDA_Brier'] and dm['RPS']<=mm['RPS'] and dm['HDA_LogLoss']<cm['HDA_LogLoss'] and dm['binary_Draw_LogLoss']<cm['binary_Draw_LogLoss'] and dm['HDA_LogLoss']<sm['HDA_LogLoss'] and dm['binary_Draw_LogLoss']<sm['binary_Draw_LogLoss'] and dm['Draw_AUC'] is not None and sm['Draw_AUC'] is not None and dm['Draw_AUC']>sm['Draw_AUC'] and wins>=int(cfg['dynamic_division_HDA_LogLoss_wins_vs_static_R39L_min']))
    return {'market':mm,'market_context':cm,'static_R39L':sm,'dynamic_recency':dm,'division_wins_vs_static_R39L':wins,'division_detail_vs_static_R39L':detail,'gate_pass':bool(ok)}

def blind_gate(rl,rows,market,context,static,dynamic,labels):
    mm=metric(rl,rows,market,labels);cm=metric(rl,rows,context,labels);sm=metric(rl,rows,static,labels);dm=metric(rl,rows,dynamic,labels)
    ok=(dm['HDA_LogLoss']<mm['HDA_LogLoss'] and dm['binary_Draw_LogLoss']<mm['binary_Draw_LogLoss'] and dm['HDA_Brier']<=mm['HDA_Brier'] and dm['RPS']<=mm['RPS'] and dm['HDA_LogLoss']<cm['HDA_LogLoss'] and dm['binary_Draw_LogLoss']<cm['binary_Draw_LogLoss'] and dm['HDA_LogLoss']<sm['HDA_LogLoss'] and dm['binary_Draw_LogLoss']<sm['binary_Draw_LogLoss'] and dm['Draw_AUC'] is not None and sm['Draw_AUC'] is not None and dm['Draw_AUC']>sm['Draw_AUC'])
    return {'market':mm,'market_context':cm,'static_R39L':sm,'dynamic_recency':dm,'gate_pass':bool(ok)}

def public_model(rl,m):return rl.model_public(m)
def write_stop(outdir,base,status,extra):
    base['status']=status;base['holdout_labels_accessed']=0
    (outdir/'final_freeze_receipt_r39m.json').write_text(json.dumps({'final_freeze_completed':False,'holdout_labels_accessed_before_freeze':0,**extra},indent=2))
    (outdir/'r39m_result.json').write_text(json.dumps(base,indent=2));print(json.dumps({'status':status,'holdout_labels_accessed':0},indent=2))

def self_test():
    rl,rm=modules();rl.self_test()
    raw={'p':[(date(2023,1,1).toordinal(),10.0,90.0),(date(2023,12,31).toordinal(),1.0,90.0)]};idx=rm.pack(raw);g90,m90,_=rm.weighted_before(idx,'p',date(2024,1,2),90);g365,m365,_=rm.weighted_before(idx,'p',date(2024,1,2),365);assert g90/m90<g365/m365;print('PASS_R39M_EVALUATOR_SELF_TEST')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--prereg',type=Path);ap.add_argument('--contract',type=Path);ap.add_argument('--source-registration',type=Path);ap.add_argument('--r39l-source-registration',type=Path);ap.add_argument('--r39i-registration',type=Path);ap.add_argument('--market-dir',type=Path);ap.add_argument('--raw-dir',type=Path);ap.add_argument('--games',type=Path);ap.add_argument('--lineups',type=Path);ap.add_argument('--appearances',type=Path);ap.add_argument('--out-dir',type=Path);ap.add_argument('--self-test',action='store_true');a=ap.parse_args()
    if a.self_test:self_test();return
    pre=json.loads(a.prereg.read_text());contract=json.loads(a.contract.read_text());src=json.loads(a.source_registration.read_text());r39l_src=json.loads(a.r39l_source_registration.read_text());r39i=json.loads(a.r39i_registration.read_text());outdir=a.out_dir;outdir.mkdir(parents=True,exist_ok=True)
    assert pre['preregistration_status']=='RECENCY_COVERAGE_PASSED_MODEL_FROZEN_BEFORE_R39M_TARGET_LABEL_EVALUATION';assert pre['coverage_binding']['status']=='PASS_R39M_ZERO_TARGET_LABEL_RECENCY_FEATURE_COVERAGE';assert pre['coverage_binding']['target_labels_accessed']==0;assert contract['frozen_before_real_target_label_evaluation'] is True
    rl,rm,f,static,dynamic=build_all(a,pre,src,r39l_src,r39i)
    pre_static=[r for r in static if r['season']!='2526'];hold_static=[r for r in static if r['season']=='2526']
    if (len(pre_static),len(hold_static))!=(8161,1273):raise RuntimeError('pre/hold row drift')
    fixed=sorted(hold_static,key=lambda r:f.htxt(f"{pre['blind_binding']['fixed100_seed']}|{r['identity']}"))[:100];fixed_ids=[r['identity'] for r in fixed];fixedsha=f.set_sha(fixed_ids)
    if fixedsha!=pre['blind_binding']['fixed100_identity_sha256']:raise RuntimeError(f'fixed100 drift {fixedsha}')
    dyn_hash={str(h):rm.feature_hash({r['identity']:{'x':r['x']} for r in dynamic[h]}) for h in dynamic}
    feature_freeze={'feature_freeze_completed':True,'frozen_at_utc':datetime.now(timezone.utc).isoformat(),'static_all_rows':len(static),'preholdout_rows':8161,'holdout_pool_rows':1273,'fixed100_rows':100,'fixed100_identity_sha256':fixedsha,'static_feature_sha256':rows_hash(static),'dynamic_feature_sha256_by_half_life':dyn_hash,'target_labels_accessed_before_feature_freeze':0,'scientific_blobs':{
      'prereg':git_blob('football-data/research/lineup_attack_recency_r39m/preregistration_r39m.json'),'source_registration':git_blob('football-data/research/lineup_attack_recency_r39m/source_registration_r39m.json'),'contract':git_blob('football-data/research/lineup_attack_recency_r39m/evaluator_contract_r39m.json'),'feature_builder':git_blob('football-data/research/lineup_attack_recency_r39m/audit_recency_features_r39m.py'),'evaluator':git_blob('football-data/research/lineup_attack_recency_r39m/evaluate_recency_r39m.py')}}
    (outdir/'feature_freeze_receipt_r39m.json').write_text(json.dumps(feature_freeze,indent=2))
    labels=rl.load_labels(a.raw_dir,{'1920','2021','2122','2223','2324','2425'},f,{r['identity'] for r in pre_static})
    context_l2=float(pre['model_contract']['market_context_benchmark_l2']);static_l2=float(pre['model_contract']['static_R39L_benchmark_l2']);l2s=[float(x) for x in pre['model_contract']['dynamic_full_l2_candidates']];halves=[int(x) for x in pre['recency_weighted_player_attack_rate']['half_life_days_candidates']]
    fold_bench={};candidate_streams={(h,l2):[] for h in halves for l2 in l2s};candidate_folds={(h,l2):{} for h in halves for l2 in l2s};pooled_rows=[]
    for fold in pre['rolling_development']['folds']:
        name=fold['name'];train_s=fold['train_seasons'];test_s=[fold['test_season']];train_static=subset(pre_static,train_s);test_static=subset(pre_static,test_s);test_ids=[r['identity'] for r in test_static];pooled_rows.extend(test_static)
        cm=rl.fit_offset_logistic(train_static,labels,2,context_l2,contract);sm=rl.fit_offset_logistic(train_static,labels,10,static_l2,contract);market=rl.market_pred(test_static);cp=rl.predict(test_static,cm,contract);sp=rl.predict(test_static,sm,contract)
        fold_bench[name]={'rows':test_static,'market':market,'context':cp,'static':sp,'context_model':public_model(rl,cm),'static_model':public_model(rl,sm)}
        for h in halves:
            train_dyn=subset([r for r in dynamic[h] if r['season']!='2526'],train_s);test_dyn=by_ids(dynamic[h],test_ids)
            for l2 in l2s:
                dm=rl.fit_offset_logistic(train_dyn,labels,10,l2,contract);dp=rl.predict(test_dyn,dm,contract);candidate_folds[(h,l2)][name]={'rows':test_dyn,'pred':dp,'model':public_model(rl,dm)};candidate_streams[(h,l2)].append((test_dyn,dp))
    board=[]
    for (h,l2),streams in candidate_streams.items():
        pred={};rows=[]
        for rr,pp in streams:rows.extend(rr);pred.update(pp)
        met=rl.metric_pack(rows,pred,labels);board.append({'half_life_days':h,'l2':l2,'pooled_metrics':met})
    selected=sorted(board,key=lambda z:(z['pooled_metrics']['HDA_LogLoss'],z['pooled_metrics']['binary_Draw_LogLoss'],-z['half_life_days'],-z['l2']))[0];sel=(selected['half_life_days'],selected['l2']);rolling={};rolling_pass=True
    for fold in pre['rolling_development']['folds']:
        name=fold['name'];b=fold_bench[name];c=candidate_folds[sel][name];g=gate(rl,c['rows'],b['market'],b['context'],b['static'],c['pred'],labels,pre['rolling_development']['each_fold_gate']);g['dynamic_model']=c['model'];g['context_model']=b['context_model'];g['static_model']=b['static_model'];rolling[name]=g;rolling_pass=rolling_pass and g['gate_pass']
    base={'schema_version':pre['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'source_counts':{'preholdout':8161,'holdout_pool':1273,'fixed100':100},'fixed100_identity_sha256':fixedsha,'feature_freeze_receipt':feature_freeze,'candidate_board':board,'selected_dynamic':{'half_life_days':sel[0],'l2':sel[1]},'rolling_development':rolling,'rolling_gate_pass':bool(rolling_pass),'holdout_labels_accessed':0,'hard_limits':pre['hard_limits']}
    if not rolling_pass:
        write_stop(outdir,base,pre['rolling_development']['if_fail'],{'rolling_pass':False,'selected_dynamic':base['selected_dynamic'],'fixed100_identity_sha256':fixedsha});return
    train_s=pre['policy_stress']['train_seasons'];test_s=[pre['policy_stress']['test_season']];tr_s=subset(pre_static,train_s);te_s=subset(pre_static,test_s);te_ids=[r['identity'] for r in te_s];tr_d=subset([r for r in dynamic[sel[0]] if r['season']!='2526'],train_s);te_d=by_ids(dynamic[sel[0]],te_ids)
    cm=rl.fit_offset_logistic(tr_s,labels,2,context_l2,contract);sm=rl.fit_offset_logistic(tr_s,labels,10,static_l2,contract);dm=rl.fit_offset_logistic(tr_d,labels,10,sel[1],contract);market=rl.market_pred(te_s);cp=rl.predict(te_s,cm,contract);sp=rl.predict(te_s,sm,contract);dp=rl.predict(te_d,dm,contract);pg=gate(rl,te_d,market,cp,sp,dp,labels,pre['policy_stress']['gate']);base['policy']=pg
    if not pg['gate_pass']:
        write_stop(outdir,base,pre['policy_stress']['if_fail'],{'rolling_pass':True,'policy_pass':False,'selected_dynamic':base['selected_dynamic'],'fixed100_identity_sha256':fixedsha});return
    all_pre_s=pre_static;all_pre_d=[r for r in dynamic[sel[0]] if r['season']!='2526'];cmf=rl.fit_offset_logistic(all_pre_s,labels,2,context_l2,contract);smf=rl.fit_offset_logistic(all_pre_s,labels,10,static_l2,contract);dmf=rl.fit_offset_logistic(all_pre_d,labels,10,sel[1],contract)
    final_freeze={'final_freeze_completed':True,'frozen_at_utc':datetime.now(timezone.utc).isoformat(),'rolling_pass':True,'policy_pass':True,'selected_dynamic':base['selected_dynamic'],'context_model':public_model(rl,cmf),'static_R39L_model':public_model(rl,smf),'dynamic_model':public_model(rl,dmf),'fixed100_identity_sha256':fixedsha,'holdout_labels_accessed_before_freeze':0,'feature_freeze_receipt_sha256':sha_text((outdir/'feature_freeze_receipt_r39m.json').read_text()),'scientific_blobs':feature_freeze['scientific_blobs']};(outdir/'final_freeze_receipt_r39m.json').write_text(json.dumps(final_freeze,indent=2))
    fixed_s=by_ids(hold_static,fixed_ids);fixed_d=by_ids(dynamic[sel[0]],fixed_ids);hlab=rl.load_labels(a.raw_dir,{'2526'},f,set(fixed_ids));market=rl.market_pred(fixed_s);cp=rl.predict(fixed_s,cmf,contract);sp=rl.predict(fixed_s,smf,contract);dp=rl.predict(fixed_d,dmf,contract);hg=blind_gate(rl,fixed_d,market,cp,sp,dp,hlab);base['holdout']=hg;base['holdout_labels_accessed']=100;base['final_freeze_receipt']=final_freeze;base['status']=pre['blind_confirmation']['pass_status'] if hg['gate_pass'] else pre['blind_confirmation']['fail_status'];(outdir/'r39m_result.json').write_text(json.dumps(base,indent=2));print(json.dumps({'status':base['status'],'selected_dynamic':base['selected_dynamic'],'rolling_development':rolling,'policy':pg,'holdout':hg,'holdout_labels_accessed':100},indent=2))
if __name__=='__main__':main()
