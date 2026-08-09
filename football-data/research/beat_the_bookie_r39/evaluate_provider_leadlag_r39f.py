#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,gzip,hashlib,json,math,random,re
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
COL_RE=re.compile(r'^(home|draw|away)_b(\d+)_(\d+)$');OUTCOMES=('home','draw','away');CUT_IDX={24:47,6:65,1:70}
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
 s=v.strip().casefold()
 if not s or s in {'nan','na','null','none'}:return False
 try:x=float(s)
 except ValueError:return False
 return math.isfinite(x) and x>1.0
def clip(p,lo=1e-8,hi=.99999999):return min(hi,max(lo,float(p)))
def logit(p):p=clip(p);return math.log(p/(1-p))
def entropy(q):return -sum(float(x)*math.log(max(float(x),1e-15)) for x in q)
def devig3(vals):
 inv=np.array([1/float(x) for x in vals],dtype=float);inv/=inv.sum();return inv
def parse_mapping(header):
 m={}
 for i,name in enumerate(header[3:],3):
  x=COL_RE.match(name)
  if x:m[(int(x.group(2)),int(x.group(3)),x.group(1))]=i
 if len(m)!=32*72*3:raise RuntimeError(f'odds mapping {len(m)}')
 return m
def set_sha(items):return htxt('\n'.join(sorted(x['identity'] for x in items))+'\n')
def provider_all72(row,m,b):
 for h in range(72):
  if not(valid(row[m[(b,h,'home')]]) and valid(row[m[(b,h,'draw')]]) and valid(row[m[(b,h,'away')]])):return False
 return True
def base3_ok(row,m):
 sets=[]
 for idx in CUT_IDX.values():
  books=set()
  for b in range(1,33):
   if valid(row[m[(b,idx,'home')]]) and valid(row[m[(b,idx,'draw')]]) and valid(row[m[(b,idx,'away')]]):books.add(b)
  sets.append(books)
 return len(set.intersection(*sets))>=5
def strict_record(row,m,identity,dt):
 common=[b for b in range(1,33) if provider_all72(row,m,b)]
 if len(common)<5:return None
 draw=np.full((32,72),np.nan,dtype=np.float32);cut=np.full((3,32,3),np.nan,dtype=np.float32);cut_order=[24,6,1]
 for b in common:
  bi=b-1
  for h in range(72):
   q=devig3([row[m[(b,h,o)]] for o in OUTCOMES]);draw[bi,h]=q[1]
   if h in CUT_IDX.values():cut[[CUT_IDX[x] for x in cut_order].index(h),bi,:]=q
 return {'identity':identity,'dt':dt,'common':np.array([b-1 for b in common],dtype=np.int16),'draw':draw,'cut':cut}
def load_market(source_dir):
 strict=[];base3=[];seen=set();schemas=[]
 for sanitized in ('odds_series_no_scores.csv.gz','odds_series_b_no_scores.csv.gz'):
  raw=sanitized.replace('_no_scores','')
  with gzip.open(source_dir/sanitized,'rt',encoding='utf-8-sig',newline='') as f:
   r=csv.reader(f);h=next(r);assert h[:3]==['match_id','match_date','match_time'];assert 'score_home' not in h and 'score_away' not in h;schemas.append(h[3:]);m=parse_mapping(h)
   for row in r:
    if len(row)!=len(h) or not row[0].strip():continue
    ident=f'{raw}|{row[0].strip()}'
    if ident in seen:raise RuntimeError(f'duplicate {ident}')
    seen.add(ident);dt=parse_dt(row[1],row[2]);rec=strict_record(row,m,ident,dt)
    if rec is not None:strict.append(rec);base3.append({'identity':ident,'dt':dt})
    elif base3_ok(row,m):base3.append({'identity':ident,'dt':dt})
 if schemas[0]!=schemas[1]:raise RuntimeError('provider slot schema mismatch')
 return strict,base3
