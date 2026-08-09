#!/usr/bin/env python3
from __future__ import annotations

import argparse,csv,gzip,hashlib,json,math,random,re
from datetime import datetime,timezone
from pathlib import Path
import numpy as np

COL_RE=re.compile(r'^(home|draw|away)_b(\d+)_(\d+)$')
OUTCOMES=('home','draw','away')


def htxt(s:str)->str:return hashlib.sha256(s.encode()).hexdigest()
def hfile(p:Path)->str:
    h=hashlib.sha256()
    with p.open('rb') as f:
        for c in iter(lambda:f.read(1024*1024),b''):h.update(c)
    return h.hexdigest()
def canonical_sha(o)->str:return htxt(json.dumps(o,sort_keys=True,separators=(',',':')))
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
def clip(p,lo=1e-8,hi=.99999999):return min(hi,max(lo,float(p)))
def logit(p):
    p=clip(p);return math.log(p/(1-p))
def entropy(p):return -sum(float(x)*math.log(max(float(x),1e-15)) for x in p)

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
    complete={}
    for h in (24,6,1):
        s=suffix(h);books=[]
        for b in range(1,33):
            vals=[row[mapping[(b,s,o)]] for o in OUTCOMES]
            if all(valid(v) for v in vals):books.append(b)
        complete[h]=set(books)
    common=set.intersection(*(complete[h] for h in (24,6,1)))
    if len(common)<5:return None
    qs={};draw_std={}
    for h in (24,6,1):
        s=suffix(h);per=[]
        for b in sorted(common):per.append(devig(*(row[mapping[(b,s,o)]] for o in OUTCOMES)))
        arr=np.vstack(per);qs[h]=arr.mean(axis=0);draw_std[h]=float(arr[:,1].std(ddof=0))
    q24,q6,q1=qs[24],qs[6],qs[1]
    d246=float(q6[1]-q24[1]);d61=float(q1[1]-q6[1]);gap24=abs(float(q24[0]-q24[2]));gap1=abs(float(q1[0]-q1[2]));e24=entropy(q24);e1=entropy(q1)
    features=[logit(float(q1[1])),d246,d61,d61/5.0-d246/18.0,draw_std[24],draw_std[6],draw_std[1],draw_std[1]-draw_std[24],gap24,gap1,gap1-gap24,e24,e1,e1-e24,math.log1p(len(common))]
    return {'features':features,'q1':q1.tolist(),'common_bookies':len(common)}

def load_feature_rows(source_dir:Path):
    rows={}
    for source in ('odds_series.csv.gz','odds_series_b.csv.gz'):
        p=source_dir/source.replace('.csv.gz','_no_scores.csv.gz')
        with gzip.open(p,'rt',encoding='utf-8-sig',newline='') as f:
            r=csv.reader(f);header=next(r)
            assert header[:3]==['match_id','match_date','match_time']
            assert 'score_home' not in header and 'score_away' not in header
            mapping=parse_mapping(header)
            for row in r:
                if len(row)!=len(header) or not row[0].strip():continue
                feat=feature_from_row(row,mapping)
                if feat is None:continue
                identity=f'{source}|{row[0].strip()}'
                if identity in rows:raise RuntimeError(f'duplicate {identity}')
                feat.update({'identity':identity,'dt':parse_dt(row[1],row[2])});rows[identity]=feat
    return rows

def set_sha(items):return htxt('\n'.join(sorted(x['identity'] for x in items))+'\n')
def recompute_sets(rows,pre):
    start=datetime.fromisoformat(pre['identity_binding']['holdout_start'])
    training=[x for x in rows.values() if x['dt']<start];holdout=[x for x in rows.values() if x['dt']>=start]
    old=sorted(holdout,key=lambda x:htxt(f"{pre['identity_binding']['r39c_consumed_seed']}|{x['identity']}"))[:100]
    assert set_sha(old)==pre['identity_binding']['r39c_consumed_identity_set_sha256']
    old_ids={x['identity'] for x in old}
    remain=[x for x in holdout if x['identity'] not in old_ids]
    test=sorted(remain,key=lambda x:htxt(f"{pre['identity_binding']['r39d_seed']}|{x['identity']}"))[:pre['identity_binding']['r39d_rows']]
    assert set_sha(test)==pre['identity_binding']['r39d_identity_set_sha256']
    assert not (old_ids & {x['identity'] for x in test})
    assert len(training)==pre['identity_binding']['training_eligible_rows'] and len(holdout)==pre['identity_binding']['holdout_eligible_rows']
    return sorted(training,key=lambda x:(x['dt'],x['identity'])),old,test,start

