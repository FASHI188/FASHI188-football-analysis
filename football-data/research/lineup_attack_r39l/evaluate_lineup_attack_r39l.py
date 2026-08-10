#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,hashlib,importlib.util,json,math,subprocess
from datetime import datetime,timezone
from pathlib import Path
import numpy as np

HERE=Path(__file__).resolve().parent

def load_feature_module():
    p=HERE/'audit_lineup_attack_features_r39l.py'
    spec=importlib.util.spec_from_file_location('r39l_features',p)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def sha_text(s:str)->str:return hashlib.sha256(s.encode()).hexdigest()
def set_sha(ids):return sha_text('\n'.join(sorted(ids))+'\n')
def logit(p):
    p=min(1-1e-12,max(1e-12,float(p)));return math.log(p/(1-p))
def sigmoid(z,clip=(-35.0,35.0)):
    z=np.clip(np.asarray(z,dtype=float),clip[0],clip[1]);return 1.0/(1.0+np.exp(-z))

def git_blob(path):
    return subprocess.check_output(['git','rev-parse',f'HEAD:{path}'],text=True).strip()

def feature_hash(rows):
    lines=[]
    for r in rows:
        vals=','.join(format(float(v),'.17g') for v in r['x'])
        q=','.join(format(float(v),'.17g') for v in r['q'])
        lines.append(f"{r['identity']}|{r['date']}|{r['div']}|{vals}|{q}")
    return sha_text('\n'.join(lines)+'\n')

def load_labels(raw_dir,seasons,feature_module,allowed_ids):
    seasons=set(seasons);allowed=set(allowed_ids);out={}
    for p in sorted(Path(raw_dir).glob('*.csv')):
        season=p.stem.split('_',1)[0]
        if season not in seasons:continue
        with p.open('r',encoding='utf-8-sig',errors='replace',newline='') as f:
            rd=csv.DictReader(f);hdr=set(rd.fieldnames or [])
            need={'Div','Date','Time','HomeTeam','AwayTeam','FTR'}
            if not need<=hdr:continue
            for row in rd:
                rr={'Season':season,**row};ident=feature_module.identity(rr)
                if ident not in allowed:continue
                v=str(row.get('FTR','')).strip().upper()
                if v in {'H','D','A'}:out[ident]={'H':0,'D':1,'A':2}[v]
    if set(out)!=allowed:
        missing=sorted(allowed-set(out))[:5];raise RuntimeError(f'label identity mismatch got={len(out)} expected={len(allowed)} missing={missing}')
    return out

def build_rows(market_dir,games,lineups,appearances,source_reg,r39i_reg):
    f=load_feature_module()
    games_d,starters,complete,mapped,_,_=f.rebuild_mapping(market_dir,games,lineups,r39i_reg)
    pidx,cidx,_,bad=f.load_appearances(appearances,set(source_reg['feature_contract']['eligible_appearance_competitions']))
    if bad!=0:raise RuntimeError(f'bad appearance rows {bad}')
    rows=[]
    for m in mapped:
        z,reason=f.feature_row(m,starters,pidx,cidx,source_reg)
        if z is None:raise RuntimeError(f'feature coverage drift {m["identity"]}: {reason}')
        if z['strict_lag_violation']:raise RuntimeError(f'strict lag violation {m["identity"]}')
        rows.append({'identity':m['identity'],'season':m['season'],'div':m['div'],'date':m['target_date'],'x':[float(v) for v in z['x']],'q':np.asarray(m['qclose'],dtype=float)})
    rows.sort(key=lambda r:(r['date'],r['div'],r['identity']))
    return f,rows

def standardize_fit(X):
    mu=np.mean(X,axis=0);sd=np.std(X,axis=0);sd=np.where(sd<1e-12,1.0,sd);return mu,sd

