#!/usr/bin/env python3
from __future__ import annotations

import argparse,csv,gzip,hashlib,json,math,random,re
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
import numpy as np

COL_RE=re.compile(r'^(home|draw|away)_b(\d+)_(\d+)$')
OUTCOMES=('home','draw','away')

def htxt(s:str)->str:return hashlib.sha256(s.encode()).hexdigest()
def hfile(p:Path)->str:
    h=hashlib.sha256()
    with p.open('rb') as f:
        for c in iter(lambda:f.read(1024*1024),b''):h.update(c)
    return h.hexdigest()
def parse_dt(d,t):
    for fmt in ('%Y-%m-%d %H:%M:%S','%Y-%m-%d %H:%M'):
        try:return datetime.strptime(f'{d} {t}',fmt)
        except ValueError:pass
    raise ValueError(f'bad datetime {d} {t}')
def valid(v):
    t=v.strip().casefold()
    if not t or t in {'nan','na','null','none'}:return False
    try:x=float(t)
    except ValueError:return False
    return math.isfinite(x) and x>1.0
def suffix(h):return 71-int(h)
def clip(p,lo=1e-8,hi=0.99999999):return min(hi,max(lo,float(p)))
def logit(p):
    p=clip(p);return math.log(p/(1-p))
def sigmoid(x):
    if x>=0:return 1/(1+math.exp(-x))
    e=math.exp(x);return e/(1+e)
def entropy(p):return -sum(x*math.log(max(x,1e-15)) for x in p)

def devig(h,d,a):
    inv=np.array([1/float(h),1/float(d),1/float(a)],dtype=float);inv/=inv.sum();return inv

def parse_mapping(header):
    m={}
    for i,name in enumerate(header[3:],3):
        x=COL_RE.match(name)
        if x:m[(int(x.group(2)),int(x.group(3)),x.group(1))]=i
    if len(m)!=32*72*3:raise RuntimeError(f'odds mapping {len(m)}')
    return m

def feature_from_row(row,mapping):
    cuts=(24,6,1);complete={}
    for h in cuts:
        s=suffix(h);books=[]
        for b in range(1,33):
            vals=[row[mapping[(b,s,o)]] for o in OUTCOMES]
            if all(valid(v) for v in vals):books.append(b)
        complete[h]=set(books)
    common=set.intersection(*(complete[h] for h in cuts))
    if len(common)<5:return None
    qs={};draw_std={}
    for h in cuts:
        s=suffix(h);per=[]
        for b in sorted(common):
            per.append(devig(*(row[mapping[(b,s,o)]] for o in OUTCOMES)))
        arr=np.vstack(per);qs[h]=arr.mean(axis=0);draw_std[h]=float(arr[:,1].std(ddof=0))
    q24,q6,q1=qs[24],qs[6],qs[1]
    d246=float(q6[1]-q24[1]);d61=float(q1[1]-q6[1])
    gap24=abs(float(q24[0]-q24[2]));gap1=abs(float(q1[0]-q1[2]))
    e24=entropy(q24);e1=entropy(q1)
    features=[
        logit(float(q1[1])),d246,d61,d61/5.0-d246/18.0,
        draw_std[24],draw_std[6],draw_std[1],draw_std[1]-draw_std[24],
        gap24,gap1,gap1-gap24,e24,e1,e1-e24,math.log1p(len(common))
    ]
    return {'features':features,'q1':q1.tolist(),'common_bookies':len(common)}

def load_feature_rows(source_dir:Path,prereg:dict):
    rows={};max_dt=None
    for source in ('odds_series.csv.gz','odds_series_b.csv.gz'):
        p=source_dir/source.replace('.csv.gz','_no_scores.csv.gz')
        with gzip.open(p,'rt',encoding='utf-8-sig',newline='') as f:
            reader=csv.reader(f);header=next(reader)
            assert header[:3]==['match_id','match_date','match_time']
            assert 'score_home' not in header and 'score_away' not in header
            mapping=parse_mapping(header)
            for row in reader:
                if len(row)!=len(header) or not row[0].strip():continue
                feat=feature_from_row(row,mapping)
                if feat is None:continue
                dt=parse_dt(row[1],row[2]);identity=f'{source}|{row[0].strip()}'
                feat.update({'identity':identity,'source_file':source,'match_id':row[0].strip(),'dt':dt})
                rows[identity]=feat
                max_dt=dt if max_dt is None or dt>max_dt else max_dt
    return rows,max_dt