def read_training_labels(original_dir,training_ids,holdout_start):
    labels={};accessed=0;holdout_access=0
    for source in ('odds_series.csv.gz','odds_series_b.csv.gz'):
        with gzip.open(original_dir/source,'rt',encoding='utf-8-sig',newline='') as f:
            r=csv.reader(f);h=next(r);assert h[:5]==['match_id','match_date','match_time','score_home','score_away']
            for row in r:
                if len(row)<5:continue
                dt=parse_dt(row[1],row[2]);identity=f'{source}|{row[0].strip()}'
                if dt>=holdout_start:continue
                if identity not in training_ids:continue
                labels[identity]=(int(float(row[3])),int(float(row[4])));accessed+=1
    return labels,accessed,holdout_access

def read_r39d_labels_after_freeze(original_dir,test_ids,r39c_ids):
    labels={};accessed=0;r39c_access=0
    for source in ('odds_series.csv.gz','odds_series_b.csv.gz'):
        with gzip.open(original_dir/source,'rt',encoding='utf-8-sig',newline='') as f:
            r=csv.reader(f);h=next(r);assert h[:5]==['match_id','match_date','match_time','score_home','score_away']
            for row in r:
                if len(row)<5:continue
                identity=f'{source}|{row[0].strip()}'
                if identity in r39c_ids:
                    continue
                if identity not in test_ids:continue
                labels[identity]=(int(float(row[3])),int(float(row[4])));accessed+=1
    return labels,accessed,r39c_access

def standardize_fit(X):
    mean=X.mean(axis=0);std=X.std(axis=0);std=np.where(std<1e-12,1.0,std);return (X-mean)/std,mean,std

def fit_logistic(X,y,l2=1.0,max_iter=50,tol=1e-8):
    Z=np.column_stack([np.ones(len(X)),X]);beta=np.zeros(Z.shape[1]);pen=np.zeros_like(beta);pen[1:]=l2;converged=False;delta_max=None
    for it in range(1,max_iter+1):
        eta=Z@beta;p=1/(1+np.exp(-np.clip(eta,-40,40)));w=np.clip(p*(1-p),1e-8,None)
        grad=Z.T@(p-y)+pen*beta;H=Z.T@(Z*w[:,None])+np.diag(pen)+np.eye(Z.shape[1])*1e-10
        delta=np.linalg.solve(H,grad);beta-=delta;delta_max=float(np.max(np.abs(delta)))
        if delta_max<tol:converged=True;break
    eta=Z@beta;p=1/(1+np.exp(-np.clip(eta,-40,40)));grad=Z.T@(p-y)+pen*beta
    return beta,{'iterations':it,'converged':converged,'coefficient_delta_max':delta_max,'gradient_abs_max':float(np.max(np.abs(grad)))}
def predict_logistic(X,beta):
    Z=np.column_stack([np.ones(len(X)),X]);return 1/(1+np.exp(-np.clip(Z@beta,-40,40)))
def threeway(q1,pd):
    pd=clip(pd);h,a=float(q1[0]),float(q1[2]);s=h+a;return np.array([(1-pd)*h/s,pd,(1-pd)*a/s],dtype=float)
def actual_idx(hg,ag):return 0 if hg>ag else 1 if hg==ag else 2

def probability_metrics(probs,actual):
    ll=brier=rps=0.0
    for p,y in zip(probs,actual):
        ll-=math.log(max(float(p[y]),1e-15));one=np.zeros(3);one[y]=1;brier+=float(((p-one)**2).sum());rps+=0.5*((float(p[0])-float(one[0]))**2+((float(p[0]+p[1]))-float(one[0]+one[1]))**2)
    n=len(actual);return {'rows':n,'log_loss':ll/n,'brier':brier/n,'rps':rps/n}