def fit_offset_logistic(rows,labels,dims,l2,contract):
    X=np.asarray([r['x'][:dims] for r in rows],dtype=float);mu,sd=standardize_fit(X);Xs=(X-mu)/sd
    Z=np.column_stack([np.ones(len(rows)),Xs]);y=np.asarray([1.0 if labels[r['identity']]==1 else 0.0 for r in rows]);off=np.asarray([logit(r['q'][1]) for r in rows])
    theta=np.zeros(Z.shape[1],dtype=float);pen=np.zeros_like(theta);pen[1:]=float(l2);maxit=int(contract['binary_residual_model']['newton_max_iterations']);tol=float(contract['binary_residual_model']['newton_parameter_tolerance']);clip=tuple(contract['binary_residual_model']['sigmoid_linear_predictor_clip'])
    converged=False;iters=0
    for it in range(maxit):
        eta=off+Z@theta;p=sigmoid(eta,clip);w=np.maximum(p*(1-p),1e-12)
        grad=Z.T@(p-y)+pen*theta;H=Z.T@(w[:,None]*Z)+np.diag(pen)
        try:delta=np.linalg.solve(H,grad)
        except np.linalg.LinAlgError as e:raise RuntimeError(f'Newton linear solve failed: {e}')
        theta-=delta;iters=it+1
        if float(np.max(np.abs(delta)))<tol:converged=True;break
    if not converged:raise RuntimeError(f'Newton did not converge L2={l2} dims={dims}')
    return {'dims':int(dims),'l2':float(l2),'mu':mu,'sd':sd,'theta':theta,'iterations':iters,'converged':True}

def predict(rows,model,contract):
    X=np.asarray([r['x'][:model['dims']] for r in rows],dtype=float);Xs=(X-model['mu'])/model['sd'];Z=np.column_stack([np.ones(len(rows)),Xs]);off=np.asarray([logit(r['q'][1]) for r in rows]);pd=sigmoid(off+Z@model['theta'],tuple(contract['binary_residual_model']['sigmoid_linear_predictor_clip']))
    out={}
    for r,d in zip(rows,pd):
        hshare=float(r['q'][0]/(r['q'][0]+r['q'][2]));nd=1-float(d);out[r['identity']]=np.asarray([nd*hshare,float(d),nd*(1-hshare)],dtype=float)
    return out

def market_pred(rows):return {r['identity']:np.asarray(r['q'],dtype=float) for r in rows}
def auc_binary(y,s):
    y=np.asarray(y,dtype=int);s=np.asarray(s,dtype=float);n1=int(y.sum());n0=len(y)-n1
    if n1==0 or n0==0:return None
    order=np.argsort(s,kind='mergesort');ranks=np.empty(len(s),dtype=float);i=0
    while i<len(s):
        j=i+1
        while j<len(s) and s[order[j]]==s[order[i]]:j+=1
        rank=(i+1+j)/2.0;ranks[order[i:j]]=rank;i=j
    return float((ranks[y==1].sum()-n1*(n1+1)/2)/(n1*n0))
def metric_pack(rows,pred,labels):
    ys=np.asarray([labels[r['identity']] for r in rows],dtype=int);P=np.asarray([pred[r['identity']] for r in rows],dtype=float);eps=1e-15
    if np.max(np.abs(P.sum(axis=1)-1))>1e-12:raise RuntimeError('probability conservation failed')
    ll=float(np.mean([-math.log(max(float(P[i,y]),eps)) for i,y in enumerate(ys)]));one=np.eye(3)[ys];brier=float(np.mean(np.sum((P-one)**2,axis=1)));pc=np.cumsum(P,axis=1)[:,:2];oc=np.cumsum(one,axis=1)[:,:2];rps=float(np.mean(np.sum((pc-oc)**2,axis=1)/2));yd=(ys==1).astype(int);pd=P[:,1];dll=float(np.mean(-(yd*np.log(np.clip(pd,eps,1-eps))+(1-yd)*np.log(np.clip(1-pd,eps,1-eps)))));top=np.argmax(P,axis=1)
    return {'HDA_LogLoss':ll,'HDA_Brier':brier,'RPS':rps,'binary_Draw_LogLoss':dll,'Draw_AUC':auc_binary(yd,pd),'Top1_accuracy':float(np.mean(top==ys)),'Top1_draw_count':int(np.sum(top==1)),'actual_draw_count':int(np.sum(ys==1))}