def recompute_lock(rows:dict,prereg:dict):
    start=datetime.fromisoformat(prereg['identity_binding']['holdout_start'])
    training=[x for x in rows.values() if x['dt']<start];holdout=[x for x in rows.values() if x['dt']>=start]
    seed=51139
    selected=sorted(holdout,key=lambda x:htxt(f'{seed}|{x["identity"]}'))[:100]
    ids=sorted(x['identity'] for x in selected);sha=htxt('\n'.join(ids)+'\n')
    b=prereg['identity_binding']
    assert sha==b['identity_set_sha256'],(sha,b['identity_set_sha256'])
    assert len(training)==b['training_eligible_rows'];assert len(holdout)==b['holdout_eligible_rows'];assert len(selected)==100
    return training,holdout,selected,sha

def read_training_labels(original_dir:Path,training_ids:set[str],holdout_start:datetime):
    labels={};accessed=0;holdout_accessed=0
    for source in ('odds_series.csv.gz','odds_series_b.csv.gz'):
        with gzip.open(original_dir/source,'rt',encoding='utf-8-sig',newline='') as f:
            r=csv.reader(f);h=next(r);assert h[:5]==['match_id','match_date','match_time','score_home','score_away']
            for row in r:
                if len(row)<5:continue
                dt=parse_dt(row[1],row[2])
                identity=f'{source}|{row[0].strip()}'
                if dt>=holdout_start:
                    continue
                if identity not in training_ids:continue
                hg=int(float(row[3]));ag=int(float(row[4]));accessed+=1
                labels[identity]=(hg,ag)
    return labels,accessed,holdout_accessed

def read_test_labels_after_freeze(original_dir:Path,test_ids:set[str]):
    labels={};accessed=0
    for source in ('odds_series.csv.gz','odds_series_b.csv.gz'):
        with gzip.open(original_dir/source,'rt',encoding='utf-8-sig',newline='') as f:
            r=csv.reader(f);h=next(r);assert h[:5]==['match_id','match_date','match_time','score_home','score_away']
            for row in r:
                if len(row)<5:continue
                identity=f'{source}|{row[0].strip()}'
                if identity not in test_ids:continue
                hg=int(float(row[3]));ag=int(float(row[4]));accessed+=1;labels[identity]=(hg,ag)
    return labels,accessed

def standardize(X):
    mean=X.mean(axis=0);std=X.std(axis=0);std=np.where(std<1e-12,1.0,std);return (X-mean)/std,mean,std

def fit_logistic(X,y,l2=1.0,max_iter=50,tol=1e-8):
    Z=np.column_stack([np.ones(len(X)),X]);beta=np.zeros(Z.shape[1]);pen=np.zeros_like(beta);pen[1:]=l2
    converged=False;delta_max=None
    for it in range(1,max_iter+1):
        eta=Z@beta;p=1/(1+np.exp(-np.clip(eta,-40,40)));w=np.clip(p*(1-p),1e-8,None)
        grad=Z.T@(p-y)+pen*beta;H=Z.T@(Z*w[:,None])+np.diag(pen)+np.eye(Z.shape[1])*1e-10
        delta=np.linalg.solve(H,grad);beta-=delta;delta_max=float(np.max(np.abs(delta)))
        if delta_max<tol:converged=True;break
    eta=Z@beta;p=1/(1+np.exp(-np.clip(eta,-40,40)));grad=Z.T@(p-y)+pen*beta
    return beta,{'iterations':it,'converged':converged,'coefficient_delta_max':delta_max,'gradient_abs_max':float(np.max(np.abs(grad)))}
def predict_logistic(X,beta):
    Z=np.column_stack([np.ones(len(X)),X]);eta=Z@beta;return 1/(1+np.exp(-np.clip(eta,-40,40)))
def threeway(q1,pd):
    pd=clip(pd);h,a=float(q1[0]),float(q1[2]);s=h+a
    return np.array([(1-pd)*h/s,pd,(1-pd)*a/s],dtype=float)