def decision_metrics(pred,actual):
    n=len(actual);hits=sum(int(p==y) for p,y in zip(pred,actual));draw_pred=sum(p==1 for p in pred);actual_draw=sum(y==1 for y in actual);tp=sum(p==1 and y==1 for p,y in zip(pred,actual));prec=tp/draw_pred if draw_pred else 0.0;rec=tp/actual_draw if actual_draw else 0.0;f1=2*prec*rec/(prec+rec) if prec+rec else 0.0
    return {'rows':n,'hits':hits,'accuracy':hits/n,'predicted_draw_count':draw_pred,'actual_draw_count':actual_draw,'draw_true_positive':tp,'draw_precision':prec,'draw_recall':rec,'draw_f1':f1}
def auc_draw(probs,actual):
    pos=[float(p[1]) for p,y in zip(probs,actual) if y==1];neg=[float(p[1]) for p,y in zip(probs,actual) if y!=1]
    if not pos or not neg:return None
    wins=0.0
    for a in pos:
        for b in neg:wins+=1 if a>b else 0.5 if a==b else 0
    return wins/(len(pos)*len(neg))
def market_pred(q):return int(np.argmax(np.asarray(q,dtype=float)))
def decision_score(p):return float(p[1]-max(p[0],p[2]))
def apply_boundary(p,threshold):
    natural=int(np.argmax(p))
    if natural==1:return 1
    if decision_score(p)>=threshold:return 1
    return 0 if float(p[0])>=float(p[2]) else 2

def select_policy_lane(policy_rows,policy_probs,policy_actual,market_preds,coverages,pre):
    n=len(policy_rows);scores=[decision_score(p) for p in policy_probs];base=decision_metrics(market_preds,policy_actual);draw_prev=sum(y==1 for y in policy_actual)/n;cands=[]
    ranked=sorted(range(n),key=lambda i:(-scores[i],policy_rows[i]['identity']))
    for cov in coverages:
        k=max(1,min(n,int(math.ceil(float(cov)*n))));selected=set(ranked[:k]);threshold=min(scores[i] for i in selected)
        pred=[]
        for i,p in enumerate(policy_probs):pred.append(1 if i in selected else (1 if int(np.argmax(p))==1 else (0 if float(p[0])>=float(p[2]) else 2)))
        m=decision_metrics(pred,policy_actual);eligible=m['accuracy']>base['accuracy'] and m['draw_f1']>0 and m['draw_precision']>=draw_prev+float(pre['policy_selection']['eligible_lane_requirements']['draw_precision_at_least_policy_draw_prevalence_plus'])
        cands.append({'target_coverage':float(cov),'selected_rows':k,'score_threshold':threshold,'metrics':m,'eligible':eligible})
    elig=[c for c in cands if c['eligible']]
    if not elig:return None,cands,base,draw_prev
    chosen=sorted(elig,key=lambda c:(-c['metrics']['accuracy'],-c['metrics']['draw_f1'],-c['metrics']['draw_precision'],c['target_coverage']))[0]
    return chosen,cands,base,draw_prev

def bootstrap_accuracy(base_pred,cand_pred,actual,n,seed):
    rnd=random.Random(seed);N=len(actual);vals=[]
    for _ in range(n):
        s=0
        for _j in range(N):
            i=rnd.randrange(N);s+=int(cand_pred[i]==actual[i])-int(base_pred[i]==actual[i])
        vals.append(s/N)
    vals.sort();q=lambda x:vals[min(len(vals)-1,max(0,int(x*(len(vals)-1))))]
    return {'samples':n,'seed':seed,'mean':sum(vals)/n,'p05':q(.05),'p50':q(.50),'p95':q(.95)}

