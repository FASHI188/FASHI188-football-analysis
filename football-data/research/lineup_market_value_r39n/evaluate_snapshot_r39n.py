#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,hashlib,importlib.util,json,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
import numpy as np

HERE=Path(__file__).resolve().parent
R39L=HERE.parent/'lineup_attack_r39l'

def load_module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def mods():
    rl=load_module('r39l_eval',R39L/'evaluate_lineup_attack_r39l.py');feat=rl.load_feature_module();return rl,feat

def sha_file(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''):h.update(b)
    return h.hexdigest()
def git_blob(path):return subprocess.check_output(['git','rev-parse',f'HEAD:{path}'],text=True).strip()
def parse_date(s):return datetime.strptime(str(s),'%Y-%m-%d').date()

def load_r39n(path,binding):
    s=binding['frozen_snapshot'];p=Path(path)
    if sha_file(p)!=s['snapshot_csv_sha256']:raise RuntimeError('R39N snapshot CSV sha mismatch')
    rows=[]
    with p.open('r',encoding='utf-8',newline='') as f:
        rd=csv.DictReader(f);need={'identity','season','div','date','qH','qD','qA'}|{f'x{i}' for i in range(14)}
        if not need<=set(rd.fieldnames or []):raise RuntimeError('R39N snapshot schema mismatch')
        for z in rd:rows.append({'identity':z['identity'],'season':z['season'],'div':z['div'],'date':parse_date(z['date']),'q':np.asarray([float(z['qH']),float(z['qD']),float(z['qA'])]),'x':[float(z[f'x{i}']) for i in range(14)]})
    rows.sort(key=lambda r:(r['date'],r['div'],r['identity']))
    if len(rows)!=s['feature_eligible']['all'] or len({r['identity'] for r in rows})!=len(rows):raise RuntimeError('R39N snapshot identity count mismatch')
    return rows

def load_r39l_static(path,binding):
    b=binding['frozen_static_R39L_benchmark_snapshot'];p=Path(path)
    if sha_file(p)!=b['snapshot_csv_sha256']:raise RuntimeError('R39L benchmark snapshot CSV sha mismatch')
    out={}
    with p.open('r',encoding='utf-8',newline='') as f:
        rd=csv.DictReader(f);need={'identity'}|{f's{i}' for i in range(10)}
        if not need<=set(rd.fieldnames or []):raise RuntimeError('R39L benchmark snapshot schema mismatch')
        for z in rd:out[z['identity']]=[float(z[f's{i}']) for i in range(10)]
    return out

def add_static(rows,static):
    out=[]
    for r in rows:
        if r['identity'] not in static:raise RuntimeError(f'missing R39L benchmark feature {r["identity"]}')
        out.append({**r,'sx':static[r['identity']]})
    return out

def sub(rows,seasons):
    s=set(seasons);return [r for r in rows if r['season'] in s]
def by_ids(rows,ids):
    ids=set(ids);return [r for r in rows if r['identity'] in ids]
def to_model_rows(rows,key,dims):
    return [{'identity':r['identity'],'season':r['season'],'div':r['div'],'date':r['date'],'q':r['q'],'x':list(r[key][:dims])} for r in rows]

def gate(rl,rows,market,context,static,cand,labels,cfg):
    mm=rl.metric_pack(rows,market,labels);cm=rl.metric_pack(rows,context,labels);sm=rl.metric_pack(rows,static,labels);fm=rl.metric_pack(rows,cand,labels);wins,detail=rl.per_div_wins(rows,cand,static,labels)
    ok=(fm['HDA_LogLoss']<mm['HDA_LogLoss'] and fm['binary_Draw_LogLoss']<mm['binary_Draw_LogLoss'] and fm['HDA_Brier']<=mm['HDA_Brier'] and fm['RPS']<=mm['RPS'] and fm['HDA_LogLoss']<cm['HDA_LogLoss'] and fm['binary_Draw_LogLoss']<cm['binary_Draw_LogLoss'] and fm['HDA_LogLoss']<sm['HDA_LogLoss'] and fm['binary_Draw_LogLoss']<sm['binary_Draw_LogLoss'] and fm['Draw_AUC'] is not None and sm['Draw_AUC'] is not None and fm['Draw_AUC']>sm['Draw_AUC'] and wins>=int(cfg['R39N_division_HDA_LogLoss_wins_vs_R39L_static_min']))
    return {'market':mm,'market_context':cm,'static_R39L':sm,'R39N':fm,'division_wins_vs_R39L_static':wins,'division_detail_vs_R39L_static':detail,'gate_pass':bool(ok)}

