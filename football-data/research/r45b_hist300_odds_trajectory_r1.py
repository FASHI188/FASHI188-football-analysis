import zipfile,gzip,io,json,math,random
from pathlib import Path
from datetime import datetime
import numpy as np
from scipy.optimize import minimize
from sklearn.metrics import roc_auc_score
import pandas as pd

ZIP=Path('/mnt/data/archive (1)(1).zip')
SEED=20260813
N=300
TPS=[24,48,71]
BOOKS=range(1,33)
OUT=Path('/mnt/data/r45b_hist300_odds_trajectory_result.json')
PRED=Path('/mnt/data/r45b_hist300_odds_trajectory_predictions.csv')

def logistic(x): return 1/(1+np.exp(-np.clip(x,-35,35)))
def logit(p):
    p=np.clip(p,1e-9,1-1e-9); return np.log(p/(1-p))

def agg_snapshot(parts, idxs):
    H=[];D=[];A=[];O=[]; bybook={}
    for b,(ih,id_,ia) in idxs.items():
        try:
            h=float(parts[ih]); d=float(parts[id_]); a=float(parts[ia])
        except: continue
        if not (math.isfinite(h) and math.isfinite(d) and math.isfinite(a) and h>1 and d>1 and a>1): continue
        rh,rd,ra=1/h,1/d,1/a; s=rh+rd+ra
        ph,pd,pa=rh/s,rd/s,ra/s
        H.append(ph);D.append(pd);A.append(pa);O.append(s-1);bybook[b]=(ph,pd,pa)
    if len(D)<3: return None
    mh,md,ma=np.median(H),np.median(D),np.median(A); ss=mh+md+ma
    return {'pH':mh/ss,'pD':md/ss,'pA':ma/ss,'book_count':len(D),'draw_disp':float(np.std(D)),'overround':float(np.mean(O)),'bybook':bybook}

def row_features(parts, meta_idx, snap_idxs):
    try:
        dt=datetime.fromisoformat(parts[meta_idx['match_date']]+' '+parts[meta_idx['match_time']])
    except: return None
    snaps={}
    for t in TPS:
        s=agg_snapshot(parts,snap_idxs[t])
        if s is None: return None
        snaps[t]=s
    s24,s48,s71=snaps[24],snaps[48],snaps[71]
    common=set(s24['bybook']) & set(s71['bybook'])
    if not common: return None
    cons=sum(1 for b in common if s71['bybook'][b][1] > s24['bybook'][b][1]) / len(common)
    f=[
      s71['pD']-s24['pD'],
      s71['pD']-s48['pD'],
      abs(s24['pH']-s24['pA'])-abs(s71['pH']-s71['pA']),
      1-abs(s71['pH']-s71['pA']),
      s71['draw_disp'],
      s71['overround'],
      math.log1p(s71['book_count']),
      cons,
    ]
    if not all(math.isfinite(x) for x in f): return None
    return {'match_id':parts[meta_idx['match_id']], 'match_date':parts[meta_idx['match_date']], 'match_time':parts[meta_idx['match_time']], 'dt':dt,
            'score_home_raw':parts[meta_idx['score_home']], 'score_away_raw':parts[meta_idx['score_away']],
            'base':[s71['pH'],s71['pD'],s71['pA']], 'features':f}

def multiclass_ll(y,p): return float(-np.mean(np.log(np.clip(p[np.arange(len(y)),y],1e-15,1))))
def binary_ll(y,p): return float(-np.mean(y*np.log(np.clip(p,1e-15,1))+(1-y)*np.log(np.clip(1-p,1e-15,1))))
def brier_mc(y,p):
    oh=np.eye(3)[y]; return float(np.mean(np.sum((p-oh)**2,axis=1)/3))
def rps(y,p):
    oh=np.eye(3)[y]; return float(np.mean(np.sum((np.cumsum(p,axis=1)[:,:-1]-np.cumsum(oh,axis=1)[:,:-1])**2,axis=1)/2))
