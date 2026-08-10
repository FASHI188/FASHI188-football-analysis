#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,hashlib,importlib.util,json,subprocess
from datetime import datetime,timezone,datetime as dt
from pathlib import Path
import numpy as np

HERE=Path(__file__).resolve().parent
R39L=HERE.parent/'lineup_attack_r39l'

def load_module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def core_modules():
    core=load_module('r39m_core',HERE/'evaluate_recency_r39m.py')
    rl=load_module('r39l_eval',R39L/'evaluate_lineup_attack_r39l.py')
    feat=rl.load_feature_module()
    return core,rl,feat

def sha_file(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''):h.update(b)
    return h.hexdigest()
def sha_text(s):return hashlib.sha256(s.encode()).hexdigest()
def git_blob(path):return subprocess.check_output(['git','rev-parse',f'HEAD:{path}'],text=True).strip()

def load_snapshot(path,binding,feat):
    snap=binding['frozen_snapshot'];p=Path(path)
    if sha_file(p)!=snap['snapshot_csv_sha256']:raise RuntimeError('snapshot CSV sha256 mismatch')
    static=[];dynamic={90:[],180:[],365:[]}
    with p.open('r',encoding='utf-8',newline='') as f:
        rd=csv.DictReader(f);required={'identity','season','div','date','qH','qD','qA'}|{f's{i}' for i in range(10)}|{f'd{h}_{i}' for h in (90,180,365) for i in range(10)}
        if not required<=set(rd.fieldnames or []):raise RuntimeError('snapshot schema mismatch')
        for z in rd:
            meta={'identity':z['identity'],'season':z['season'],'div':z['div'],'date':dt.strptime(z['date'],'%Y-%m-%d').date(),'q':np.asarray([float(z['qH']),float(z['qD']),float(z['qA'])],dtype=float)}
            sr={**meta,'x':[float(z[f's{i}']) for i in range(10)]};static.append(sr)
            for h in (90,180,365):dynamic[h].append({**meta,'x':[float(z[f'd{h}_{i}']) for i in range(10)]})
    static.sort(key=lambda r:(r['date'],r['div'],r['identity']))
    for h in dynamic:dynamic[h].sort(key=lambda r:(r['date'],r['div'],r['identity']))
    ids=[r['identity'] for r in static]
    if len(ids)!=9434 or len(set(ids))!=9434:raise RuntimeError('snapshot row identity count mismatch')
    if any([r['identity'] for r in dynamic[h]]!=ids for h in dynamic):raise RuntimeError('snapshot dynamic alignment mismatch')
    pre=[r for r in static if r['season']!='2526'];hold=[r for r in static if r['season']=='2526']
    fixed=sorted(hold,key=lambda r:feat.htxt(f"51145|{r['identity']}"))[:100];fixedsha=feat.set_sha([r['identity'] for r in fixed])
    if (len(pre),len(hold),len(fixed))!=(8161,1273,100):raise RuntimeError('snapshot partition count mismatch')
    if fixedsha!=snap['fixed100_identity_sha256']:raise RuntimeError('snapshot fixed100 mismatch')
    return static,dynamic,fixed,fixedsha