def recompute_sets(strict,base3,pre):
 start=datetime.fromisoformat(pre['identity_binding']['holdout_start']);bhold=[x for x in base3 if x['dt']>=start]
 c=sorted(bhold,key=lambda x:htxt(f"{pre['identity_binding']['r39c_seed']}|{x['identity']}"))[:100]
 if set_sha(c)!=pre['identity_binding']['r39c_sha256']:raise RuntimeError('R39C drift')
 cids={x['identity'] for x in c};rem=[x for x in bhold if x['identity'] not in cids]
 d=sorted(rem,key=lambda x:htxt(f"{pre['identity_binding']['r39d_seed']}|{x['identity']}"))[:100]
 if set_sha(d)!=pre['identity_binding']['r39d_sha256']:raise RuntimeError('R39D drift')
 dids={x['identity'] for x in d};training=sorted([x for x in strict if x['dt']<start],key=lambda x:(x['dt'],x['identity']));hold=[x for x in strict if x['dt']>=start and x['identity'] not in cids and x['identity'] not in dids]
 e=sorted(hold,key=lambda x:htxt(f"{pre['identity_binding']['r39f_blind_seed']}|{x['identity']}"))[:pre['identity_binding']['r39f_blind_rows']]
 if set_sha(e)!=pre['identity_binding']['r39f_blind_sha256']:raise RuntimeError('R39F blind drift')
 return training,sorted(e,key=lambda x:(x['dt'],x['identity'])),start,cids,dids
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
def read_blind_labels(original_dir,test_ids,consumed):
 labels={};access=0;consumed_access=0
 for src in ('odds_series.csv.gz','odds_series_b.csv.gz'):
  with gzip.open(original_dir/src,'rt',encoding='utf-8-sig',newline='') as f:
   r=csv.reader(f);next(r)
   for row in r:
    if len(row)<5:continue
    ident=f'{src}|{row[0].strip()}'
    if ident in consumed:continue
    if ident not in test_ids:continue
    labels[ident]=(int(float(row[3])),int(float(row[4])));access+=1
 return labels,access,consumed_access
def actual_idx(h,a):return 0 if h>a else 1 if h==a else 2
def fit_logistic_1d(x,y,l2=1.0,max_iter=50,tol=1e-8):
 X=np.column_stack([np.ones(len(x)),np.asarray(x,dtype=float)]);beta=np.zeros(2);pen=np.array([0.,l2]);conv=False;dm=None
 for it in range(1,max_iter+1):
  eta=X@beta;p=1/(1+np.exp(-np.clip(eta,-40,40)));w=np.clip(p*(1-p),1e-8,None);grad=X.T@(p-y)+pen*beta;H=X.T@(X*w[:,None])+np.diag(pen)+np.eye(2)*1e-10;delta=np.linalg.solve(H,grad);beta-=delta;dm=float(np.max(np.abs(delta)))
  if dm<tol:conv=True;break
 return beta,{'iterations':it,'converged':conv,'delta_max':dm}
def standardize(X):
 mean=X.mean(0);std=X.std(0);std=np.where(std<1e-12,1.0,std);return (X-mean)/std,mean,std
def fit_logistic(X,y,l2=1.0,max_iter=60,tol=1e-8):
 Z=np.column_stack([np.ones(len(X)),X]);beta=np.zeros(Z.shape[1]);pen=np.zeros_like(beta);pen[1:]=l2;conv=False;dm=None
 for it in range(1,max_iter+1):
  eta=Z@beta;p=1/(1+np.exp(-np.clip(eta,-40,40)));w=np.clip(p*(1-p),1e-8,None);grad=Z.T@(p-y)+pen*beta;H=Z.T@(Z*w[:,None])+np.diag(pen)+np.eye(Z.shape[1])*1e-10;delta=np.linalg.solve(H,grad);beta-=delta;dm=float(np.max(np.abs(delta)))
  if dm<tol:conv=True;break
 return beta,{'iterations':it,'converged':conv,'delta_max':dm}