def acc(y,p): return float(np.mean(np.argmax(p,axis=1)==y))
def metrics(y,p):
    yd=(y==1).astype(int); pd_=p[:,1]
    pred=np.argmax(p,axis=1); pred_draw=pred==1
    tp=int(np.sum(pred_draw & (yd==1))); pp=int(np.sum(pred_draw)); ap=int(np.sum(yd))
    return {'multiclass_logloss':multiclass_ll(y,p),'draw_binary_logloss':binary_ll(yd,pd_),'multiclass_brier':brier_mc(y,p),'rps':rps(y,p),'accuracy':acc(y,p),
            'draw_auc':float(roc_auc_score(yd,pd_)) if len(np.unique(yd))>1 else None,
            'top1_draw_count':pp,'actual_draw_count':ap,'top1_draw_precision':float(tp/pp) if pp else None,'top1_draw_recall':float(tp/ap) if ap else None}

def fit_offset(X,y,pd_base,lam=1.0):
    off=logit(pd_base)
    def obj(beta):
        q=logistic(off+beta[0]+X@beta[1:])
        ll=-np.sum(y*np.log(np.clip(q,1e-15,1))+(1-y)*np.log(np.clip(1-q,1e-15,1)))
        return ll+0.5*lam*np.sum(beta[1:]**2)
    def jac(beta):
        q=logistic(off+beta[0]+X@beta[1:]); e=q-y
        g=np.empty_like(beta);g[0]=np.sum(e);g[1:]=X.T@e+lam*beta[1:];return g
    res=minimize(obj,np.zeros(X.shape[1]+1),jac=jac,method='L-BFGS-B',options={'maxiter':2000,'gtol':1e-8})
    return res.x,res

def apply_offset(X,pbase,beta):
    qD=logistic(logit(pbase[:,1])+beta[0]+X@beta[1:]); rem=1-qD; ha=pbase[:,0]+pbase[:,2]
    return np.column_stack([rem*pbase[:,0]/ha,qD,rem*pbase[:,2]/ha])

rng=random.Random(SEED); reservoir=[]; eligible_count=0; total_rows=0
with zipfile.ZipFile(ZIP) as z, z.open('odds_series.csv.gz') as raw, gzip.GzipFile(fileobj=raw) as gz, io.TextIOWrapper(gz,encoding='utf-8',errors='replace',newline='') as f:
    header=f.readline().rstrip('\n\r').split(','); pos={c:i for i,c in enumerate(header)}
    meta_idx={c:pos[c] for c in ['match_id','match_date','match_time','score_home','score_away']}
    snap_idxs={t:{b:(pos[f'home_b{b}_{t}'],pos[f'draw_b{b}_{t}'],pos[f'away_b{b}_{t}']) for b in BOOKS} for t in TPS}
    for line in f:
        total_rows+=1
        parts=line.rstrip('\n\r').split(',')
        if len(parts)!=len(header): continue
        rec=row_features(parts,meta_idx,snap_idxs)
        if rec is None: continue
        eligible_count+=1
        if len(reservoir)<N: reservoir.append(rec)
        else:
            j=rng.randrange(eligible_count)
            if j<N: reservoir[j]=rec

if len(reservoir)<N: raise SystemExit(f'only {len(reservoir)} eligible')
for r in reservoir:
    sh=float(r['score_home_raw']); sa=float(r['score_away_raw'])
    r['y']=0 if sh>sa else (1 if sh==sa else 2)
reservoir.sort(key=lambda r:r['dt'])
features=np.array([r['features'] for r in reservoir],float); base=np.array([r['base'] for r in reservoir],float); y=np.array([r['y'] for r in reservoir],int)
feature_names=['draw_move_24_to_71','draw_move_48_to_71','balance_move_24_to_71','close_home_away_balance','close_draw_book_dispersion','close_mean_overround','log_close_book_count','fraction_books_draw_prob_up_24_to_71']
dates=np.array([r['dt'].date().isoformat() for r in reservoir])
ends=[]
for i in range(1,N+1):
    if i==N or dates[i]!=dates[i-1]: ends.append(i)