def blind_gate(rl,rows,market,context,static,cand,labels):
    mm=rl.metric_pack(rows,market,labels);cm=rl.metric_pack(rows,context,labels);sm=rl.metric_pack(rows,static,labels);fm=rl.metric_pack(rows,cand,labels)
    ok=(fm['HDA_LogLoss']<mm['HDA_LogLoss'] and fm['binary_Draw_LogLoss']<mm['binary_Draw_LogLoss'] and fm['HDA_Brier']<=mm['HDA_Brier'] and fm['RPS']<=mm['RPS'] and fm['HDA_LogLoss']<cm['HDA_LogLoss'] and fm['binary_Draw_LogLoss']<cm['binary_Draw_LogLoss'] and fm['HDA_LogLoss']<sm['HDA_LogLoss'] and fm['binary_Draw_LogLoss']<sm['binary_Draw_LogLoss'] and fm['Draw_AUC'] is not None and sm['Draw_AUC'] is not None and fm['Draw_AUC']>sm['Draw_AUC'])
    return {'market':mm,'market_context':cm,'static_R39L':sm,'R39N':fm,'gate_pass':bool(ok)}
def pub(rl,m):return rl.model_public(m)
def write_stop(outdir,base,status,extra):
    base['status']=status;base['holdout_labels_accessed']=0
    (outdir/'final_freeze_receipt_r39n.json').write_text(json.dumps({'final_freeze_completed':False,'holdout_labels_accessed_before_freeze':0,**extra},indent=2));(outdir/'r39n_result.json').write_text(json.dumps(base,indent=2));print(json.dumps({'status':status,'holdout_labels_accessed':0},indent=2))

