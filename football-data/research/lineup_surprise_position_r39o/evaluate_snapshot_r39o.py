#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,hashlib,importlib.util,json,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
import numpy as np

HERE=Path(__file__).resolve().parent
R39N=HERE.parent/'lineup_market_value_r39n'

def load_module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def modules():
    rn=load_module('r39n_eval',R39N/'evaluate_snapshot_r39n.py');rl,feat=rn.mods();return rn,rl,feat

def sha_file(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''):h.update(b)
    return h.hexdigest()
def git_blob(path):return subprocess.check_output(['git','rev-parse',f'HEAD:{path}'],text=True).strip()
def parse_date(s):return datetime.strptime(str(s),'%Y-%m-%d').date()

def load_r39o(path,binding):
    b=binding['frozen_R39O_snapshot'];p=Path(path)
    if sha_file(p)!=b['snapshot_csv_sha256']:raise RuntimeError('R39O snapshot CSV sha mismatch')
    rows=[]
    with p.open('r',encoding='utf-8',newline='') as f:
        rd=csv.DictReader(f);need={'identity','season','div','date','qH','qD','qA'}|{f'x{i}' for i in range(14)}
        if not need<=set(rd.fieldnames or []):raise RuntimeError('R39O snapshot schema mismatch')
        for z in rd:
            rows.append({'identity':z['identity'],'season':z['season'],'div':z['div'],'date':parse_date(z['date']),'q':np.asarray([float(z['qH']),float(z['qD']),float(z['qA'])]),'ox':[float(z[f'x{i}']) for i in range(14)]})
    rows.sort(key=lambda r:(r['date'],r['div'],r['identity']))
    if len(rows)!=b['rows']['all'] or len({r['identity'] for r in rows})!=len(rows):raise RuntimeError('R39O snapshot identity count mismatch')
    return rows

def load_r39n_benchmark(path,binding):
    b=binding['frozen_R39N_absolute_value_benchmark'];p=Path(path)
    if sha_file(p)!=b['snapshot_csv_sha256']:raise RuntimeError('R39N benchmark CSV sha mismatch')
    out={}
    with p.open('r',encoding='utf-8',newline='') as f:
        rd=csv.DictReader(f);need={'identity','qH','qD','qA'}|{f'x{i}' for i in range(14)}
        if not need<=set(rd.fieldnames or []):raise RuntimeError('R39N benchmark schema mismatch')
        for z in rd:out[z['identity']]={'q':np.asarray([float(z['qH']),float(z['qD']),float(z['qA'])]),'nx':[float(z[f'x{i}']) for i in range(14)]}
    return out

def load_r39l_benchmark(path,binding):
    b=binding['frozen_R39L_static_attack_benchmark'];p=Path(path)
    if sha_file(p)!=b['snapshot_csv_sha256']:raise RuntimeError('R39L benchmark CSV sha mismatch')
    out={}
    with p.open('r',encoding='utf-8',newline='') as f:
        rd=csv.DictReader(f);need={'identity'}|{f's{i}' for i in range(10)}
        if not need<=set(rd.fieldnames or []):raise RuntimeError('R39L benchmark schema mismatch')
        for z in rd:out[z['identity']]=[float(z[f's{i}']) for i in range(10)]
    return out

def join_benchmarks(rows,nmap,smap):
    out=[]
    for r in rows:
        ident=r['identity']
        if ident not in nmap or ident not in smap:raise RuntimeError(f'missing benchmark feature {ident}')
        if np.max(np.abs(np.asarray(r['q'])-np.asarray(nmap[ident]['q'])))>1e-12:raise RuntimeError(f'closing-market mismatch {ident}')
        out.append({**r,'nx':nmap[ident]['nx'],'sx':smap[ident]})
    return out

def sub(rows,seasons):
    s=set(seasons);return [r for r in rows if r['season'] in s]
def to_model_rows(rows,key,dims):
    return [{'identity':r['identity'],'season':r['season'],'div':r['div'],'date':r['date'],'q':r['q'],'x':list(r[key][:dims])} for r in rows]
def pub(rl,m):return rl.model_public(m)