ends=np.array(ends)
def nearest(target,min_end):
    c=ends[ends>min_end]; return int(c[np.argmin(np.abs(c-target))])
b1=nearest(120,0);b2=nearest(180,b1);b3=nearest(240,b2);b4=N
bounds=[b1,b2,b3,b4]
folds=[];PB=[];PC=[];YY=[];test_idx=[];prev=b1
for fi,end in enumerate([b2,b3,b4],1):
    tr=np.arange(prev);te=np.arange(prev,end)
    mu=features[tr].mean(0);sd=features[tr].std(0);sd=np.where(sd<1e-9,1,sd)
    Xtr=(features[tr]-mu)/sd;Xte=(features[te]-mu)/sd
    beta,res=fit_offset(Xtr,(y[tr]==1).astype(float),base[tr,1],1.0)
    q=apply_offset(Xte,base[te],beta)
    mb,mc=metrics(y[te],base[te]),metrics(y[te],q)
    folds.append({'fold':fi,'train_n':len(tr),'test_n':len(te),'train_end':reservoir[tr[-1]]['dt'].isoformat(),'test_start':reservoir[te[0]]['dt'].isoformat(),'test_end':reservoir[te[-1]]['dt'].isoformat(),
                  'optimizer_success':bool(res.success),'iterations':int(res.nit),'final_grad_norm':float(np.linalg.norm(res.jac)),
                  'beta':{'intercept':float(beta[0]),**{feature_names[j]:float(beta[j+1]) for j in range(len(feature_names))}},
                  'baseline':mb,'challenger':mc,
                  'delta':{k:float(mc[k]-mb[k]) if isinstance(mb[k],(int,float)) and isinstance(mc[k],(int,float)) else None for k in ['multiclass_logloss','draw_binary_logloss','multiclass_brier','rps','accuracy','draw_auc']}})
    PB.append(base[te]);PC.append(q);YY.append(y[te]);test_idx.extend(te.tolist());prev=end
Y=np.concatenate(YY);PB=np.vstack(PB);PC=np.vstack(PC)
mb,mc=metrics(Y,PB),metrics(Y,PC)
delta={k:float(mc[k]-mb[k]) if isinstance(mb[k],(int,float)) and isinstance(mc[k],(int,float)) else None for k in ['multiclass_logloss','draw_binary_logloss','multiclass_brier','rps','accuracy','draw_auc']}
rng2=np.random.default_rng(SEED);tdates=dates[np.array(test_idx)];ud=np.unique(tdates)
def row_mc(y,p): return -np.log(np.clip(p[np.arange(len(y)),y],1e-15,1))
def row_bin(y,p):
    yd=(y==1).astype(float);q=p[:,1];return -(yd*np.log(np.clip(q,1e-15,1))+(1-yd)*np.log(np.clip(1-q,1e-15,1)))
dmc=row_mc(Y,PC)-row_mc(Y,PB);db=row_bin(Y,PC)-row_bin(Y,PB);bm=[];bb=[]
for _ in range(2000):
    ds=rng2.choice(ud,size=len(ud),replace=True);ii=np.concatenate([np.flatnonzero(tdates==d) for d in ds]);bm.append(float(dmc[ii].mean()));bb.append(float(db[ii].mean()))