def actual_idx(hg,ag):return 0 if hg>ag else 1 if hg==ag else 2
def metrics(probs,actual):
    n=len(actual);hits=0;ll=0;brier=0;rps=0;draw_preds=0;draw_tp=0
    for p,y in zip(probs,actual):
        pred=int(np.argmax(p));hits+=pred==y;draw_preds+=pred==1;draw_tp+=(pred==1 and y==1)
        ll-=math.log(max(float(p[y]),1e-15));one=np.zeros(3);one[y]=1;brier+=float(((p-one)**2).sum())
        rps+=0.5*((float(p[0])-float(one[0]))**2+((float(p[0]+p[1]))-float(one[0]+one[1]))**2)
    actual_draw=sum(y==1 for y in actual);prec=draw_tp/draw_preds if draw_preds else 0.0;rec=draw_tp/actual_draw if actual_draw else 0.0;f1=2*prec*rec/(prec+rec) if prec+rec else 0.0
    return {'rows':n,'hits':hits,'accuracy':hits/n,'log_loss':ll/n,'brier':brier/n,'rps':rps/n,'predicted_draw_count':draw_preds,'actual_draw_count':actual_draw,'draw_true_positive':draw_tp,'draw_precision':prec,'draw_recall':rec,'draw_f1':f1}
def auc_draw(probs,actual):
    pos=[float(p[1]) for p,y in zip(probs,actual) if y==1];neg=[float(p[1]) for p,y in zip(probs,actual) if y!=1]
    if not pos or not neg:return None
    wins=0.0
    for a in pos:
        for b in neg:wins+=1 if a>b else 0.5 if a==b else 0
    return wins/(len(pos)*len(neg))
def bootstrap_accuracy(base,cand,actual,n,seed):
    rnd=random.Random(seed);N=len(actual);diff=[]
    base_ok=[int(np.argmax(p)==y) for p,y in zip(base,actual)];cand_ok=[int(np.argmax(p)==y) for p,y in zip(cand,actual)]
    for _ in range(n):
        s=0
        for _j in range(N):
            i=rnd.randrange(N);s+=cand_ok[i]-base_ok[i]
        diff.append(s/N)
    diff.sort();q=lambda x:diff[min(len(diff)-1,max(0,int(x*(len(diff)-1))))]
    return {'samples':n,'seed':seed,'mean':sum(diff)/n,'p05':q(.05),'p50':q(.50),'p95':q(.95)}