def self_test():
    rl,_=mods();c={'binary_residual_model':{'newton_max_iterations':50,'newton_parameter_tolerance':1e-10,'sigmoid_linear_predictor_clip':[-35,35]}}
    rows=[];labels={}
    for i in range(80):
        ident=f'i{i}';rows.append({'identity':ident,'q':np.asarray([.39,.27,.34]),'x':[float((i+j)%7)/7 for j in range(14)],'div':'E0','date':i,'season':'x'});labels[ident]=1 if i%4==0 else (0 if i%2==0 else 2)
    m=rl.fit_offset_logistic(rows,labels,14,10.0,c);p=rl.predict(rows,m,c);assert m['converged'];assert max(abs(float(v.sum())-1) for v in p.values())<1e-12;print('PASS_R39N_EVALUATOR_SELF_TEST')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--prereg',type=Path);ap.add_argument('--contract',type=Path);ap.add_argument('--snapshot-binding',type=Path);ap.add_argument('--r39n-snapshot',type=Path);ap.add_argument('--r39l-snapshot',type=Path);ap.add_argument('--raw-dir',type=Path);ap.add_argument('--out-dir',type=Path);ap.add_argument('--self-test',action='store_true');a=ap.parse_args()
    if a.self_test:self_test();return
    rl,feat=mods();pre=json.loads(a.prereg.read_text());contract=json.loads(a.contract.read_text());binding=json.loads(a.snapshot_binding.read_text());outdir=a.out_dir;outdir.mkdir(parents=True,exist_ok=True)
    assert binding['status']=='FROZEN_ZERO_LABEL_VALUATION_SNAPSHOT_BOUND_FOR_EVALUATION';assert binding['scientific_design_changed'] is False and binding['sample_changed'] is False and binding['gates_changed'] is False
    rows=add_static(load_r39n(a.r39n_snapshot,binding),load_r39l_static(a.r39l_snapshot,binding));pre_rows=[r for r in rows if r['season']!='2526'];hold=[r for r in rows if r['season']=='2526']
    if (len(pre_rows),len(hold))!=(8161,1273):raise RuntimeError(f'eligible partition drift {len(pre_rows)} {len(hold)}')
    fixed=sorted(hold,key=lambda r:feat.htxt(f"51145|{r['identity']}"))[:100];fixedsha=feat.set_sha([r['identity'] for r in fixed]);assert fixedsha==pre['blind_binding']['fixed100_identity_sha256']
    feature_freeze={'feature_freeze_completed':True,'frozen_at_utc':datetime.now(timezone.utc).isoformat(),'input_mode':'hash_locked_R39N_plus_R39L_benchmark_snapshots','R39N_snapshot_artifact_id':binding['frozen_snapshot']['artifact_id'],'R39N_snapshot_csv_sha256':binding['frozen_snapshot']['snapshot_csv_sha256'],'R39L_snapshot_artifact_id':binding['frozen_static_R39L_benchmark_snapshot']['artifact_id'],'R39L_snapshot_csv_sha256':binding['frozen_static_R39L_benchmark_snapshot']['snapshot_csv_sha256'],'preholdout_rows':len(pre_rows),'holdout_pool_rows':len(hold),'fixed100_rows':100,'fixed100_identity_sha256':fixedsha,'target_labels_accessed_before_feature_freeze':0,'scientific_blobs':{'prereg':git_blob('football-data/research/lineup_market_value_r39n/preregistration_r39n.json'),'contract':git_blob('football-data/research/lineup_market_value_r39n/evaluator_contract_r39n.json'),'binding':git_blob('football-data/research/lineup_market_value_r39n/feature_snapshot_binding_r39n.json'),'evaluator':git_blob('football-data/research/lineup_market_value_r39n/evaluate_snapshot_r39n.py')}};(outdir/'feature_freeze_receipt_r39n.json').write_text(json.dumps(feature_freeze,indent=2))
    labels=rl.load_labels(a.raw_dir,{'1920','2021','2122','2223','2324','2425'},feat,{r['identity'] for r in pre_rows})
    context_l2=float(contract['benchmarks']['market_context']['L2']);static_l2=float(contract['benchmarks']['static_R39L']['L2']);l2s=[float(x) for x in contract['candidate']['L2_candidates']];folds=contract['rolling_folds'];streams={l2:[] for l2 in l2s};fold_data={l2:{} for l2 in l2s};bench={}
    for fold in folds:
        name=fold['name'];tr=sub(pre_rows,fold['train_seasons']);te=sub(pre_rows,[fold['test_season']]);trc=to_model_rows(tr,'x',2);tec=to_model_rows(te,'x',2);trs=to_model_rows(tr,'sx',10);tes=to_model_rows(te,'sx',10);trn=to_model_rows(tr,'x',14);ten=to_model_rows(te,'x',14)
        cm=rl.fit_offset_logistic(trc,labels,2,context_l2,contract);sm=rl.fit_offset_logistic(trs,labels,10,static_l2,contract);market=rl.market_pred(te);cp=rl.predict(tec,cm,contract);sp=rl.predict(tes,sm,contract);bench[name]={'rows':te,'market':market,'context':cp,'static':sp,'context_model':pub(rl,cm),'static_model':pub(rl,sm)}
        for l2 in l2s:
            nm=rl.fit_offset_logistic(trn,labels,14,l2,contract);npred=rl.predict(ten,nm,contract);fold_data[l2][name]={'rows':te,'pred':npred,'model':pub(rl,nm)};streams[l2].append((te,npred))
    board=[]
    for l2,parts in streams.items():
        rr=[];pp={}
        for r,p in parts:rr.extend(r);pp.update(p)
        board.append({'l2':l2,'pooled_metrics':rl.metric_pack(rr,pp,labels)})
    selected=sorted(board,key=lambda z:(z['pooled_metrics']['HDA_LogLoss'],z['pooled_metrics']['binary_Draw_LogLoss'],-z['l2']))[0];sel=selected['l2'];rolling={};rpass=True
    for fold in folds:
        name=fold['name'];b=bench[name];c=fold_data[sel][name];g=gate(rl,c['rows'],b['market'],b['context'],b['static'],c['pred'],labels,pre['rolling_design']['each_fold_gate']);g['R39N_model']=c['model'];g['context_model']=b['context_model'];g['static_R39L_model']=b['static_model'];rolling[name]=g;rpass=rpass and g['gate_pass']
    base={'schema_version':pre['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'source_counts':{'preholdout':len(pre_rows),'holdout_pool':len(hold),'fixed100':100},'fixed100_identity_sha256':fixedsha,'feature_freeze_receipt':feature_freeze,'candidate_board':board,'selected_L2':sel,'rolling_development':rolling,'rolling_gate_pass':bool(rpass),'holdout_labels_accessed':0,'hard_limits':pre['hard_limits']}
    if not rpass:write_stop(outdir,base,pre['rolling_design']['if_fail'],{'rolling_pass':False,'selected_L2':sel,'fixed100_identity_sha256':fixedsha});return
    tr=sub(pre_rows,contract['policy']['train_seasons']);te=sub(pre_rows,[contract['policy']['test_season']]);cm=rl.fit_offset_logistic(to_model_rows(tr,'x',2),labels,2,context_l2,contract);sm=rl.fit_offset_logistic(to_model_rows(tr,'sx',10),labels,10,static_l2,contract);nm=rl.fit_offset_logistic(to_model_rows(tr,'x',14),labels,14,sel,contract);pg=gate(rl,te,rl.market_pred(te),rl.predict(to_model_rows(te,'x',2),cm,contract),rl.predict(to_model_rows(te,'sx',10),sm,contract),rl.predict(to_model_rows(te,'x',14),nm,contract),labels,pre['rolling_design']['each_fold_gate']);base['policy']=pg
    if not pg['gate_pass']:write_stop(outdir,base,pre['policy_design']['if_fail'],{'rolling_pass':True,'policy_pass':False,'selected_L2':sel,'fixed100_identity_sha256':fixedsha});return
    cmf=rl.fit_offset_logistic(to_model_rows(pre_rows,'x',2),labels,2,context_l2,contract);smf=rl.fit_offset_logistic(to_model_rows(pre_rows,'sx',10),labels,10,static_l2,contract);nmf=rl.fit_offset_logistic(to_model_rows(pre_rows,'x',14),labels,14,sel,contract);final={'final_freeze_completed':True,'frozen_at_utc':datetime.now(timezone.utc).isoformat(),'rolling_pass':True,'policy_pass':True,'selected_L2':sel,'context_model':pub(rl,cmf),'static_R39L_model':pub(rl,smf),'R39N_model':pub(rl,nmf),'fixed100_identity_sha256':fixedsha,'holdout_labels_accessed_before_freeze':0,'scientific_blobs':feature_freeze['scientific_blobs']};(outdir/'final_freeze_receipt_r39n.json').write_text(json.dumps(final,indent=2))
    hlabels=rl.load_labels(a.raw_dir,{'2526'},feat,{r['identity'] for r in fixed});hg=blind_gate(rl,fixed,rl.market_pred(fixed),rl.predict(to_model_rows(fixed,'x',2),cmf,contract),rl.predict(to_model_rows(fixed,'sx',10),smf,contract),rl.predict(to_model_rows(fixed,'x',14),nmf,contract),hlabels);base['holdout']=hg;base['holdout_labels_accessed']=100;base['final_freeze_receipt']=final;base['status']=contract['blind']['pass_status'] if hg['gate_pass'] else contract['blind']['fail_status'];(outdir/'r39n_result.json').write_text(json.dumps(base,indent=2));print(json.dumps({'status':base['status'],'selected_L2':sel,'rolling_development':rolling,'policy':pg,'holdout':hg,'holdout_labels_accessed':100},indent=2))
if __name__=='__main__':main()