def gate(rl,rows,market,context,static,absolute,cand,labels,cfg):
    mm=rl.metric_pack(rows,market,labels);cm=rl.metric_pack(rows,context,labels);sm=rl.metric_pack(rows,static,labels);nm=rl.metric_pack(rows,absolute,labels);om=rl.metric_pack(rows,cand,labels)
    wins,detail=rl.per_div_wins(rows,cand,absolute,labels)
    ok=(om['HDA_LogLoss']<mm['HDA_LogLoss'] and om['binary_Draw_LogLoss']<mm['binary_Draw_LogLoss'] and om['HDA_Brier']<=mm['HDA_Brier'] and om['RPS']<=mm['RPS'] and om['HDA_LogLoss']<cm['HDA_LogLoss'] and om['binary_Draw_LogLoss']<cm['binary_Draw_LogLoss'] and om['HDA_LogLoss']<sm['HDA_LogLoss'] and om['binary_Draw_LogLoss']<sm['binary_Draw_LogLoss'] and om['HDA_LogLoss']<nm['HDA_LogLoss'] and om['binary_Draw_LogLoss']<nm['binary_Draw_LogLoss'] and om['Draw_AUC'] is not None and nm['Draw_AUC'] is not None and om['Draw_AUC']>nm['Draw_AUC'] and wins>=int(cfg['R39O_division_HDA_LogLoss_wins_vs_R39N_min']))
    return {'market':mm,'market_context':cm,'static_R39L':sm,'absolute_R39N':nm,'R39O':om,'division_wins_vs_R39N':wins,'division_detail_vs_R39N':detail,'gate_pass':bool(ok)}

def blind_gate(rl,rows,market,context,static,absolute,cand,labels):
    mm=rl.metric_pack(rows,market,labels);cm=rl.metric_pack(rows,context,labels);sm=rl.metric_pack(rows,static,labels);nm=rl.metric_pack(rows,absolute,labels);om=rl.metric_pack(rows,cand,labels)
    ok=(om['HDA_LogLoss']<mm['HDA_LogLoss'] and om['binary_Draw_LogLoss']<mm['binary_Draw_LogLoss'] and om['HDA_Brier']<=mm['HDA_Brier'] and om['RPS']<=mm['RPS'] and om['HDA_LogLoss']<cm['HDA_LogLoss'] and om['binary_Draw_LogLoss']<cm['binary_Draw_LogLoss'] and om['HDA_LogLoss']<sm['HDA_LogLoss'] and om['binary_Draw_LogLoss']<sm['binary_Draw_LogLoss'] and om['HDA_LogLoss']<nm['HDA_LogLoss'] and om['binary_Draw_LogLoss']<nm['binary_Draw_LogLoss'] and om['Draw_AUC'] is not None and nm['Draw_AUC'] is not None and om['Draw_AUC']>nm['Draw_AUC'])
    return {'market':mm,'market_context':cm,'static_R39L':sm,'absolute_R39N':nm,'R39O':om,'gate_pass':bool(ok)}

def write_stop(outdir,base,status,extra):
    base['status']=status;base['holdout_labels_accessed']=0
    (outdir/'final_freeze_receipt_r39o.json').write_text(json.dumps({'final_freeze_completed':False,'holdout_labels_accessed_before_freeze':0,**extra},indent=2))
    (outdir/'r39o_result.json').write_text(json.dumps(base,indent=2));print(json.dumps({'status':status,'holdout_labels_accessed':0},indent=2))