def per_div_wins(rows,candidate,benchmark,labels):
    wins=0;detail={}
    for d in sorted({r['div'] for r in rows}):
        rr=[r for r in rows if r['div']==d];a=metric_pack(rr,candidate,labels);b=metric_pack(rr,benchmark,labels);win=a['HDA_LogLoss']<b['HDA_LogLoss'];wins+=int(win);detail[d]={'rows':len(rr),'candidate_HDA_LogLoss':a['HDA_LogLoss'],'benchmark_HDA_LogLoss':b['HDA_LogLoss'],'win':win}
    return wins,detail

def model_public(m):
    return {'dims':m['dims'],'l2':m['l2'],'iterations':m['iterations'],'converged':m['converged'],'mu_sha256':sha_text(','.join(format(float(x),'.17g') for x in m['mu'])),'sd_sha256':sha_text(','.join(format(float(x),'.17g') for x in m['sd'])),'theta_sha256':sha_text(','.join(format(float(x),'.17g') for x in m['theta']))}
def select_candidate(fit,val,labels,dims,contract,l2s):
    board=[];models={}
    market=market_pred(val)
    for l2 in l2s:
        m=fit_offset_logistic(fit,labels,dims,l2,contract);models[float(l2)]=m;pred=predict(val,m,contract);met=metric_pack(val,pred,labels);board.append({'l2':float(l2),'metrics':met,'model':model_public(m)})
    sel=sorted(board,key=lambda z:(z['metrics']['HDA_LogLoss'],z['metrics']['binary_Draw_LogLoss'],z['l2']))[0]
    return models[sel['l2']],sel,board,market

def gate(rows,market,context,full,labels,cfg):
    mm=metric_pack(rows,market,labels);cm=metric_pack(rows,context,labels);fm=metric_pack(rows,full,labels);wins,detail=per_div_wins(rows,full,context,labels)
    ok=(fm['HDA_LogLoss']<mm['HDA_LogLoss'] and fm['binary_Draw_LogLoss']<mm['binary_Draw_LogLoss'] and fm['HDA_Brier']<=mm['HDA_Brier'] and fm['RPS']<=mm['RPS'] and fm['HDA_LogLoss']<cm['HDA_LogLoss'] and fm['binary_Draw_LogLoss']<cm['binary_Draw_LogLoss'] and fm['Draw_AUC'] is not None and cm['Draw_AUC'] is not None and fm['Draw_AUC']>cm['Draw_AUC'] and wins>=int(cfg['full_HDA_LogLoss_division_wins_vs_context_min']))
    return {'market':mm,'market_context':cm,'full_lineup_attack':fm,'division_wins_vs_context':wins,'division_detail_vs_context':detail,'gate_pass':bool(ok)}
def blind_gate(rows,market,context,full,labels):
    mm=metric_pack(rows,market,labels);cm=metric_pack(rows,context,labels);fm=metric_pack(rows,full,labels)
    ok=(fm['HDA_LogLoss']<mm['HDA_LogLoss'] and fm['binary_Draw_LogLoss']<mm['binary_Draw_LogLoss'] and fm['HDA_Brier']<=mm['HDA_Brier'] and fm['RPS']<=mm['RPS'] and fm['HDA_LogLoss']<cm['HDA_LogLoss'] and fm['binary_Draw_LogLoss']<cm['binary_Draw_LogLoss'] and fm['Draw_AUC'] is not None and cm['Draw_AUC'] is not None and fm['Draw_AUC']>cm['Draw_AUC'])
    return {'market':mm,'market_context':cm,'full_lineup_attack':fm,'gate_pass':bool(ok)}
def write_stop(outdir,base,status,extra):
    base['status']=status;base['holdout_labels_accessed']=0;(outdir/'final_freeze_receipt_r39l.json').write_text(json.dumps({'final_freeze_completed':False,'holdout_labels_accessed_before_freeze':0,**extra},indent=2));(outdir/'r39l_result.json').write_text(json.dumps(base,indent=2));print(json.dumps({'status':status,'holdout_labels_accessed':0},indent=2))