def model_public(rl,m):return rl.model_public(m)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--prereg',type=Path,required=True);ap.add_argument('--contract',type=Path,required=True);ap.add_argument('--snapshot-binding',type=Path,required=True);ap.add_argument('--feature-snapshot',type=Path,required=True);ap.add_argument('--raw-dir',type=Path,required=True);ap.add_argument('--out-dir',type=Path,required=True);ap.add_argument('--self-test',action='store_true');a=ap.parse_args()
    core,rl,feat=core_modules()
    if a.self_test:
        core.self_test();print('PASS_R39M_SNAPSHOT_EVALUATOR_SELF_TEST');return
    pre=json.loads(a.prereg.read_text());contract=json.loads(a.contract.read_text());binding=json.loads(a.snapshot_binding.read_text());outdir=a.out_dir;outdir.mkdir(parents=True,exist_ok=True)
    assert binding['status']=='FROZEN_ZERO_LABEL_FEATURE_SNAPSHOT_BOUND_FOR_EVALUATION'
    assert binding['scientific_design_changed'] is False and binding['sample_changed'] is False and binding['gates_changed'] is False
    assert binding['source_drift_audit']['first_evaluation_preholdout_labels_loaded']==0 and binding['source_drift_audit']['first_evaluation_fixed100_labels_loaded']==0
    assert binding['frozen_snapshot']['zero_label_contract']['target_result_labels_accessed']==0
    static,dynamic,fixed,fixedsha=load_snapshot(a.feature_snapshot,binding,feat)
    pre_static=[r for r in static if r['season']!='2526'];hold_static=[r for r in static if r['season']=='2526']
    feature_freeze={'feature_freeze_completed':True,'frozen_at_utc':datetime.now(timezone.utc).isoformat(),'input_mode':'hash_locked_zero_label_feature_snapshot','snapshot_artifact_id':binding['frozen_snapshot']['artifact_id'],'snapshot_artifact_sha256':binding['frozen_snapshot']['artifact_sha256'],'snapshot_csv_sha256':binding['frozen_snapshot']['snapshot_csv_sha256'],'preholdout_rows':8161,'holdout_pool_rows':1273,'fixed100_rows':100,'fixed100_identity_sha256':fixedsha,'target_labels_accessed_before_feature_freeze':0,'scientific_blobs':{'prereg':git_blob('football-data/research/lineup_attack_recency_r39m/preregistration_r39m.json'),'contract':git_blob('football-data/research/lineup_attack_recency_r39m/evaluator_contract_r39m.json'),'original_evaluator':git_blob('football-data/research/lineup_attack_recency_r39m/evaluate_recency_r39m.py'),'snapshot_binding':git_blob('football-data/research/lineup_attack_recency_r39m/feature_snapshot_binding_r39m.json'),'snapshot_evaluator':git_blob('football-data/research/lineup_attack_recency_r39m/evaluate_recency_snapshot_r39m.py')}}
    (outdir/'feature_freeze_receipt_r39m.json').write_text(json.dumps(feature_freeze,indent=2))
    labels=rl.load_labels(a.raw_dir,{'1920','2021','2122','2223','2324','2425'},feat,{r['identity'] for r in pre_static})
    context_l2=float(pre['model_contract']['market_context_benchmark_l2']);static_l2=float(pre['model_contract']['static_R39L_benchmark_l2']);l2s=[float(x) for x in pre['model_contract']['dynamic_full_l2_candidates']];halves=[int(x) for x in pre['recency_weighted_player_attack_rate']['half_life_days_candidates']]
    fold_bench={};candidate_streams={(h,l2):[] for h in halves for l2 in l2s};candidate_folds={(h,l2):{} for h in halves for l2 in l2s}
    for fold in pre['rolling_development']['folds']:
        name=fold['name'];train_s=fold['train_seasons'];test_s=[fold['test_season']];train_static=core.subset(pre_static,train_s);test_static=core.subset(pre_static,test_s);test_ids=[r['identity'] for r in test_static]
        cm=rl.fit_offset_logistic(train_static,labels,2,context_l2,contract);sm=rl.fit_offset_logistic(train_static,labels,10,static_l2,contract);market=rl.market_pred(test_static);cp=rl.predict(test_static,cm,contract);sp=rl.predict(test_static,sm,contract)
        fold_bench[name]={'rows':test_static,'market':market,'context':cp,'static':sp,'context_model':model_public(rl,cm),'static_model':model_public(rl,sm)}
        for h in halves:
            train_dyn=core.subset([r for r in dynamic[h] if r['season']!='2526'],train_s);test_dyn=core.by_ids(dynamic[h],test_ids)
            for l2 in l2s:
                dm=rl.fit_offset_logistic(train_dyn,labels,10,l2,contract);dp=rl.predict(test_dyn,dm,contract);candidate_folds[(h,l2)][name]={'rows':test_dyn,'pred':dp,'model':model_public(rl,dm)};candidate_streams[(h,l2)].append((test_dyn,dp))
    board=[]
    for (h,l2),streams in candidate_streams.items():
        pred={};rows=[]
        for rr,pp in streams:rows.extend(rr);pred.update(pp)
        board.append({'half_life_days':h,'l2':l2,'pooled_metrics':rl.metric_pack(rows,pred,labels)})
    selected=sorted(board,key=lambda z:(z['pooled_metrics']['HDA_LogLoss'],z['pooled_metrics']['binary_Draw_LogLoss'],-z['half_life_days'],-z['l2']))[0];sel=(selected['half_life_days'],selected['l2']);rolling={};rolling_pass=True
    for fold in pre['rolling_development']['folds']:
        name=fold['name'];b=fold_bench[name];c=candidate_folds[sel][name];g=core.gate(rl,c['rows'],b['market'],b['context'],b['static'],c['pred'],labels,pre['rolling_development']['each_fold_gate']);g['dynamic_model']=c['model'];g['context_model']=b['context_model'];g['static_model']=b['static_model'];rolling[name]=g;rolling_pass=rolling_pass and g['gate_pass']
    base={'schema_version':pre['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'execution_input':'frozen_zero_label_feature_snapshot','source_counts':{'preholdout':8161,'holdout_pool':1273,'fixed100':100},'fixed100_identity_sha256':fixedsha,'feature_freeze_receipt':feature_freeze,'candidate_board':board,'selected_dynamic':{'half_life_days':sel[0],'l2':sel[1]},'rolling_development':rolling,'rolling_gate_pass':bool(rolling_pass),'holdout_labels_accessed':0,'hard_limits':pre['hard_limits']}
    if not rolling_pass:
        core.write_stop(outdir,base,pre['rolling_development']['if_fail'],{'rolling_pass':False,'selected_dynamic':base['selected_dynamic'],'fixed100_identity_sha256':fixedsha});return
    train_s=pre['policy_stress']['train_seasons'];test_s=[pre['policy_stress']['test_season']];tr_s=core.subset(pre_static,train_s);te_s=core.subset(pre_static,test_s);te_ids=[r['identity'] for r in te_s];tr_d=core.subset([r for r in dynamic[sel[0]] if r['season']!='2526'],train_s);te_d=core.by_ids(dynamic[sel[0]],te_ids)
    cm=rl.fit_offset_logistic(tr_s,labels,2,context_l2,contract);sm=rl.fit_offset_logistic(tr_s,labels,10,static_l2,contract);dm=rl.fit_offset_logistic(tr_d,labels,10,sel[1],contract);market=rl.market_pred(te_s);cp=rl.predict(te_s,cm,contract);sp=rl.predict(te_s,sm,contract);dp=rl.predict(te_d,dm,contract);pg=core.gate(rl,te_d,market,cp,sp,dp,labels,pre['policy_stress']['gate']);base['policy']=pg
    if not pg['gate_pass']:
        core.write_stop(outdir,base,pre['policy_stress']['if_fail'],{'rolling_pass':True,'policy_pass':False,'selected_dynamic':base['selected_dynamic'],'fixed100_identity_sha256':fixedsha});return
    cmf=rl.fit_offset_logistic(pre_static,labels,2,context_l2,contract);smf=rl.fit_offset_logistic(pre_static,labels,10,static_l2,contract);all_pre_d=[r for r in dynamic[sel[0]] if r['season']!='2526'];dmf=rl.fit_offset_logistic(all_pre_d,labels,10,sel[1],contract)
    final_freeze={'final_freeze_completed':True,'frozen_at_utc':datetime.now(timezone.utc).isoformat(),'rolling_pass':True,'policy_pass':True,'selected_dynamic':base['selected_dynamic'],'context_model':model_public(rl,cmf),'static_R39L_model':model_public(rl,smf),'dynamic_model':model_public(rl,dmf),'fixed100_identity_sha256':fixedsha,'holdout_labels_accessed_before_freeze':0,'snapshot_artifact_id':binding['frozen_snapshot']['artifact_id'],'snapshot_csv_sha256':binding['frozen_snapshot']['snapshot_csv_sha256'],'scientific_blobs':feature_freeze['scientific_blobs']};(outdir/'final_freeze_receipt_r39m.json').write_text(json.dumps(final_freeze,indent=2))
    fixed_s=core.by_ids(hold_static,[r['identity'] for r in fixed]);fixed_d=core.by_ids(dynamic[sel[0]],[r['identity'] for r in fixed]);hlab=rl.load_labels(a.raw_dir,{'2526'},feat,{r['identity'] for r in fixed});market=rl.market_pred(fixed_s);cp=rl.predict(fixed_s,cmf,contract);sp=rl.predict(fixed_s,smf,contract);dp=rl.predict(fixed_d,dmf,contract);hg=core.blind_gate(rl,fixed_d,market,cp,sp,dp,hlab);base['holdout']=hg;base['holdout_labels_accessed']=100;base['final_freeze_receipt']=final_freeze;base['status']=pre['blind_confirmation']['pass_status'] if hg['gate_pass'] else pre['blind_confirmation']['fail_status'];(outdir/'r39m_result.json').write_text(json.dumps(base,indent=2));print(json.dumps({'status':base['status'],'selected_dynamic':base['selected_dynamic'],'rolling_development':rolling,'policy':pg,'holdout':hg,'holdout_labels_accessed':100},indent=2))
if __name__=='__main__':main()