def self_test():
    q=devig(2,4,4);assert abs(float(q.sum())-1)<1e-12
    X=np.array([[-2.],[-1.],[1.],[2.]],dtype=float);y=np.array([0.,0.,1.,1.]);Xs,mean,std=standardize_fit(X);b,d=fit_logistic(Xs,y);p=predict_logistic(Xs,b);assert d['converged'] and p[0]<p[-1]
    pp=[np.array([.4,.35,.25]),np.array([.45,.30,.25])];assert decision_score(pp[0])>-1 and apply_boundary(pp[0],-.1)==1
    print('PASS_R39D_SYNTHETIC_SELF_TEST')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--prereg',type=Path);ap.add_argument('--sanitized-dir',type=Path);ap.add_argument('--original-dir',type=Path);ap.add_argument('--out-dir',type=Path);ap.add_argument('--self-test',action='store_true');a=ap.parse_args()
    if a.self_test:self_test();return
    if not all((a.prereg,a.sanitized_dir,a.original_dir,a.out_dir)):raise SystemExit('missing required args')
    a.out_dir.mkdir(parents=True,exist_ok=True);pre=json.loads(a.prereg.read_text());rows=load_feature_rows(a.sanitized_dir);training,r39c,test,start=recompute_sets(rows,pre)
    training_ids={x['identity'] for x in training};labels,train_access,holdout_before=read_training_labels(a.original_dir,training_ids,start);assert holdout_before==0 and len(labels)==len(training)
    split=int(math.floor(len(training)*float(pre['chronological_policy_design']['fit_fraction'])));fit_rows=training[:split];policy_rows=training[split:];assert fit_rows and policy_rows and max(x['dt'] for x in fit_rows)<=min(x['dt'] for x in policy_rows)
    Xfit=np.array([x['features'] for x in fit_rows]);yfit=np.array([1 if labels[x['identity']][0]==labels[x['identity']][1] else 0 for x in fit_rows],dtype=float);Xs,mean,std=standardize_fit(Xfit);beta,diag=fit_logistic(Xs,yfit,l2=float(pre['probability_model']['l2_lambda']),max_iter=int(pre['probability_model']['max_iterations']),tol=float(pre['probability_model']['tolerance']));assert diag['converged']
    Xp=(np.array([x['features'] for x in policy_rows])-mean)/std;pd=predict_logistic(Xp,beta);policy_probs=[threeway(x['q1'],pd[i]) for i,x in enumerate(policy_rows)];policy_actual=[actual_idx(*labels[x['identity']]) for x in policy_rows];policy_market=[market_pred(x['q1']) for x in policy_rows]
    chosen,cands,policy_base,policy_prev=select_policy_lane(policy_rows,policy_probs,policy_actual,policy_market,pre['candidate_target_coverages'],pre)
    common={'schema_version':pre['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'r39d_identity_set_sha256':pre['identity_binding']['r39d_identity_set_sha256'],'r39c_identity_set_sha256':pre['identity_binding']['r39c_consumed_identity_set_sha256'],'training_labels_accessed':train_access,'holdout_period_labels_accessed_before_freeze':0,'r39c_labels_accessed':0,'fit_rows':len(fit_rows),'policy_rows':len(policy_rows),'fit_draws':int(yfit.sum()),'policy_draw_prevalence':policy_prev,'policy_market_metrics':policy_base,'policy_candidates':cands,'model_diagnostics':diag,'hard_limits':pre['hard_limits']}
    if chosen is None:
        common['status']='STOP_R39D_BEFORE_HOLDOUT_LABEL_ACCESS_NO_TRAINING_POLICY_LANE';common['r39d_holdout_labels_accessed']=0;(a.out_dir/'r39d_result.json').write_text(json.dumps(common,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(common,ensure_ascii=False,indent=2));return
    freeze={'schema':'r39d-model-policy-freeze','preregistration_sha256':hfile(a.prereg),'r39d_identity_set_sha256':pre['identity_binding']['r39d_identity_set_sha256'],'fit_rows':len(fit_rows),'policy_rows':len(policy_rows),'feature_names':pre['features'],'fit_mean':mean.tolist(),'fit_std':std.tolist(),'trajectory_beta':beta.tolist(),'model_diagnostics':diag,'selected_policy':chosen,'holdout_period_labels_accessed_before_freeze':0,'r39c_labels_accessed':0,'numpy_version':np.__version__}
    freeze['model_policy_parameter_sha256']=canonical_sha(freeze);fp=a.out_dir/'model_policy_freeze_receipt_r39d.json';fp.write_text(json.dumps(freeze,ensure_ascii=False,indent=2),encoding='utf-8');freeze_file_sha=hfile(fp);assert fp.exists() and fp.stat().st_size>0
    test_ids={x['identity'] for x in test};r39c_ids={x['identity'] for x in r39c};test_labels,test_access,r39c_access=read_r39d_labels_after_freeze(a.original_dir,test_ids,r39c_ids);assert test_access==100 and len(test_labels)==100 and r39c_access==0
    test_rows=sorted(test,key=lambda x:(x['dt'],x['identity']));Xt=(np.array([x['features'] for x in test_rows])-mean)/std;pdt=predict_logistic(Xt,beta);traj=[threeway(x['q1'],pdt[i]) for i,x in enumerate(test_rows)];base=[np.array(x['q1'],dtype=float) for x in test_rows];actual=[actual_idx(*test_labels[x['identity']]) for x in test_rows];base_pred=[market_pred(x['q1']) for x in test_rows];cand_pred=[apply_boundary(p,float(chosen['score_threshold'])) for p in traj]
    pb=probability_metrics(base,actual);pt=probability_metrics(traj,actual);db=decision_metrics(base_pred,actual);dc=decision_metrics(cand_pred,actual);ab=auc_draw(base,actual);at=auc_draw(traj,actual);boot=bootstrap_accuracy(base_pred,cand_pred,actual,int(pre['holdout_evaluation']['paired_bootstrap']['samples']),int(pre['holdout_evaluation']['paired_bootstrap']['seed']));g=pre['holdout_evaluation']['exploratory_pass_gate_all_required']
    gates={'decision_accuracy_strictly_better_than_market':dc['accuracy']>db['accuracy'],'decision_draw_precision_min':dc['draw_precision']>=float(g['decision_draw_precision_min']),'decision_draw_recall_min':dc['draw_recall']>=float(g['decision_draw_recall_min']),'decision_draw_f1_min':dc['draw_f1']>=float(g['decision_draw_f1_min']),'decision_predicted_draw_count_min':dc['predicted_draw_count']>=int(g['decision_predicted_draw_count_min']),'decision_predicted_draw_count_max':dc['predicted_draw_count']<=int(g['decision_predicted_draw_count_max']),'trajectory_log_loss_nonworse_than_market':pt['log_loss']<=pb['log_loss']+1e-12,'trajectory_brier_nonworse_than_market':pt['brier']<=pb['brier']+1e-12,'trajectory_rps_nonworse_than_market':pt['rps']<=pb['rps']+1e-12,'trajectory_draw_auc_strictly_better_than_market':at is not None and ab is not None and at>ab};gates['passed']=all(gates.values())
    status='PASS_R39D_LEARNED_DRAW_BOUNDARY_EXPLORATORY_REQUIRES_NEW_CONFIRMATION' if gates['passed'] else 'NO_R39D_LEARNED_DRAW_BOUNDARY_INCREMENT_FIXED100'
    result={**common,'status':status,'selected_policy':chosen,'model_policy_freeze_file_sha256':freeze_file_sha,'model_policy_parameter_sha256':freeze['model_policy_parameter_sha256'],'r39d_holdout_labels_accessed':test_access,'r39c_labels_accessed':r39c_access,'actual_hda':{'home':sum(y==0 for y in actual),'draw':sum(y==1 for y in actual),'away':sum(y==2 for y in actual)},'market_probability_metrics':pb,'trajectory_probability_metrics':pt,'market_decision_metrics':db,'r39d_decision_metrics':dc,'market_draw_auc':ab,'trajectory_draw_auc':at,'paired_bootstrap_accuracy_delta':boot,'gate':gates}
    (a.out_dir/'r39d_result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