def pred_logistic(X,b):
 Z=np.column_stack([np.ones(len(X)),X]);return 1/(1+np.exp(-np.clip(Z@b,-40,40)))
def fit_provider_calibrations(rows,labels,min_rows,l2):
 out={};counts={}
 for b in range(32):
  xs=[];ys=[]
  for r in rows:
   if b not in r['common']:continue
   xs.append(logit(float(r['draw'][b,70])));hg,ag=labels[r['identity']];ys.append(1. if hg==ag else 0.)
  counts[b]=len(xs)
  if len(xs)>=min_rows:
   beta,diag=fit_logistic_1d(np.asarray(xs),np.asarray(ys),l2=l2)
   if not diag['converged']:raise RuntimeError(f'provider calibration no converge b{b+1}')
   out[b]={'alpha':float(beta[0]),'beta':float(beta[1]),'rows':len(xs),'diag':diag}
 return out,counts
class CorrAcc:
 def __init__(self):self.n=0;self.sx=self.sy=self.sxx=self.syy=self.sxy=0.0
 def add_many(self,x,y):
  x=np.asarray(x,dtype=float);y=np.asarray(y,dtype=float);self.n+=len(x);self.sx+=float(x.sum());self.sy+=float(y.sum());self.sxx+=float(x@x);self.syy+=float(y@y);self.sxy+=float(x@y)
 def corr(self):
  if self.n<10:return 0.0
  vx=self.sxx-self.sx*self.sx/self.n;vy=self.syy-self.sy*self.sy/self.n
  if vx<=1e-12 or vy<=1e-12:return 0.0
  return (self.sxy-self.sx*self.sy/self.n)/math.sqrt(vx*vy)
def fit_lead_scores(rows,active):
 acc={b:{lag:CorrAcc() for lag in (1,2,3)} for b in active};aset=set(active)
 for r in rows:
  books=[b for b in r['common'] if b in aset]
  if len(books)<5:continue
  L={b:np.array([logit(x) for x in r['draw'][b,:71]],dtype=float) for b in books};moves={b:np.diff(L[b]) for b in books}
  for b in books:
   others=[o for o in books if o!=b]
   for lag in (1,2,3):
    n=70-lag
    if n<=0:continue
    crowd=np.mean(np.vstack([moves[o][lag:lag+n] for o in others]),axis=0);acc[b][lag].add_many(moves[b][:n],crowd)
 out={}
 for b in active:
  cs=[acc[b][lag].corr() for lag in (1,2,3)];out[b]={'corr_lag1':cs[0],'corr_lag2':cs[1],'corr_lag3':cs[2],'lead_score':max(0.0,float(np.mean(cs))),'pairs':{str(l):acc[b][l].n for l in (1,2,3)}}
 return out
def calibrate_path(raw,b,cal):
 a=cal[b]['alpha'];bb=cal[b]['beta'];z=np.array([logit(float(x)) for x in raw],dtype=float);return 1/(1+np.exp(-np.clip(a+bb*z,-40,40)))
def corr_xy(x,y):
 x=np.asarray(x,dtype=float);y=np.asarray(y,dtype=float)
 if len(x)<3:return 0.0
 if x.std()<1e-12 or y.std()<1e-12:return 0.0
 return float(np.corrcoef(x,y)[0,1])