def self_test():
    contract={'binary_residual_model':{'newton_max_iterations':50,'newton_parameter_tolerance':1e-10,'sigmoid_linear_predictor_clip':[-35,35]}}
    rows=[];labels={}
    for i in range(60):
        q=np.asarray([.38,.27,.35]);x=[(i%5-2)/2,(i%7-3)/3]+[0.1*(i%3)]*8;ident=f'i{i}';rows.append({'identity':ident,'q':q,'x':x,'div':'E0','date':i,'season':'x'});labels[ident]=1 if i%4==0 else (0 if i%2==0 else 2)
    m=fit_offset_logistic(rows,labels,2,10.0,contract);p=predict(rows,m,contract);assert max(abs(float(v.sum())-1) for v in p.values())<1e-12;assert m['converged'];print('PASS_R39L_EVALUATOR_SELF_TEST')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--prereg',type=Path);ap.add_argument('--contract',type=Path);ap.add_argument('--source-registration',type=Path);ap.add_argument('--r39i-registration',type=Path);ap.add_argument('--market-dir',type=Path);ap.add_argument('--raw-dir',type=Path);ap.add_argument('--games',type=Path);ap.add_argument('--lineups',type=Path);ap.add_argument('--appearances',type=Path);ap.add_argument('--out-dir',type=Path);ap.add_argument('--self-test',action='store_true');a=ap.parse_args()
    if a.self_test:self_test();return
    pre=json.loads(a.prereg.read_text());contract=json.loads(a.contract.read_text());src=json.loads(a.source_registration.read_text());r39i=json.loads(a.r39i_registration.read_text());outdir=a.out_dir;outdir.mkdir(parents=True,exist_ok=True)
    assert pre['preregistration_status']=='FEATURE_COVERAGE_PASSED_MODEL_FROZEN_BEFORE_TARGET_LABEL_EVALUATION';assert pre['source_binding']['final_feature_coverage_audit_status']=='PASS_R39L_ZERO_TARGET_LABEL_FEATURE_COVERAGE';assert pre['source_binding']['feature_audit_target_labels_accessed']==0;assert contract['frozen_before_real_target_label_evaluation'] is True
    f,rows=build_rows(a.market_dir,a.games,a.lineups,a.appearances,src,r39i);pre_rows=[r for r in rows if r['season']!='2526'];hold=[r for r in rows if r['season']=='2526']
    if len(pre_rows)!=8161 or len(hold)!=1273:raise RuntimeError(f'feature row drift pre={len(pre_rows)} hold={len(hold)}')
    seed=int(pre['source_binding']['fixed100_seed']);fixed=sorted(hold,key=lambda r:f.htxt(f"{seed}|{r['identity']}"))[:100];fixedsha=f.set_sha([r['identity'] for r in fixed])
    if fixedsha!=pre['source_binding']['fixed100_identity_sha256']:raise RuntimeError(f'fixed100 drift {fixedsha}')
    if any(len(r['x'])!=10 for r in rows):raise RuntimeError('feature dimension drift')
    feature_receipt={'feature_freeze_completed':True,'frozen_at_utc':datetime.now(timezone.utc).isoformat(),'preholdout_rows':len(pre_rows),'holdout_pool_rows':len(hold),'fixed100_rows':len(fixed),'fixed100_identity_sha256':fixedsha,'preholdout_feature_sha256':feature_hash(pre_rows),'fixed100_feature_sha256':feature_hash(fixed),'target_labels_accessed_before_feature_freeze':0,'prereg_blob_sha':git_blob('football-data/research/lineup_attack_r39l/preregistration_r39l.json'),'contract_blob_sha':git_blob('football-data/research/lineup_attack_r39l/evaluator_contract_r39l.json'),'feature_builder_blob_sha':git_blob('football-data/research/lineup_attack_r39l/audit_lineup_attack_features_r39l.py'),'evaluator_blob_sha':git_blob('football-data/research/lineup_attack_r39l/evaluate_lineup_attack_r39l.py')};(outdir/'feature_freeze_receipt_r39l.json').write_text(json.dumps(feature_receipt,indent=2))
    preids={r['identity'] for r in pre_rows};labels=load_labels(a.raw_dir,{'1920','2021','2122','2223','2324','2425'},f,preids)
    n=len(pre_rows);nf=int(contract['chronology']['fit_rows']);nv=int(contract['chronology']['validation_rows']);fit=pre_rows[:nf];val=pre_rows[nf:nf+nv];policy=pre_rows[nf+nv:]
    if (len(fit),len(val),len(policy))!=(5712,1224,1225):raise RuntimeError('split drift')
    l2s=[float(x) for x in contract['binary_residual_model']['l2_candidates']]
    csel,cinfo,cboard,_=select_candidate(fit,val,labels,2,contract,l2s);fsel,finfo,fboard,marketv=select_candidate(fit,val,labels,10,contract,l2s);cpredv=predict(val,csel,contract);fpredv=predict(val,fsel,contract);vg=gate(val,marketv,cpredv,fpredv,labels,pre['validation_gate_all_required'])
    base={'schema_version':pre['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'source_counts':{'preholdout':len(pre_rows),'holdout_pool':len(hold),'fixed100':len(fixed)},'fixed100_identity_sha256':fixedsha,'feature_freeze_receipt':feature_receipt,'split_counts':{'fit':len(fit),'validation':len(val),'policy':len(policy)},'selected_l2':{'market_context':cinfo['l2'],'full_lineup_attack':finfo['l2']},'validation_candidate_boards':{'market_context':cboard,'full_lineup_attack':fboard},'validation':vg,'holdout_labels_accessed':0,'hard_limits':pre['hard_limits']}
    if not vg['gate_pass']:
        write_stop(outdir,base,pre['validation_gate_all_required']['if_fail'],{'validation_pass':False,'selected_l2':base['selected_l2'],'fixed100_identity_sha256':fixedsha});return
    trainval=fit+val;cfit=fit_offset_logistic(trainval,labels,2,cinfo['l2'],contract);ffit=fit_offset_logistic(trainval,labels,10,finfo['l2'],contract);marketp=market_pred(policy);cpredp=predict(policy,cfit,contract);fpredp=predict(policy,ffit,contract);pg=gate(policy,marketp,cpredp,fpredp,labels,pre['policy_stress_gate_all_required']);base['policy']=pg
    if not pg['gate_pass']:
        write_stop(outdir,base,pre['policy_stress_gate_all_required']['if_fail'],{'validation_pass':True,'policy_pass':False,'selected_l2':base['selected_l2'],'fixed100_identity_sha256':fixedsha});return
    cfinal=fit_offset_logistic(pre_rows,labels,2,cinfo['l2'],contract);ffinal=fit_offset_logistic(pre_rows,labels,10,finfo['l2'],contract);final_freeze={'final_freeze_completed':True,'frozen_at_utc':datetime.now(timezone.utc).isoformat(),'validation_pass':True,'policy_pass':True,'selected_l2':base['selected_l2'],'market_context_model':model_public(cfinal),'full_lineup_attack_model':model_public(ffinal),'fixed100_identity_sha256':fixedsha,'holdout_labels_accessed_before_freeze':0,'preholdout_feature_sha256':feature_receipt['preholdout_feature_sha256'],'fixed100_feature_sha256':feature_receipt['fixed100_feature_sha256'],'prereg_blob_sha':feature_receipt['prereg_blob_sha'],'contract_blob_sha':feature_receipt['contract_blob_sha'],'feature_builder_blob_sha':feature_receipt['feature_builder_blob_sha'],'evaluator_blob_sha':feature_receipt['evaluator_blob_sha']};(outdir/'final_freeze_receipt_r39l.json').write_text(json.dumps(final_freeze,indent=2))
    fixedids={r['identity'] for r in fixed};hlab=load_labels(a.raw_dir,{'2526'},f,fixedids);marketh=market_pred(fixed);ch=predict(fixed,cfinal,contract);fh=predict(fixed,ffinal,contract);hg=blind_gate(fixed,marketh,ch,fh,hlab);base['holdout']=hg;base['holdout_labels_accessed']=100;base['status']=pre['blind_holdout_gate_all_required']['pass_status'] if hg['gate_pass'] else pre['blind_holdout_gate_all_required']['fail_status'];base['final_freeze_receipt']=final_freeze;(outdir/'r39l_result.json').write_text(json.dumps(base,indent=2));print(json.dumps({'status':base['status'],'selected_l2':base['selected_l2'],'validation':vg,'policy':pg,'holdout':hg,'holdout_labels_accessed':100},indent=2))
if __name__=='__main__':main()
