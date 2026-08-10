#!/usr/bin/env python3
from __future__ import annotations

import argparse,hashlib,importlib.util,json,math
from datetime import datetime,timezone
from pathlib import Path
import numpy as np


def sha_text(s:str)->str:return hashlib.sha256(s.encode()).hexdigest()
def canonical_sha(o)->str:return sha_text(json.dumps(o,sort_keys=True,separators=(',',':')))

def load_module(name:str,path:Path):
    spec=importlib.util.spec_from_file_location(name,path)
    if spec is None or spec.loader is None:raise RuntimeError(f'cannot import {name}')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def setsha(xs):return sha_text('\n'.join(sorted(x['identity'] for x in xs))+'\n')

def softmax_from_offsets(Z,beta,q):
    q=np.clip(np.asarray(q,dtype=float),1e-8,1.0)
    off_h=np.log(q[:,0]/q[:,2]);off_d=np.log(q[:,1]/q[:,2])
    logits=np.column_stack([off_h+Z@beta[0],off_d+Z@beta[1],np.zeros(len(Z))])
    logits-=logits.max(axis=1,keepdims=True)
    e=np.exp(logits);return e/e.sum(axis=1,keepdims=True)

def multinomial_objective(Z,beta,q,y,l2):
    p=softmax_from_offsets(Z,beta,q)
    nll=-float(np.log(np.clip(p[np.arange(len(y)),y],1e-15,1.0)).sum())
    penalty=.5*l2*float((beta[:,1:]**2).sum())
    return nll+penalty

def fit_market_offset_multinomial(X,q,y,l2,max_iter,delta_tol,grad_tol,backtrack,min_step):
    X=np.asarray(X,dtype=float);q=np.asarray(q,dtype=float);y=np.asarray(y,dtype=int)
    Z=np.column_stack([np.ones(len(X)),X]);pdim=Z.shape[1]
    beta=np.zeros((2,pdim),dtype=float);pen=np.ones(pdim,dtype=float)*l2;pen[0]=0.0
    initial_obj=multinomial_objective(Z,beta,q,y,l2);current_obj=initial_obj
    converged=False;reason='maximum_iterations';delta_max=None;grad_max=None;backtracks_total=0;accepted_steps=[]
    for it in range(1,max_iter+1):
        probs=softmax_from_offsets(Z,beta,q);ph=probs[:,0];pd=probs[:,1]
        ih=(y==0).astype(float);idd=(y==1).astype(float)
        gh=Z.T@(ph-ih)+pen*beta[0];gd=Z.T@(pd-idd)+pen*beta[1]
        grad=np.concatenate([gh,gd]);grad_max=float(np.max(np.abs(grad)))
        if grad_max<=grad_tol:
            converged=True;reason='gradient_tolerance';delta_max=0.0;break
        hh=Z.T@(Z*(ph*(1-ph))[:,None])+np.diag(pen)
        dd=Z.T@(Z*(pd*(1-pd))[:,None])+np.diag(pen)
        hd=Z.T@(Z*(-ph*pd)[:,None])
        H=np.block([[hh,hd],[hd.T,dd]])+np.eye(2*pdim)*1e-10
        delta=np.linalg.solve(H,grad).reshape(2,pdim)
        step=1.0;accepted=False
        while step>=min_step-1e-15:
            proposal=beta-step*delta
            obj=multinomial_objective(Z,proposal,q,y,l2)
            if math.isfinite(obj) and obj<=current_obj+1e-10:
                beta=proposal;current_obj=obj;accepted=True;accepted_steps.append(step);break
            step*=backtrack;backtracks_total+=1
        if not accepted:raise RuntimeError('R39F Newton backtracking failed to find a non-increasing step')
        delta_max=float(np.max(np.abs(step*delta)))
        if delta_max<=delta_tol:
            converged=True;reason='coefficient_delta_tolerance';break
    probs=softmax_from_offsets(Z,beta,q);ph=probs[:,0];pd=probs[:,1]
    ih=(y==0).astype(float);idd=(y==1).astype(float)
    gh=Z.T@(ph-ih)+pen*beta[0];gd=Z.T@(pd-idd)+pen*beta[1]
    grad_max=float(np.max(np.abs(np.concatenate([gh,gd]))))
    return beta,{
      'iterations':it,'converged':converged,'convergence_reason':reason,
      'coefficient_delta_max':delta_max,'gradient_abs_max':grad_max,
      'initial_objective':initial_obj,'final_objective':current_obj,
      'line_search_backtracks_total':backtracks_total,
      'accepted_step_min':min(accepted_steps) if accepted_steps else None,
      'accepted_step_max':max(accepted_steps) if accepted_steps else None}