def micro_features(r,cal,lead):
 books=[b for b in r['common'] if b in cal]
 if len(books)<5:return None
 paths={b:calibrate_path(r['draw'][b,:71],b,cal) for b in books};mat=np.vstack([paths[b] for b in books]);ordinary=mat.mean(axis=0);scores=np.array([max(0.0,lead[b]['lead_score'])+0.01 for b in books],dtype=float);scores/=scores.sum();leader=np.sum(mat*scores[:,None],axis=0);ranked=sorted(books,key=lambda b:(-lead[b]['lead_score'],b));top=ranked[:min(3,len(ranked))];rest=[b for b in books if b not in top];topmean=np.mean(np.vstack([paths[b] for b in top]),axis=0);restmean=np.mean(np.vstack([paths[b] for b in rest]),axis=0) if rest else ordinary
 def mv(path,idx):return float(path[-1]-path[idx])
 leader24=mv(leader,47);leader6=mv(leader,65);agree24=np.mean([np.sign(mv(paths[b],47))==np.sign(leader24) for b in books]);agree6=np.mean([np.sign(mv(paths[b],65))==np.sign(leader6) for b in books]);props=[];moves={b:np.diff(np.array([logit(x) for x in paths[b]],dtype=float)) for b in books}
 for lag in (1,2,3):
  xx=[];yy=[];n=70-lag
  for b in books:
   oth=[o for o in books if o!=b];crowd=np.mean(np.vstack([moves[o][lag:lag+n] for o in oth]),axis=0);xx.extend(moves[b][:n]);yy.extend(crowd)
  props.append(corr_xy(xx,yy))
 d=np.diff(leader);nz=np.sign(d);nz=nz[nz!=0];reversal=float(np.sum(nz[1:]!=nz[:-1])/(len(nz)-1)) if len(nz)>1 else 0.0;q1=np.nanmean(r['cut'][2,books,:],axis=0);q1=q1/q1.sum()
 f=[logit(float(ordinary[-1])),logit(float(leader[-1])),float(leader[-1]-ordinary[-1]),mv(ordinary,0),mv(leader,0),mv(ordinary,47),leader24,mv(ordinary,65),leader6,float(topmean[47]-restmean[47]),float(topmean[65]-restmean[65]),float(topmean[-1]-restmean[-1]),float((topmean[-1]-restmean[-1])-(topmean[47]-restmean[47])),float(mat[:,47].std()),float(mat[:,65].std()),float(mat[:,-1].std()),float(mat[:,-1].std()-mat[:,47].std()),float(agree24),float(agree6),props[0],props[1],props[2],reversal,math.log1p(len(books)),abs(float(q1[0]-q1[2])),entropy(q1)]
 return np.asarray(f,dtype=float),q1
def benchmark15(r):
 books=list(r['common']);qs=[];std=[]
 for ci in range(3):
  arr=r['cut'][ci,books,:].astype(float);q=arr.mean(0);q/=q.sum();qs.append(q);std.append(float(arr[:,1].std()))
 q24,q6,q1=qs;d246=float(q6[1]-q24[1]);d61=float(q1[1]-q6[1]);g24=abs(float(q24[0]-q24[2]));g1=abs(float(q1[0]-q1[2]));e24=entropy(q24);e1=entropy(q1)
 return np.asarray([logit(float(q1[1])),d246,d61,d61/5-d246/18,std[0],std[1],std[2],std[2]-std[0],g24,g1,g1-g24,e24,e1,e1-e24,math.log1p(len(books))],dtype=float),q1
def threeway(q1,pd):
 pd=clip(pd);h,a=float(q1[0]),float(q1[2]);s=h+a
 return np.array([(1-pd)*h/s,pd,(1-pd)*a/s]) if s>0 else np.array([(1-pd)/2,pd,(1-pd)/2])
def prob_metrics(probs,actual):
 ll=br=rps=0.0
 for p,y in zip(probs,actual):
  one=np.zeros(3);one[y]=1;ll-=math.log(max(float(p[y]),1e-15));br+=float(((p-one)**2).sum());rps+=.5*((float(p[0]-one[0]))**2+(float(p[0]+p[1]-one[0]-one[1]))**2)
 n=len(actual);return {'log_loss':ll/n,'brier':br/n,'rps':rps/n}
def binary_logloss(pd,y):
 pd=np.clip(np.asarray(pd,dtype=float),1e-15,1-1e-15);y=np.asarray(y,dtype=float);return float(np.mean(-(y*np.log(pd)+(1-y)*np.log(1-pd))))