def canonical_sha(obj):return htxt(json.dumps(obj,sort_keys=True,separators=(',',':')))

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--prereg',type=Path,required=True);ap.add_argument('--sanitized-dir',type=Path,required=True);ap.add_argument('--original-dir',type=Path,required=True);ap.add_argument('--out-dir',type=Path,required=True);a=ap.parse_args();a.out_dir.mkdir(parents=True,exist_ok=True)
    pre=json.loads(a.prereg.read_text());rows,max_dt=load_feature_rows(a.sanitized_dir,pre);training,holdout,test,lock_sha=recompute_lock(rows,pre)
    start=datetime.fromisoformat(pre['identity_binding']['holdout_start']);training_ids={x['identity'] for x in training};test_ids={x['identity'] for x in test}
    labels,training_label_access,holdout_before=read_training_labels(a.original_dir,training_ids,start)
    assert holdout_before==0;assert len(labels)==len(training),(len(labels),len(training))
    names=pre['features'];X=np.array([x['features'] for x in training],dtype=float);y=np.array([1 if labels[x['identity']][0]==labels[x['identity']][1] else 0 for x in training],dtype=float)
    Xs,mean,std=standardize(X)
    beta_cal,diag_cal=fit_logistic(Xs[:,[0]],y,l2=1.0,max_iter=50,tol=1e-8);beta_traj,diag_traj=fit_logistic(Xs,y,l2=1.0,max_iter=50,tol=1e-8)
    model_payload={'schema':'r39c-model-freeze','identity_set_sha256':lock_sha,'training_rows':len(training),'training_draws':int(y.sum()),'holdout_labels_accessed_before_freeze':0,'feature_names':names,'training_mean':mean.tolist(),'training_std':std.tolist(),'calibration_beta':beta_cal.tolist(),'trajectory_beta':beta_traj.tolist(),'calibration_diagnostics':diag_cal,'trajectory_diagnostics':diag_traj,'numpy_version':np.__version__}
    model_payload['model_parameter_sha256']=canonical_sha(model_payload)
    freeze_path=a.out_dir/'model_freeze_receipt.json';freeze_path.write_text(json.dumps(model_payload,ensure_ascii=False,indent=2),encoding='utf-8')
    assert freeze_path.exists() and freeze_path.stat().st_size>0

    test_labels,test_label_access=read_test_labels_after_freeze(a.original_dir,test_ids);assert len(test_labels)==100 and test_label_access==100
    test_sorted=sorted(test,key=lambda x:(x['dt'],x['identity']));Xt=np.array([x['features'] for x in test_sorted]);Xts=(Xt-mean)/std
    pcal=predict_logistic(Xts[:,[0]],beta_cal);ptraj=predict_logistic(Xts,beta_traj)
    baseline=[];cal=[];traj=[];actual=[]
    for i,x in enumerate(test_sorted):
        q=np.array(x['q1'],dtype=float);baseline.append(q);cal.append(threeway(q,pcal[i]));traj.append(threeway(q,ptraj[i]));hg,ag=test_labels[x['identity']];actual.append(actual_idx(hg,ag))
    mb=metrics(baseline,actual);mc=metrics(cal,actual);mt=metrics(traj,actual);ab=auc_draw(baseline,actual);ac=auc_draw(cal,actual);at=auc_draw(traj,actual)
    boot=bootstrap_accuracy(baseline,traj,actual,5000,511390)
    gate={'accuracy_better':mt['accuracy']>mb['accuracy'],'log_loss_nonworse':mt['log_loss']<=mb['log_loss']+1e-12,'brier_nonworse':mt['brier']<=mb['brier']+1e-12,'rps_nonworse':mt['rps']<=mb['rps']+1e-12,'draw_auc_better':at is not None and ab is not None and at>ab,'predicted_draw_exists':mt['predicted_draw_count']>=1,'bootstrap_accuracy_p05_positive':boot['p05']>0,'log_loss_nonworse_than_calibration_only':mt['log_loss']<=mc['log_loss']+1e-12};gate['passed']=all(gate.values())
    status='PASS_R39C_TRAJECTORY_CHALLENGER_EXPLORATION_ONLY' if gate['passed'] else 'NO_R39C_TRAJECTORY_INCREMENT_FIXED100_EXPLORATION_ONLY'
    result={'schema_version':pre['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'status':status,'identity_set_sha256':lock_sha,'source_max_datetime':max_dt.isoformat(),'training':{'rows':len(training),'labels_accessed':training_label_access,'draws':int(y.sum()),'holdout_period_labels_used_for_fitting':0},'model_freeze':{'receipt_sha256':hfile(freeze_path),'model_parameter_sha256':model_payload['model_parameter_sha256'],'holdout_labels_accessed_before_freeze':0},'holdout':{'rows':100,'labels_accessed_after_model_freeze':test_label_access,'actual_distribution':dict(Counter(OUTCOMES[y] for y in actual))},'metrics':{'market_baseline':{**mb,'draw_auc':ab},'calibration_only':{**mc,'draw_auc':ac},'trajectory_residual':{**mt,'draw_auc':at}},'paired_bootstrap_accuracy_delta_vs_market':boot,'trajectory_gate':gate,'hard_limits':pre['hard_limits']}
    (a.out_dir/'status.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    with (a.out_dir/'candidate_summary.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.writer(f);w.writerow(['candidate','hits','accuracy','log_loss','brier','rps','predicted_draw_count','actual_draw_count','draw_precision','draw_recall','draw_f1','draw_auc'])
        for name,m in result['metrics'].items():w.writerow([name,m['hits'],m['accuracy'],m['log_loss'],m['brier'],m['rps'],m['predicted_draw_count'],m['actual_draw_count'],m['draw_precision'],m['draw_recall'],m['draw_f1'],m['draw_auc']])
    with (a.out_dir/'fixed100_predictions.csv').open('w',newline='',encoding='utf-8') as f:
        fields=['identity','match_datetime','common_bookies','actual','baseline_home','baseline_draw','baseline_away','baseline_pick','cal_draw','cal_pick','traj_draw','traj_pick'];w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for i,x in enumerate(test_sorted):
            w.writerow({'identity':x['identity'],'match_datetime':x['dt'].isoformat(),'common_bookies':x['common_bookies'],'actual':OUTCOMES[actual[i]],'baseline_home':baseline[i][0],'baseline_draw':baseline[i][1],'baseline_away':baseline[i][2],'baseline_pick':OUTCOMES[int(np.argmax(baseline[i]))],'cal_draw':cal[i][1],'cal_pick':OUTCOMES[int(np.argmax(cal[i]))],'traj_draw':traj[i][1],'traj_pick':OUTCOMES[int(np.argmax(traj[i]))]})
    manifest=[]
    for p in sorted(a.out_dir.iterdir()):
        if p.is_file() and p.name!='manifest.json':manifest.append({'name':p.name,'bytes':p.stat().st_size,'sha256':hfile(p)})
    (a.out_dir/'manifest.json').write_text(json.dumps({'schema':'r39c-manifest','files':manifest},indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