def self_test():
    _rn,rl,_feat=modules();c={'binary_residual_model':{'newton_max_iterations':50,'newton_parameter_tolerance':1e-10,'sigmoid_linear_predictor_clip':[-35,35]}}
    rows=[];labels={}
    for i in range(100):
        ident=f'i{i}';rows.append({'identity':ident,'q':np.asarray([.39,.27,.34]),'x':[float((i+j)%11)/11 for j in range(14)],'div':['E0','D1','I1','SP1','F1'][i%5],'date':i,'season':'x'});labels[ident]=1 if i%4==0 else (0 if i%2==0 else 2)
    m=rl.fit_offset_logistic(rows,labels,14,10.0,c);p=rl.predict(rows,m,c);assert m['converged'];assert max(abs(float(v.sum())-1.0) for v in p.values())<1e-12;print('PASS_R39O_EVALUATOR_SELF_TEST')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--prereg',type=Path);ap.add_argument('--contract',type=Path);ap.add_argument('--binding',type=Path);ap.add_argument('--r39o-snapshot',type=Path);ap.add_argument('--r39n-snapshot',type=Path);ap.add_argument('--r39l-snapshot',type=Path);ap.add_argument('--raw-dir',type=Path);ap.add_argument('--out-dir',type=Path);ap.add_argument('--self-test',action='store_true');a=ap.parse_args()
    if a.self_test:self_test();return
    rn,rl,feat=modules();pre=json.loads(a.prereg.read_text());contract=json.loads(a.contract.read_text());binding=json.loads(a.binding.read_text());outdir=a.out_dir;outdir.mkdir(parents=True,exist_ok=True)
    assert binding['status']=='FROZEN_ZERO_LABEL_R39O_SNAPSHOT_BOUND_FOR_EVALUATION';assert binding['scientific_design_changed_after_snapshot'] is False and binding['sample_reselected'] is False and binding['gates_changed_after_snapshot'] is False
    rows=join_benchmarks(load_r39o(a.r39o_snapshot,binding),load_r39n_benchmark(a.r39n_snapshot,binding),load_r39l_benchmark(a.r39l_snapshot,binding))
    pre_rows=[r for r in rows if r['season']!='2526'];hold=[r for r in rows if r['season']=='2526']
    if (len(pre_rows),len(hold))!=(8145,1267):raise RuntimeError(f'R39O partition drift {len(pre_rows)} {len(hold)}')
    fixed=sorted(hold,key=lambda r:feat.htxt(f"51145|{r['identity']}"))[:100];fixedsha=feat.set_sha([r['identity'] for r in fixed]);assert fixedsha==pre['blind_binding']['fixed100_identity_sha256']
    feature_freeze={'feature_freeze_completed':True,'frozen_at_utc':datetime.now(timezone.utc).isoformat(),'input_mode':'three_hash_locked_zero_label_snapshots','R39O_snapshot_artifact_id':binding['frozen_R39O_snapshot']['artifact_id'],'R39O_snapshot_csv_sha256':binding['frozen_R39O_snapshot']['snapshot_csv_sha256'],'R39N_snapshot_artifact_id':binding['frozen_R39N_absolute_value_benchmark']['artifact_id'],'R39N_snapshot_csv_sha256':binding['frozen_R39N_absolute_value_benchmark']['snapshot_csv_sha256'],'R39L_snapshot_artifact_id':binding['frozen_R39L_static_attack_benchmark']['artifact_id'],'R39L_snapshot_csv_sha256':binding['frozen_R39L_static_attack_benchmark']['snapshot_csv_sha256'],'preholdout_rows':len(pre_rows),'holdout_pool_rows':len(hold),'fixed100_rows':100,'fixed100_identity_sha256':fixedsha,'target_labels_accessed_before_feature_freeze':0,'scientific_blobs':{'prereg':git_blob('football-data/research/lineup_surprise_position_r39o/preregistration_r39o.json'),'contract':git_blob('football-data/research/lineup_surprise_position_r39o/evaluator_contract_r39o.json'),'binding':git_blob('football-data/research/lineup_surprise_position_r39o/feature_snapshot_binding_r39o.json'),'evaluator':git_blob('football-data/research/lineup_surprise_position_r39o/evaluate_snapshot_r39o.py')}}
    (outdir/'feature_freeze_receipt_r39o.json').write_text(json.dumps(feature_freeze,indent=2))
    rolling_seasons={'1920','2021','2122','2223','2324'};rolling_rows=[r for r in pre_rows if r['season'] in rolling_seasons]
    labels=rl.load_labels(a.raw_dir,rolling_seasons,feat,{r['identity'] for r in rolling_rows})
    context_l2=float(contract['benchmarks']['market_context']['L2']);static_l2=float(contract['benchmarks']['static_R39L']['L2']);absolute_l2=float(contract['benchmarks']['absolute_R39N']['L2']);l2s=[float(x) for x in contract['candidate']['L2_candidates']];folds=contract['rolling_folds']
    streams={l2:[] for l2 in l2s};fold_data={l2:{} for l2 in l2s};bench={}
    for fold in folds:
        name=fold['name'];tr=sub(pre_rows,fold['train_seasons']);te=sub(pre_rows,[fold['test_season']])
        cm=rl.fit_offset_logistic(to_model_rows(tr,'ox',2),labels,2,context_l2,contract);sm=rl.fit_offset_logistic(to_model_rows(tr,'sx',10),labels,10,static_l2,contract);nm=rl.fit_offset_logistic(to_model_rows(tr,'nx',14),labels,14,absolute_l2,contract)
        market=rl.market_pred(te);cp=rl.predict(to_model_rows(te,'ox',2),cm,contract);sp=rl.predict(to_model_rows(te,'sx',10),sm,contract);npred=rl.predict(to_model_rows(te,'nx',14),nm,contract)
        bench[name]={'rows':te,'market':market,'context':cp,'static':sp,'absolute':npred,'context_model':pub(rl,cm),'static_model':pub(rl,sm),'absolute_model':pub(rl,nm)}
        for l2 in l2s:
            om=rl.fit_offset_logistic(to_model_rows(tr,'ox',14),labels,14,l2,contract);op=rl.predict(to_model_rows(te,'ox',14),om,contract);fold_data[l2][name]={'rows':te,'pred':op,'model':pub(rl,om)};streams[l2].append((te,op))
    board=[]
    for l2,parts in streams.items():
        rr=[];pp={}
        for r,p in parts:rr.extend(r);pp.update(p)
        board.append({'l2':l2,'pooled_metrics':rl.metric_pack(rr,pp,labels)})
    selected=sorted(board,key=lambda z:(z['pooled_metrics']['HDA_LogLoss'],z['pooled_metrics']['binary_Draw_LogLoss'],-z['l2']))[0];sel=selected['l2'];rolling={};rpass=True
    for fold in folds:
        name=fold['name'];b=bench[name];c=fold_data[sel][name];g=gate(rl,c['rows'],b['market'],b['context'],b['static'],b['absolute'],c['pred'],labels,pre['rolling_design']['each_fold_gate']);g['R39O_model']=c['model'];g['context_model']=b['context_model'];g['static_R39L_model']=b['static_model'];g['absolute_R39N_model']=b['absolute_model'];rolling[name]=g;rpass=rpass and g['gate_pass']
    base={'schema_version':pre['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'source_counts':{'preholdout':len(pre_rows),'holdout_pool':len(hold),'fixed100':100},'fixed100_identity_sha256':fixedsha,'feature_freeze_receipt':feature_freeze,'candidate_board':board,'selected_L2':sel,'rolling_development':rolling,'rolling_gate_pass':bool(rpass),'policy_labels_accessed':0,'holdout_labels_accessed':0,'hard_limits':pre['hard_limits']}
    if not rpass:write_stop(outdir,base,pre['rolling_design']['if_fail'],{'rolling_pass':False,'selected_L2':sel,'policy_labels_accessed':0,'fixed100_identity_sha256':fixedsha});return
    policy_rows=sub(pre_rows,['2425']);policy_labels=rl.load_labels(a.raw_dir,{'2425'},feat,{r['identity'] for r in policy_rows});labels_all=dict(labels);labels_all.update(policy_labels);base['policy_labels_accessed']=len(policy_labels)
    tr=sub(pre_rows,contract['policy']['train_seasons']);te=policy_rows
    cm=rl.fit_offset_logistic(to_model_rows(tr,'ox',2),labels_all,2,context_l2,contract);sm=rl.fit_offset_logistic(to_model_rows(tr,'sx',10),labels_all,10,static_l2,contract);nm=rl.fit_offset_logistic(to_model_rows(tr,'nx',14),labels_all,14,absolute_l2,contract);om=rl.fit_offset_logistic(to_model_rows(tr,'ox',14),labels_all,14,sel,contract)
    pg=gate(rl,te,rl.market_pred(te),rl.predict(to_model_rows(te,'ox',2),cm,contract),rl.predict(to_model_rows(te,'sx',10),sm,contract),rl.predict(to_model_rows(te,'nx',14),nm,contract),rl.predict(to_model_rows(te,'ox',14),om,contract),labels_all,pre['rolling_design']['each_fold_gate']);base['policy']=pg
    if not pg['gate_pass']:write_stop(outdir,base,pre['policy_design']['if_fail'],{'rolling_pass':True,'policy_pass':False,'selected_L2':sel,'policy_labels_accessed':len(policy_labels),'fixed100_identity_sha256':fixedsha});return
    cmf=rl.fit_offset_logistic(to_model_rows(pre_rows,'ox',2),labels_all,2,context_l2,contract);smf=rl.fit_offset_logistic(to_model_rows(pre_rows,'sx',10),labels_all,10,static_l2,contract);nmf=rl.fit_offset_logistic(to_model_rows(pre_rows,'nx',14),labels_all,14,absolute_l2,contract);omf=rl.fit_offset_logistic(to_model_rows(pre_rows,'ox',14),labels_all,14,sel,contract)
    final={'final_freeze_completed':True,'frozen_at_utc':datetime.now(timezone.utc).isoformat(),'rolling_pass':True,'policy_pass':True,'selected_L2':sel,'context_model':pub(rl,cmf),'static_R39L_model':pub(rl,smf),'absolute_R39N_model':pub(rl,nmf),'R39O_model':pub(rl,omf),'fixed100_identity_sha256':fixedsha,'holdout_labels_accessed_before_freeze':0,'scientific_blobs':feature_freeze['scientific_blobs']};(outdir/'final_freeze_receipt_r39o.json').write_text(json.dumps(final,indent=2))
    hlabels=rl.load_labels(a.raw_dir,{'2526'},feat,{r['identity'] for r in fixed});hg=blind_gate(rl,fixed,rl.market_pred(fixed),rl.predict(to_model_rows(fixed,'ox',2),cmf,contract),rl.predict(to_model_rows(fixed,'sx',10),smf,contract),rl.predict(to_model_rows(fixed,'nx',14),nmf,contract),rl.predict(to_model_rows(fixed,'ox',14),omf,contract),hlabels)
    base['holdout']=hg;base['holdout_labels_accessed']=100;base['final_freeze_receipt']=final;base['status']=contract['blind']['pass_status'] if hg['gate_pass'] else contract['blind']['fail_status'];(outdir/'r39o_result.json').write_text(json.dumps(base,indent=2));print(json.dumps({'status':base['status'],'selected_L2':sel,'rolling_development':rolling,'policy':pg,'holdout':hg,'holdout_labels_accessed':100},indent=2))
if __name__=='__main__':main()