ci_mc=[float(np.quantile(bm,.05)),float(np.quantile(bm,.95))];ci_bin=[float(np.quantile(bb,.05)),float(np.quantile(bb,.95))]
ids={r['match_id'] for r in reservoir};meta={}
with zipfile.ZipFile(ZIP) as z,z.open('odds_series_matches.csv.gz') as raw,gzip.GzipFile(fileobj=raw) as gz,io.TextIOWrapper(gz,encoding='utf-8',errors='replace') as f:
    h=f.readline().rstrip('\n\r').split(',');pm={c.strip():i for i,c in enumerate(h)}
    for line in f:
        p=line.rstrip('\n\r').split(',');mid=p[pm['match_id']].strip()
        if mid in ids: meta[mid]={'league':p[pm['league']].strip(),'home_team':p[pm['home_team']].strip(),'away_team':p[pm['away_team']].strip()}
rows=[];test_pos={idx:i for i,idx in enumerate(test_idx)}
for i,r in enumerate(reservoir):
    m=meta.get(r['match_id'],{});row={'match_id':r['match_id'],'date_time':r['dt'].isoformat(),'league':m.get('league'),'home_team':m.get('home_team'),'away_team':m.get('away_team'),'score':f"{r['score_home_raw']}:{r['score_away_raw']}",'outcome':['H','D','A'][r['y']],
      'baseline_pH':base[i,0],'baseline_pD':base[i,1],'baseline_pA':base[i,2],'is_test':i in test_pos}
    if i in test_pos:
        j=test_pos[i];row.update({'challenger_pH':PC[j,0],'challenger_pD':PC[j,1],'challenger_pA':PC[j,2]})
    rows.append(row)
pd.DataFrame(rows).to_csv(PRED,index=False)
res={'schema_version':'R45B-HIST300-ODDS-TRAJECTORY-R1','research_only':True,'formal_weight':0,'source_zip':ZIP.name,'source_table':'odds_series.csv.gz',
 'sample_policy':{'seed':SEED,'source_rows':total_rows,'eligible_pool_count':eligible_count,'sample_n':N,'selection':'uniform reservoir sample after fixed pre-label feature-availability filter; result values not consulted for eligibility or sampling','fixed_checkpoints':TPS,'minimum_complete_books_each_checkpoint':3,'chronological_after_selection':True},
 'candidate':{'id':'HIST300_DRAW_MASS_LOGIT_OFFSET_ODDS_TRAJECTORY_R1','baseline':'checkpoint71 consensus no-vig H/D/A','algorithm':'logit(qD)=logit(pD_base)+beta0+beta^Tz; remaining H/A mass preserves baseline H:A ratio','l2_lambda':1.0,'features':feature_names,'candidate_search':False,'threshold_tuning':False,'post_label_feature_selection':False},
 'split':{'bounds':bounds,'folds':folds},
 'pooled_oos':{'test_n':len(Y),'baseline':mb,'challenger':mc,'delta_challenger_minus_baseline':delta,'multiclass_logloss_delta_90pct_ci':ci_mc,'draw_binary_logloss_delta_90pct_ci':ci_bin,'bootstrap':'paired calendar-date block, 2000, seed 20260813'},
 'hard_readout':{'primary_multiclass_ll_improved':delta['multiclass_logloss']<0,'primary_90pct_upper_below_zero':ci_mc[1]<0,'draw_binary_ll_improved':delta['draw_binary_logloss']<0,'draw_90pct_upper_below_zero':ci_bin[1]<0,'folds_multiclass_ll_improved':sum(f['delta']['multiclass_logloss']<0 for f in folds),'folds_draw_binary_ll_improved':sum(f['delta']['draw_binary_logloss']<0 for f in folds),'ruling':'HISTORICAL_DEVELOPMENT_ONLY_NOT_INDEPENDENT_OOS_NOT_FULL_R45B'},
 'limitations':['archive has odds trajectory and result labels but no expected-XI/roles, availability/replacement, process-capability, or task-state inputs required by full R45B','no native quote timestamps for individual odds snapshots; retrospective research only','300-match development result cannot change formal_weight or replace the separate prospective R45B OOS gate'],
 'prediction_csv':PRED.name}
OUT.write_text(json.dumps(res,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')