def auc(pd,y):
 pos=[float(p) for p,z in zip(pd,y) if z==1];neg=[float(p) for p,z in zip(pd,y) if z==0]
 if not pos or not neg:return None
 w=0.0
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
  for j in range(N):
   i=rnd.randrange(N);s+=int(cand[i]==actual[i])-int(base[i]==actual[i])
  v.append(s/N)
 v.sort();q=lambda x:v[min(len(v)-1,max(0,int(x*(len(v)-1))))];return {'mean':sum(v)/n,'p05':q(.05),'p50':q(.5),'p95':q(.95),'samples':n,'seed':seed}
def build_matrix(rows,feature_fn,labels):
 feats=[];q=[];actual=[];kept=[]
 for r in rows:
  x,qq=feature_fn(r)
  if x is None:continue
  feats.append(x);q.append(qq);hg,ag=labels[r['identity']];actual.append(actual_idx(hg,ag));kept.append(r)
 return kept,np.vstack(feats),q,actual
def self_test():
 x=np.linspace(-2,2,50);y=(x>0).astype(float);b,d=fit_logistic_1d(x,y);assert d['converged'];X=np.column_stack([x,x*x]);Xs,me,st=standardize(X);bb,dd=fit_logistic(Xs,y);assert dd['converged'];print('PASS_R39F_SYNTHETIC_SELF_TEST')
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--prereg',type=Path);ap.add_argument('--sanitized-dir',type=Path);ap.add_argument('--original-dir',type=Path);ap.add_argument('--out-dir',type=Path);ap.add_argument('--self-test',action='store_true');a=ap.parse_args()
 if a.self_test:self_test();return
 if not all((a.prereg,a.sanitized_dir,a.original_dir,a.out_dir)):raise SystemExit('missing required args')
 a.out_dir.mkdir(parents=True,exist_ok=True);pre=json.loads(a.prereg.read_text());strict,base3=load_market(a.sanitized_dir);training,test,start,cids,dids=recompute_sets(strict,base3,pre)
 if len(training)!=pre['identity_binding']['strict_lane_training_rows']:raise RuntimeError(f"training drift {len(training)}")
 ids={x['identity'] for x in training};labels,train_access=read_training_labels(a.original_dir,ids,start)
 if len(labels)!=len(training):raise RuntimeError(f'training labels {len(labels)} vs {len(training)}')
 n=len(training);rep_end=int(math.floor(n*pre['chronological_design']['representation_fraction']));fit_end=int(math.floor(n*pre['chronological_design']['fit_fraction']));val_end=int(math.floor(n*(pre['chronological_design']['fit_fraction']+pre['chronological_design']['validation_fraction'])));rep=training[:rep_end];fit=training[:fit_end];val=training[fit_end:val_end];policy=training[val_end:]
 cal,counts=fit_provider_calibrations(rep,labels,int(pre['provider_calibration']['minimum_representation_rows_per_provider']),float(pre['provider_calibration']['l2_lambda']));active=sorted(cal)
 if len(active)<pre['provider_calibration']['minimum_active_provider_slots']:raise RuntimeError(f'active providers {len(active)}')
 lead=fit_lead_scores(rep,active)
 def micro_fn(r):
  z=micro_features(r,cal,lead);return z if z is not None else (None,None)
 fit_kept,Xfit,qfit,yfit=build_matrix(fit,micro_fn,labels);val_kept,Xval,qval,yval=build_matrix(val,micro_fn,labels);pol_kept,Xpol,qpol,ypol=build_matrix(policy,micro_fn,labels)
 if min(len(fit_kept),len(val_kept),len(pol_kept))<1000:raise RuntimeError('micro coverage insufficient')
 Xs,mean,std=standardize(Xfit);Bfit=np.vstack([benchmark15(r)[0] for r in fit_kept]);Bval=np.vstack([benchmark15(r)[0] for r in val_kept]);Bs,bmean,bstd=standardize(Bfit);yb=np.array([1 if y==1 else 0 for y in yfit],dtype=float);b15,bdiag=fit_logistic(Bs,yb,l2=float(pre['benchmark15']['l2_lambda']));bvalpd=pred_logistic((Bval-bmean)/bstd,b15);bvalprob=[threeway(q,p) for q,p in zip(qval,bvalpd)];bvalm=prob_metrics(bvalprob,yval);bvalauc=auc(bvalpd,[1 if y==1 else 0 for y in yval]);bvalbin=binary_logloss(bvalpd,[1 if y==1 else 0 for y in yval]);market_val=[np.asarray(q,dtype=float) for q in qval];market_val_m=prob_metrics(market_val,yval);candidates=[]
 for l2 in pre['micro_model']['l2_candidates']:
  beta,diag=fit_logistic(Xs,np.array([1 if y==1 else 0 for y in yfit],dtype=float),l2=float(l2));pd=pred_logistic((Xval-mean)/std,beta);probs=[threeway(q,p) for q,p in zip(qval,pd)];pm=prob_metrics(probs,yval);ba=auc(pd,[1 if y==1 else 0 for y in yval]);bl=binary_logloss(pd,[1 if y==1 else 0 for y in yval]);candidates.append({'l2':float(l2),'beta':beta,'diag':diag,'validation_hda':pm,'validation_draw_auc':ba,'validation_binary_draw_logloss':bl})
 chosen=sorted(candidates,key=lambda c:(c['validation_hda']['log_loss'],c['validation_binary_draw_logloss'],c['l2']))[0];gate={'hda_logloss_better_than_market':chosen['validation_hda']['log_loss']<market_val_m['log_loss'],'hda_logloss_better_than_benchmark15':chosen['validation_hda']['log_loss']<bvalm['log_loss'],'draw_auc_better_than_benchmark15':chosen['validation_draw_auc']>bvalauc,'binary_draw_logloss_better_than_benchmark15':chosen['validation_binary_draw_logloss']<bvalbin};common_diag={'schema_version':pre['schema_version'],'generated_at_utc':datetime.now(timezone.utc).isoformat(),'strict_rows_total':len(strict),'training_rows':len(training),'representation_rows':len(rep),'fit_rows':len(fit_kept),'validation_rows':len(val_kept),'policy_rows':len(pol_kept),'training_labels_accessed':train_access,'active_provider_slots':[f'b{x+1}' for x in active],'provider_representation_counts':{f'b{k+1}':v for k,v in counts.items()},'lead_scores':{f'b{k+1}':v for k,v in lead.items()},'benchmark15_validation':{'hda':bvalm,'draw_auc':bvalauc,'binary_draw_logloss':bvalbin},'market_validation':market_val_m,'micro_candidates':[{'l2':c['l2'],'diag':c['diag'],'validation_hda':c['validation_hda'],'validation_draw_auc':c['validation_draw_auc'],'validation_binary_draw_logloss':c['validation_binary_draw_logloss']} for c in candidates],'selected_l2':chosen['l2'],'validation_gate':gate,'blind_fixed100_labels_accessed':0}
 if not all(gate.values()):
  out={**common_diag,'status':pre['micro_model']['if_validation_gate_fails']};(a.out_dir/'r39f_result.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2));return
 beta=chosen['beta'];ppd=pred_logistic((Xpol-mean)/std,beta);pprob=[threeway(q,p) for q,p in zip(qpol,ppd)];market_pol=[int(np.argmax(q)) for q in qpol];chosen_lane,cands,base_dec,prev=select_policy(pol_kept,pprob,ypol,market_pol,pre)
 if chosen_lane is None:
  out={**common_diag,'status':pre['policy']['if_no_lane'],'policy_candidates':cands,'policy_market':base_dec,'policy_draw_prevalence':prev};(a.out_dir/'r39f_result.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2));return
 freeze={'selected_l2':chosen['l2'],'provider_calibrations':{f'b{k+1}':v for k,v in cal.items()},'lead_scores':{f'b{k+1}':v for k,v in lead.items()},'micro_mean':mean.tolist(),'micro_std':std.tolist(),'micro_beta':beta.tolist(),'benchmark15_mean':bmean.tolist(),'benchmark15_std':bstd.tolist(),'benchmark15_beta':b15.tolist(),'decision_lane':chosen_lane,'prereg_sha256':hfile(a.prereg)};freeze['parameter_sha256']=canonical_sha(freeze);(a.out_dir/'model_policy_freeze_r39f.json').write_text(json.dumps(freeze,ensure_ascii=False,indent=2),encoding='utf-8');test_ids={x['identity'] for x in test};blind_labels,blind_access,consumed_access=read_blind_labels(a.original_dir,test_ids,cids|dids)
 if blind_access!=len(test) or consumed_access!=0:raise RuntimeError(f'blind label access {blind_access} consumed {consumed_access}')
 Xt=[];Bt=[];qt=[];yt=[];kept=[]
 for r in test:
  z=micro_features(r,cal,lead)
  if z is None:continue
  x,q=z;Xt.append(x);Bt.append(benchmark15(r)[0]);qt.append(q);hg,ag=blind_labels[r['identity']];yt.append(actual_idx(hg,ag));kept.append(r)
 if len(kept)!=100:raise RuntimeError(f'blind micro coverage {len(kept)}')
 Xt=np.vstack(Xt);Bt=np.vstack(Bt);tpd=pred_logistic((Xt-mean)/std,beta);tprob=[threeway(q,p) for q,p in zip(qt,tpd)];bpd=pred_logistic((Bt-bmean)/bstd,b15);bprob=[threeway(q,p) for q,p in zip(qt,bpd)];market_prob=[np.asarray(q,dtype=float) for q in qt];market_pred=[int(np.argmax(q)) for q in market_prob];cand_pred=[apply(p,chosen_lane['threshold']) for p in tprob];mdec=decision_metrics(market_pred,yt);cdec=decision_metrics(cand_pred,yt);mp=prob_metrics(market_prob,yt);cp=prob_metrics(tprob,yt);bp=prob_metrics(bprob,yt);ma=auc([q[1] for q in market_prob],[1 if y==1 else 0 for y in yt]);ca=auc(tpd,[1 if y==1 else 0 for y in yt]);ba=auc(bpd,[1 if y==1 else 0 for y in yt]);hgates={'accuracy_better_than_market':cdec['accuracy']>mdec['accuracy'],'precision_min':cdec['draw_precision']>=pre['holdout']['gate']['draw_precision_min'],'recall_min':cdec['draw_recall']>=pre['holdout']['gate']['draw_recall_min'],'f1_min':cdec['draw_f1']>=pre['holdout']['gate']['draw_f1_min'],'draw_count_min':cdec['predicted_draw_count']>=pre['holdout']['gate']['predicted_draw_min'],'draw_count_max':cdec['predicted_draw_count']<=pre['holdout']['gate']['predicted_draw_max'],'hda_logloss_nonworse_market':cp['log_loss']<=mp['log_loss'],'hda_logloss_better_benchmark15':cp['log_loss']<bp['log_loss'],'draw_auc_better_benchmark15':ca>ba};status=pre['holdout']['pass_status'] if all(hgates.values()) else pre['holdout']['fail_status'];out={**common_diag,'status':status,'policy_candidates':cands,'selected_policy':chosen_lane,'freeze_parameter_sha256':freeze['parameter_sha256'],'blind_fixed100_labels_accessed':blind_access,'blind_actual_hda':{'home':sum(y==0 for y in yt),'draw':sum(y==1 for y in yt),'away':sum(y==2 for y in yt)},'market_holdout':{'decision':mdec,'probability':mp,'draw_auc':ma},'benchmark15_holdout':{'probability':bp,'draw_auc':ba},'micro_holdout':{'decision':cdec,'probability':cp,'draw_auc':ca},'holdout_gate':hgates,'paired_bootstrap_accuracy_delta':bootstrap(market_pred,cand_pred,yt,pre['holdout']['bootstrap_samples'],pre['holdout']['bootstrap_seed'])};(a.out_dir/'r39f_result.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