def predict_market_offset_multinomial(X,mean,std,beta,q):
    Xs=(np.asarray(X,dtype=float)-mean)/std;Z=np.column_stack([np.ones(len(Xs)),Xs])
    return softmax_from_offsets(Z,beta,np.asarray(q,dtype=float))

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--prereg',type=Path,required=True)
    ap.add_argument('--model-registration',type=Path,required=True)
    ap.add_argument('--sanitized-dir',type=Path,required=True)
    ap.add_argument('--original-dir',type=Path,required=True)
    ap.add_argument('--parent-code',type=Path,required=True)
    ap.add_argument('--sequence-code',type=Path,required=True)
    ap.add_argument('--out-dir',type=Path,required=True)
    a=ap.parse_args();a.out_dir.mkdir(parents=True,exist_ok=True)
    pre=json.loads(a.prereg.read_text(encoding='utf-8'));model_reg=json.loads(a.model_registration.read_text(encoding='utf-8'))
    assert pre['status']=='PRE_REGISTERED_BEFORE_R39F_FOURTH100_LABEL_ACCESS'
    assert model_reg['status']=='MODEL_REPRESENTATION_FROZEN_BEFORE_FOURTH100_IDENTITY_LOCK'
    assert pre['models']['r39f']['threshold_selection']=='forbidden'
    parent=load_module('r39c_parent',a.parent_code);seqmod=load_module('r39e_sequence',a.sequence_code)

    rows,_=parent.load_feature_rows(a.sanitized_dir,{})
    start=datetime.fromisoformat(pre['identity_binding']['holdout_start'])
    training=[x for x in rows.values() if x['dt']<start];holdout=[x for x in rows.values() if x['dt']>=start]
    assert len(training)==49511 and len(holdout)==10734
    ordered=sorted(holdout,key=lambda x:sha_text(f'51139|{x["identity"]}'))
    first,second,third,fourth=ordered[:100],ordered[100:200],ordered[200:300],ordered[300:400]
    ib=pre['identity_binding']
    assert setsha(first)==ib['first100_identity_sha256'];assert setsha(second)==ib['second100_identity_sha256']
    assert setsha(third)==ib['third100_identity_sha256'];assert setsha(fourth)==ib['fourth100_identity_sha256']
    assert not ({x['identity'] for x in first+second+third}&{x['identity'] for x in fourth})

    eligible_ids=set(rows);seq,core_diag=seqmod.load_sequence_features(a.sanitized_dir,eligible_ids,parent)
    assert core_diag['min']>=3 and set(seq)==eligible_ids
    training_ids={x['identity'] for x in training}
    labels,training_access,holdout_before=parent.read_training_labels(a.original_dir,training_ids,start)
    assert holdout_before==0 and training_access==len(training)==len(labels)==49511
    y3=np.asarray([parent.actual_idx(*labels[x['identity']]) for x in training],dtype=int)
    ydraw=(y3==1).astype(float)

    Xc=np.asarray([x['features'] for x in training],dtype=float)
    Xseq=np.asarray([seq[x['identity']] for x in training],dtype=float)
    Xe=np.column_stack([Xc,Xseq])
    qtrain=np.asarray([x['q1'] for x in training],dtype=float)
    assert Xc.shape==(49511,15) and Xseq.shape==(49511,25) and Xe.shape==(49511,40)

    Xcs,mc,sc=parent.standardize(Xc);bc,dc=parent.fit_logistic(Xcs,ydraw,l2=1.0,max_iter=50,tol=1e-8)
    Xes,me,se=parent.standardize(Xe);be,de=parent.fit_logistic(Xes,ydraw,l2=1.0,max_iter=50,tol=1e-8)
    Xfs,mf,sf=parent.standardize(Xseq)
    cfg=pre['models']['r39f']
    bf,df=fit_market_offset_multinomial(
      Xfs,qtrain,y3,l2=float(cfg['l2_lambda']),max_iter=int(cfg['maximum_iterations']),
      delta_tol=float(cfg['coefficient_delta_tolerance']),grad_tol=float(cfg['gradient_abs_tolerance']),
      backtrack=float(cfg['backtracking_factor']),min_step=float(cfg['minimum_step']))
    if not df['converged']:raise RuntimeError(f'R39F multinomial did not converge: {df}')

    freeze={
      'schema':'r39f-market-offset-multinomial-model-freeze','fourth100_identity_sha256':setsha(fourth),
      'first100_identity_sha256':setsha(first),'second100_identity_sha256':setsha(second),'third100_identity_sha256':setsha(third),
      'training_rows':len(training),'training_class_counts':{'H':int((y3==0).sum()),'D':int((y3==1).sum()),'A':int((y3==2).sum())},
      'first300_holdout_labels_accessed':0,'fourth100_labels_accessed_before_parameter_freeze':0,
      'sequence_core_bookmaker_diagnostics_all_eligible':core_diag,
      'r39c':{'mean':mc.tolist(),'std':sc.tolist(),'beta':bc.tolist(),'diagnostics':dc},
      'r39e':{'mean':me.tolist(),'std':se.tolist(),'beta':be.tolist(),'diagnostics':de},
      'r39f':{'mean':mf.tolist(),'std':sf.tolist(),'beta_H':bf[0].tolist(),'beta_D':bf[1].tolist(),'diagnostics':df,'reference_class':'A','l2_lambda':1.0},
      'numpy_version':np.__version__}
    freeze['model_parameter_sha256']=canonical_sha(freeze)
    fp=a.out_dir/'model_parameter_freeze_r39f.json';fp.write_text(json.dumps(freeze,ensure_ascii=False,indent=2),encoding='utf-8')
    assert fp.exists() and fp.stat().st_size>0

    fourth_ids={x['identity'] for x in fourth};test_labels,test_access=parent.read_test_labels_after_freeze(a.original_dir,fourth_ids)
    assert test_access==100 and len(test_labels)==100
    ts=sorted(fourth,key=lambda z:(z['dt'],z['identity']))
    Xct=np.asarray([x['features'] for x in ts],dtype=float);Xseqt=np.asarray([seq[x['identity']] for x in ts],dtype=float);Xet=np.column_stack([Xct,Xseqt])
    qtest=np.asarray([x['q1'] for x in ts],dtype=float)
    pc=parent.predict_logistic((Xct-mc)/sc,bc);pe=parent.predict_logistic((Xet-me)/se,be)
    market=[np.asarray(q,dtype=float) for q in qtest]
    r39c=[parent.threeway(q,p) for q,p in zip(qtest,pc)];r39e=[parent.threeway(q,p) for q,p in zip(qtest,pe)]
    r39f=[p for p in predict_market_offset_multinomial(Xseqt,mf,sf,bf,qtest)]
    actual=[parent.actual_idx(*test_labels[x['identity']]) for x in ts]
    mm=parent.metrics(market,actual);mcx=parent.metrics(r39c,actual);mex=parent.metrics(r39e,actual);mfx=parent.metrics(r39f,actual)
    aucm=parent.auc_draw(market,actual);aucc=parent.auc_draw(r39c,actual);auce=parent.auc_draw(r39e,actual);aucf=parent.auc_draw(r39f,actual)
    md=[int(np.argmax(p)) for p in market];cd=[int(np.argmax(p)) for p in r39c];ed=[int(np.argmax(p)) for p in r39e];fd=[int(np.argmax(p)) for p in r39f]
    dates=[x['dt'].date().isoformat() for x in ts]
    boot_m=seqmod.cluster_bootstrap(dates,md,fd,actual,5000,511394);boot_e=seqmod.cluster_bootstrap(dates,ed,fd,actual,5000,511395)
    prob_gate={
      'r39f_log_loss_strictly_better_than_r39e':mfx['log_loss']<mex['log_loss'],
      'r39f_brier_strictly_better_than_r39e':mfx['brier']<mex['brier'],
      'r39f_rps_strictly_better_than_r39e':mfx['rps']<mex['rps'],
      'r39f_draw_auc_strictly_better_than_market':aucf is not None and aucm is not None and aucf>aucm}
    decision_gate={
      'r39f_accuracy_strictly_better_than_market':mfx['accuracy']>mm['accuracy'],
      'r39f_accuracy_strictly_better_than_r39c':mfx['accuracy']>mcx['accuracy'],
      'r39f_accuracy_strictly_better_than_r39e':mfx['accuracy']>mex['accuracy'],
      'r39f_predicted_draw_count_at_least_one':mfx['predicted_draw_count']>=1,
      'r39f_draw_f1_strictly_better_than_r39e':mfx['draw_f1']>mex['draw_f1']}
    uncertainty={'cluster_bootstrap_R39F_minus_market_accuracy_p05_positive':boot_m['p05']>0}
    prob_pass=all(prob_gate.values());decision_pass=all(decision_gate.values());uncertainty_pass=all(uncertainty.values());overall=prob_pass and decision_pass and uncertainty_pass
    status='PASS_R39F_MARKET_OFFSET_MULTINOMIAL_CHALLENGER_EXPLORATION_ONLY' if overall else 'NO_R39F_MARKET_OFFSET_MULTINOMIAL_INCREMENT_FOURTH100_EXPLORATION_ONLY'
    discord={
      'r39f_correct_market_wrong':sum(int(f==y and m!=y) for f,m,y in zip(fd,md,actual)),
      'market_correct_r39f_wrong':sum(int(m==y and f!=y) for f,m,y in zip(fd,md,actual)),
      'r39f_correct_r39e_wrong':sum(int(f==y and e!=y) for f,e,y in zip(fd,ed,actual)),
      'r39e_correct_r39f_wrong':sum(int(e==y and f!=y) for f,e,y in zip(fd,ed,actual))}
    per=[]
    for x,y,pm,pcp,pep,pfp,dm,dcx,dex,dfx in zip(ts,actual,market,r39c,r39e,r39f,md,cd,ed,fd):
        per.append({'identity':x['identity'],'match_datetime':x['dt'].isoformat(),'actual':['H','D','A'][y],
          'market_probability':[float(v) for v in pm],'r39c_probability':[float(v) for v in pcp],
          'r39e_probability':[float(v) for v in pep],'r39f_probability':[float(v) for v in pfp],
          'market_top1':['H','D','A'][dm],'r39c_top1':['H','D','A'][dcx],'r39e_top1':['H','D','A'][dex],'r39f_top1':['H','D','A'][dfx]})
    (a.out_dir/'per_match_r39f.json').write_text(json.dumps(per,ensure_ascii=False,indent=2),encoding='utf-8')
    result={
      'schema_version':pre['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'status':status,
      'identity_set_sha256':setsha(fourth),'first300_fourth100_overlap':0,
      'access_audit':{'training_labels_accessed':training_access,'first300_holdout_labels_accessed':0,'fourth100_labels_accessed_before_parameter_freeze':0,'fourth100_labels_accessed_after_parameter_freeze':test_access},
      'actual_counts':{'H':sum(y==0 for y in actual),'D':sum(y==1 for y in actual),'A':sum(y==2 for y in actual)},
      'metrics':{'market':{**mm,'draw_auc':aucm},'r39c':{**mcx,'draw_auc':aucc},'r39e':{**mex,'draw_auc':auce},'r39f':{**mfx,'draw_auc':aucf}},
      'paired_discordance':discord,'bootstrap':{'r39f_minus_market_accuracy':boot_m,'r39f_minus_r39e_accuracy':boot_e},
      'gates':{'probability_signal':{**prob_gate,'passed':prob_pass},'decision_increment':{**decision_gate,'passed':decision_pass},'strong_uncertainty':{**uncertainty,'passed':uncertainty_pass},'overall_passed':overall},
      'model_parameter_sha256':freeze['model_parameter_sha256'],'r39f_optimizer_diagnostics':df,'hard_limits':pre['hard_limits']}
    (a.out_dir/'result_r39f.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
