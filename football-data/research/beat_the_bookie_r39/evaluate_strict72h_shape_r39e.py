#!/usr/bin/env python3
from __future__ import annotations

import argparse,csv,gzip,hashlib,json,math,random,re
from datetime import datetime,timezone
from pathlib import Path
import numpy as np

COL_RE=re.compile(r'^(home|draw|away)_b(\d+)_(\d+)$')
OUTCOMES=('home','draw','away')


def htxt(s):return hashlib.sha256(s.encode()).hexdigest()
def hfile(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for c in iter(lambda:f.read(1024*1024),b''):h.update(c)
    return h.hexdigest()
def canonical_sha(o):return htxt(json.dumps(o,sort_keys=True,separators=(',',':')))
def parse_dt(d,t):
    for fmt in ('%Y-%m-%d %H:%M:%S','%Y-%m-%d %H:%M'):
        try:return datetime.strptime(f'{d} {t}',fmt)
        except ValueError:pass
    raise ValueError(f'bad datetime {d} {t}')
def valid(v):
    s=v.strip()
    if not s:return False
    try:x=float(s)
    except ValueError:return False
    return math.isfinite(x) and x>1.0
def clip(p,lo=1e-8,hi=.99999999):return min(hi,max(lo,float(p)))
def logit(p):p=clip(p);return math.log(p/(1-p))
def entropy(q):return -sum(float(x)*math.log(max(float(x),1e-15)) for x in q)
def devig(h,d,a):
    q=np.array([1/float(h),1/float(d),1/float(a)],dtype=float);q/=q.sum();return q
def parse_mapping(header):
    m={}
    for i,name in enumerate(header[3:],3):
        g=COL_RE.match(name)
        if g:m[(int(g.group(2)),int(g.group(3)),g.group(1))]=i
    if len(m)!=32*72*3:raise RuntimeError(f'odds mapping {len(m)}')
    return m
def book_valid(row,m,b,h):return valid(row[m[(b,h,'home')]]) and valid(row[m[(b,h,'draw')]]) and valid(row[m[(b,h,'away')]])
def valid_books(row,m,h):return {b for b in range(1,33) if book_valid(row,m,b,h)}
def base3_ok(row,m):return len(set.intersection(*(valid_books(row,m,h) for h in (47,65,70))))>=5
def strict_common(row,m):
    common=list(range(1,33))
    for h in range(72):
        common=[b for b in common if book_valid(row,m,b,h)]
        if len(common)<5:return None
    return common
def make_features(row,m,common):
    qs=[];dst=[];gap=[];ent=[]
    for h in range(72):
        arr=np.vstack([devig(*(row[m[(b,h,o)]] for o in OUTCOMES)) for b in common]);q=arr.mean(axis=0);qs.append(q);dst.append(float(arr[:,1].std(ddof=0)));gap.append(float(q[0]-q[2]));ent.append(entropy(q))
    draw=np.array([logit(float(q[1])) for q in qs]);signals=[draw,np.array(dst),np.array(gap),np.array(ent)];features=[];t=np.arange(6,dtype=float);tc=t-t.mean();den=float((tc*tc).sum())
    for block in range(12):
        sl=slice(block*6,(block+1)*6)
        for sig in signals:
            y=sig[sl];features.append(float(y.mean()));features.append(float((tc*(y-y.mean())).sum()/den))
    diff=np.diff(draw);nz=np.sign(diff);nz=nz[nz!=0];sign_changes=int(np.sum(nz[1:]!=nz[:-1])) if len(nz)>1 else 0;ext=int(np.sum(diff[:-1]*diff[1:]<0)) if len(diff)>1 else 0
    features += [float(draw[-1]-draw[0]),float(np.max(np.abs(diff))),float(np.std(diff)),sign_changes/71.0,ext/70.0,float(np.argmin(draw))/71.0,float(np.argmax(draw))/71.0,math.log1p(len(common))]
    if len(features)!=104:raise RuntimeError(len(features))
    q24,q6,q1=qs[47],qs[65],qs[70];d246=float(q6[1]-q24[1]);d61=float(q1[1]-q6[1]);g24=abs(float(q24[0]-q24[2]));g1=abs(float(q1[0]-q1[2]));e24=entropy(q24);e1=entropy(q1);f15=[logit(float(q1[1])),d246,d61,d61/5-d246/18,dst[47],dst[65],dst[70],dst[70]-dst[47],g24,g1,g1-g24,e24,e1,e1-e24,math.log1p(len(common))]
    return {'features104':features,'features15':f15,'q1':q1.tolist(),'strict_common_books':len(common)}
def load_rows(source_dir,start):
    rows={};base_hold=[]
    for sanitized in ('odds_series_no_scores.csv.gz','odds_series_b_no_scores.csv.gz'):
        raw=sanitized.replace('_no_scores','')
        with gzip.open(source_dir/sanitized,'rt',encoding='utf-8-sig',newline='') as f:
            r=csv.reader(f);h=next(r);assert h[:3]==['match_id','match_date','match_time'];assert 'score_home' not in h and 'score_away' not in h;m=parse_mapping(h)
            for row in r:
                if len(row)!=len(h) or not row[0].strip():continue
                dt=parse_dt(row[1],row[2]);ident=f'{raw}|{row[0].strip()}'
                if dt>=start and base3_ok(row,m):base_hold.append({'identity':ident,'dt':dt})
                common=strict_common(row,m)
                if common is None:continue
                info=make_features(row,m,common);info.update({'identity':ident,'dt':dt});rows[ident]=info
    return rows,base_hold
def set_sha(items):return htxt('\n'.join(sorted(x['identity'] for x in items))+'\n')
def reconstruct(rows,base_hold,pre):
    b=pre['identity_binding'];r39c=sorted(base_hold,key=lambda x:htxt(f"{b['r39c_seed']}|{x['identity']}"))[:100];assert set_sha(r39c)==b['r39c_sha256'];cids={x['identity'] for x in r39c};rem=[x for x in base_hold if x['identity'] not in cids];r39d=sorted(rem,key=lambda x:htxt(f"{b['r39d_seed']}|{x['identity']}"))[:100];assert set_sha(r39d)==b['r39d_sha256'];dids={x['identity'] for x in r39d};assert not cids&dids
    start=datetime.fromisoformat(b['holdout_start']);training=[x for x in rows.values() if x['dt']<start];hold=[x for x in rows.values() if x['dt']>=start and x['identity'] not in cids and x['identity'] not in dids];test=sorted(hold,key=lambda x:htxt(f"{b['r39e_seed']}|{x['identity']}"))[:100];assert set_sha(test)==b['r39e_sha256'];return sorted(training,key=lambda x:(x['dt'],x['identity'])),sorted(test,key=lambda x:(x['dt'],x['identity'])),start,cids|dids
def read_training_labels(original_dir,ids,start):
    labels={};access=0
    for src in ('odds_series.csv.gz','odds_series_b.csv.gz'):
        with gzip.open(original_dir/src,'rt',encoding='utf-8-sig',newline='') as f:
            r=csv.reader(f);h=next(r);assert h[:5]==['match_id','match_date','match_time','score_home','score_away']
            for row in r:
                if len(row)<5:continue
                dt=parse_dt(row[1],row[2]);ident=f'{src}|{row[0].strip()}'
                if dt>=start or ident not in ids:continue
                labels[ident]=(int(float(row[3])),int(float(row[4])));access+=1
    return labels,access
def read_test_labels(original_dir,test_ids,consumed):
    labels={};access=0
    for src in ('odds_series.csv.gz','odds_series_b.csv.gz'):
        with gzip.open(original_dir/src,'rt',encoding='utf-8-sig',newline='') as f:
            r=csv.reader(f);next(r)
            for row in r:
                if len(row)<5:continue
                ident=f'{src}|{row[0].strip()}'
                if ident in consumed or ident not in test_ids:continue
                labels[ident]=(int(float(row[3])),int(float(row[4])));access+=1
    return labels,access
def actual_idx(h,a):return 0 if h>a else 1 if h==a else 2
def standardize(X):mean=X.mean(0);std=X.std(0);std=np.where(std<1e-12,1.,std);return (X-mean)/std,mean,std
def fit_logistic(X,y,l2,max_iter=40,tol=1e-7):
    Z=np.column_stack([np.ones(len(X)),X]);beta=np.zeros(Z.shape[1]);pen=np.zeros_like(beta);pen[1:]=l2;conv=False;delta=np.ones_like(beta)
    for it in range(1,max_iter+1):
        eta=Z@beta;p=1/(1+np.exp(-np.clip(eta,-40,40)));w=np.clip(p*(1-p),1e-8,None);grad=Z.T@(p-y)+pen*beta;H=Z.T@(Z*w[:,None])+np.diag(pen)+np.eye(Z.shape[1])*1e-9;delta=np.linalg.solve(H,grad);beta-=delta
        if float(np.max(np.abs(delta)))<tol:conv=True;break
    return beta,{'iterations':it,'converged':conv,'delta_max':float(np.max(np.abs(delta)))}
def predict(X,b):Z=np.column_stack([np.ones(len(X)),X]);return 1/(1+np.exp(-np.clip(Z@b,-40,40)))
def threeway(q,pd):pd=clip(pd);h,a=float(q[0]),float(q[2]);s=h+a;return np.array([(1-pd)*h/s,pd,(1-pd)*a/s])
def prob_metrics(probs,actual):
    ll=br=rps=0.
    for p,y in zip(probs,actual):
        one=np.zeros(3);one[y]=1;ll-=math.log(max(float(p[y]),1e-15));br+=float(((p-one)**2).sum());rps+=.5*((float(p[0]-one[0]))**2+(float(p[0]+p[1]-one[0]-one[1]))**2)
    n=len(actual);return {'log_loss':ll/n,'brier':br/n,'rps':rps/n}
def binary_logloss(pd,y):return float(np.mean(-(y*np.log(np.clip(pd,1e-15,1))+(1-y)*np.log(np.clip(1-pd,1e-15,1)))))
def auc(pd,y):
    pos=[float(p) for p,z in zip(pd,y) if z==1];neg=[float(p) for p,z in zip(pd,y) if z==0]
    if not pos or not neg:return None
    w=0.
    for a in pos:
        for b in neg:w+=1 if a>b else .5 if a==b else 0
    return w/(len(pos)*len(neg))
def decision_metrics(pred,actual):
    n=len(actual);hits=sum(p==y for p,y in zip(pred,actual));dp=sum(p==1 for p in pred);ad=sum(y==1 for y in actual);tp=sum(p==1 and y==1 for p,y in zip(pred,actual));pr=tp/dp if dp else 0.;rc=tp/ad if ad else 0.;f1=2*pr*rc/(pr+rc) if pr+rc else 0.;return {'accuracy':hits/n,'hits':hits,'predicted_draw_count':dp,'actual_draw_count':ad,'draw_tp':tp,'draw_precision':pr,'draw_recall':rc,'draw_f1':f1}
def dscore(p):return float(p[1]-max(p[0],p[2]))
def select_policy(rows,probs,actual,market,pre):
    base=decision_metrics(market,actual);prev=sum(y==1 for y in actual)/len(actual);scores=[dscore(p) for p in probs];rank=sorted(range(len(rows)),key=lambda i:(-scores[i],rows[i]['identity']));cands=[]
    for cov in pre['policy']['coverages']:
        k=max(1,int(math.ceil(cov*len(rows))));sel=set(rank[:k]);pred=[1 if i in sel else (0 if p[0]>=p[2] else 2) for i,p in enumerate(probs)];m=decision_metrics(pred,actual);ok=m['accuracy']>base['accuracy'] and m['draw_precision']>=prev+pre['policy']['precision_over_prevalence_min'] and m['draw_f1']>=pre['policy']['draw_f1_min'];cands.append({'coverage':cov,'k':k,'threshold':min(scores[i] for i in sel),'metrics':m,'eligible':ok})
    good=[c for c in cands if c['eligible']];chosen=sorted(good,key=lambda c:(-c['metrics']['accuracy'],-c['metrics']['draw_f1'],-c['metrics']['draw_precision'],c['coverage']))[0] if good else None;return chosen,cands,base,prev
def apply(p,thr):return 1 if dscore(p)>=thr else (0 if p[0]>=p[2] else 2)
def bootstrap(base,cand,actual,n,seed):
    rnd=random.Random(seed);N=len(actual);v=[]
    for _ in range(n):
        s=0
        for _j in range(N):
            i=rnd.randrange(N);s+=int(cand[i]==actual[i])-int(base[i]==actual[i])
        v.append(s/N)
    v.sort();q=lambda x:v[min(len(v)-1,max(0,int(x*(len(v)-1))))];return {'mean':sum(v)/n,'p05':q(.05),'p50':q(.5),'p95':q(.95),'samples':n,'seed':seed}
def self_test():
    x=np.linspace(-2,2,20)[:,None];y=(x[:,0]>0).astype(float);xs,_,_=standardize(x);b,d=fit_logistic(xs,y,1.);assert d['converged'] and predict(xs,b)[0]<predict(xs,b)[-1];print('PASS_R39E_STRICT72_SELF_TEST')
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--prereg',type=Path);ap.add_argument('--sanitized-dir',type=Path);ap.add_argument('--original-dir',type=Path);ap.add_argument('--out-dir',type=Path);ap.add_argument('--self-test',action='store_true');a=ap.parse_args()
    if a.self_test:self_test();return
    pre=json.loads(a.prereg.read_text());assert pre['selected_coverage_lane']=='STRICT_COMMON5_ALL72';a.out_dir.mkdir(parents=True,exist_ok=True);start=datetime.fromisoformat(pre['identity_binding']['holdout_start']);rows,base_hold=load_rows(a.sanitized_dir,start);training,test,start,consumed=reconstruct(rows,base_hold,pre);labels,train_access=read_training_labels(a.original_dir,{x['identity'] for x in training},start);assert len(labels)==len(training)
    n=len(training);i1=int(math.floor(n*.70));i2=int(math.floor(n*.85));fit,val,policy=training[:i1],training[i1:i2],training[i2:];assert fit and val and policy
    yfit=np.array([labels[x['identity']][0]==labels[x['identity']][1] for x in fit],float);yval=np.array([labels[x['identity']][0]==labels[x['identity']][1] for x in val],float)
    X=np.array([x['features104'] for x in fit]);Xs,mean,std=standardize(X);Xv=(np.array([x['features104'] for x in val])-mean)/std;models=[];actv=[actual_idx(*labels[x['identity']]) for x in val]
    for l2 in pre['model']['l2_candidates']:
        b,d=fit_logistic(Xs,yfit,float(l2));assert d['converged'];pv=predict(Xv,b);p3=[threeway(x['q1'],pv[i]) for i,x in enumerate(val)];pm=prob_metrics(p3,actv);models.append({'l2':l2,'beta':b,'diag':d,'val_binary_logloss':binary_logloss(pv,yval),'val_draw_auc':auc(pv,yval),'val_hda_logloss':pm['log_loss']})
    chosen=sorted(models,key=lambda z:(z['val_hda_logloss'],z['val_binary_logloss'],z['l2']))[0]
    X15=np.array([x['features15'] for x in fit]);X15s,m15,s15=standardize(X15);b15,d15=fit_logistic(X15s,yfit,1.);assert d15['converged'];pv15=predict((np.array([x['features15'] for x in val])-m15)/s15,b15);p315=[threeway(x['q1'],pv15[i]) for i,x in enumerate(val)];m15v=prob_metrics(p315,actv);marketv=[np.array(x['q1']) for x in val];mmv=prob_metrics(marketv,actv);benchmark={'market_hda_logloss':mmv['log_loss'],'benchmark15_hda_logloss':m15v['log_loss'],'benchmark15_draw_auc':auc(pv15,yval)};full={'hda_logloss':chosen['val_hda_logloss'],'draw_auc':chosen['val_draw_auc']};model_gate=full['hda_logloss']<benchmark['market_hda_logloss'] and full['hda_logloss']<benchmark['benchmark15_hda_logloss'] and full['draw_auc']>benchmark['benchmark15_draw_auc']
    common={'schema_version':pre['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'selected_coverage_lane':'STRICT_COMMON5_ALL72','r39e_identity_set_sha256':pre['identity_binding']['r39e_sha256'],'strict_lane_rows':len(rows),'training_rows':len(training),'training_labels_accessed':train_access,'holdout_labels_accessed_before_freeze':0,'consumed_holdout_labels_accessed':0,'fit_rows':len(fit),'validation_rows':len(val),'policy_rows':len(policy),'model_candidates':[{'l2':z['l2'],'diag':z['diag'],'val_binary_logloss':z['val_binary_logloss'],'val_draw_auc':z['val_draw_auc'],'val_hda_logloss':z['val_hda_logloss']} for z in models],'selected_l2':chosen['l2'],'validation_full72':full,'validation_benchmarks':benchmark,'validation_model_gate_passed':model_gate,'hard_limits':pre['hard_limits']}
    if not model_gate:
        common['status']='STOP_R39E_BEFORE_HOLDOUT_LABEL_ACCESS_FULL72H_MODEL_NO_INCREMENT';common['r39e_holdout_labels_accessed']=0;(a.out_dir/'r39e_result.json').write_text(json.dumps(common,ensure_ascii=False,indent=2));print(json.dumps(common,ensure_ascii=False,indent=2));return
    Xp=(np.array([x['features104'] for x in policy])-mean)/std;ppd=predict(Xp,chosen['beta']);pp=[threeway(x['q1'],ppd[i]) for i,x in enumerate(policy)];actp=[actual_idx(*labels[x['identity']]) for x in policy];marketp=[int(np.argmax(x['q1'])) for x in policy];pol,cands,pbase,prev=select_policy(policy,pp,actp,marketp,pre);common.update({'policy_draw_prevalence':prev,'policy_market':pbase,'policy_candidates':cands})
    if pol is None:
        common['status']='STOP_R39E_BEFORE_HOLDOUT_LABEL_ACCESS_NO_POLICY_LANE';common['r39e_holdout_labels_accessed']=0;(a.out_dir/'r39e_result.json').write_text(json.dumps(common,ensure_ascii=False,indent=2));print(json.dumps(common,ensure_ascii=False,indent=2));return
    freeze={'schema':'r39e-strict72-model-policy-freeze','prereg_sha256':hfile(a.prereg),'r39e_identity_set_sha256':pre['identity_binding']['r39e_sha256'],'feature_count':104,'fit_mean':mean.tolist(),'fit_std':std.tolist(),'selected_l2':chosen['l2'],'beta':chosen['beta'].tolist(),'selected_policy':pol,'validation_full72':full,'validation_benchmarks':benchmark,'holdout_labels_accessed_before_freeze':0,'consumed_holdout_labels_accessed':0};freeze['parameter_sha256']=canonical_sha(freeze);fp=a.out_dir/'model_policy_freeze_receipt_r39e.json';fp.write_text(json.dumps(freeze,ensure_ascii=False,indent=2));freeze_file_sha=hfile(fp)
    tl,ta=read_test_labels(a.original_dir,{x['identity'] for x in test},consumed);assert ta==100 and len(tl)==100;Xt=(np.array([x['features104'] for x in test])-mean)/std;pd=predict(Xt,chosen['beta']);fullp=[threeway(x['q1'],pd[i]) for i,x in enumerate(test)];market=[np.array(x['q1']) for x in test];actual=[actual_idx(*tl[x['identity']]) for x in test];mpred=[int(np.argmax(p)) for p in market];cpred=[apply(p,pol['threshold']) for p in fullp];dm=decision_metrics(mpred,actual);dc=decision_metrics(cpred,actual);pm=prob_metrics(market,actual);pf=prob_metrics(fullp,actual);am=auc([p[1] for p in market],np.array([y==1 for y in actual],float));af=auc(pd,np.array([y==1 for y in actual],float));bt=bootstrap(mpred,cpred,actual,5000,pre['holdout']['bootstrap_seed']);g=pre['holdout']['gate'];gate={'accuracy_better':dc['accuracy']>dm['accuracy'],'precision_min':dc['draw_precision']>=g['draw_precision_min'],'recall_min':dc['draw_recall']>=g['draw_recall_min'],'f1_min':dc['draw_f1']>=g['draw_f1_min'],'predicted_draw_range':g['predicted_draw_min']<=dc['predicted_draw_count']<=g['predicted_draw_max'],'logloss_nonworse':pf['log_loss']<=pm['log_loss'],'brier_nonworse':pf['brier']<=pm['brier'],'rps_nonworse':pf['rps']<=pm['rps'],'draw_auc_better':af>am};gate['passed']=all(gate.values());common.update({'status':'PASS_R39E_FULL72H_EXPLORATORY_REQUIRES_NEW_CONFIRMATION' if gate['passed'] else 'NO_R39E_FULL72H_INCREMENT_FIXED100','selected_policy':pol,'freeze_file_sha256':freeze_file_sha,'freeze_parameter_sha256':freeze['parameter_sha256'],'r39e_holdout_labels_accessed':ta,'consumed_holdout_labels_accessed':0,'actual_hda':{'home':sum(y==0 for y in actual),'draw':sum(y==1 for y in actual),'away':sum(y==2 for y in actual)},'market_decision':dm,'r39e_decision':dc,'market_probability':pm,'r39e_probability':pf,'market_draw_auc':am,'r39e_draw_auc':af,'bootstrap_accuracy_delta':bt,'gate':gate});(a.out_dir/'r39e_result.json').write_text(json.dumps(common,ensure_ascii=False,indent=2));print(json.dumps(common,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
