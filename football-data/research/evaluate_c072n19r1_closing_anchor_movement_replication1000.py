#!/usr/bin/env python3
from __future__ import annotations
import hashlib, json, math
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
from scipy.optimize import brentq, minimize
from scipy.special import gammaln

MIRROR_REPO='MestreAlex/elo-rating'
MIRROR_REV='383d5277fdaed48fd2d909e073e047350e71cb7f'
RAW=f'https://raw.githubusercontent.com/{MIRROR_REPO}/{MIRROR_REV}/data/'
FILES=(
    ('E0','E0_2526.csv','0134017ec2cdec9db8e47e72eabbd74af068a276'),
    ('E1','E1_2526.csv','47b6539b5da4b319e82701d7c5f9bb234f758223'),
    ('D1','D1_2526.csv','9224c84b9f7574461abc48e1119a52704994517d'),
    ('D2','D2_2526.csv','18b5a44ad2fc98b4be9f5884e57d2cdef082cdc0'),
    ('I1','I1_2526.csv','05fde602b37217ddce1da60bb02fb351b246e659'),
    ('I2','I2_2526.csv','e4ac8e20e65af7c06e04a065b6bc0aa526cbfcc3'),
    ('F1','F1_2526.csv','54051914fc311277ab495ec7de950daabfad271b'),
    ('F2','F2_2526.csv','776d5648f1f3c915e34423fcd2a85c5384e5d8cf'),
    ('SP1','SP1_2526.csv','279bd1eee9759f114e89ecc38e32af9ee7c9cdac'),
    ('SP2','SP2_2526.csv','e8258f7c88b1b756b0cc9e728c84f52a83ca4072'),
)
MARKET_COLS=['Div','Date','HomeTeam','AwayTeam','Avg>2.5','Avg<2.5','AvgC>2.5','AvgC<2.5']
TARGET_COLS=['Div','Date','HomeTeam','AwayTeam','FTHG','FTAG']
EXPECTED_ID_SHA='10f77a6b20502813c0ae8402c7dd45e80054dcc5b6b6546751fca736033cddce'
TARGET_N=1000
RIDGE_LAMBDA=1.0
BOOT_REPS=5000
BOOT_SEED=72019
FOLDS=((400,550),(550,700),(700,850),(850,1000))
OUT=Path('football-data/research/_c072n19r1_evaluation')
SUMMARY=OUT/'c072n19r1_summary.json'
PRED=OUT/'c072n19r1_oos_predictions.jsonl'


def odds(x):
    try:
        v=float(x)
        return v if math.isfinite(v) and v>1 else None
    except Exception:return None

def parse_date(x):
    s=str(x).strip()
    for fmt in ('%d/%m/%Y','%d/%m/%y'):
        try:return datetime.strptime(s,fmt).date().isoformat()
        except ValueError:pass
    raise RuntimeError(f'date parse failed: {s}')

def logit(p):
    p=min(max(float(p),1e-12),1-1e-12)
    return math.log(p/(1-p))

def devig_over(o,u):
    a,b=1/float(o),1/float(u)
    return a/(a+b)

def reproduce_lock():
    pool=[]
    for code,name,blob_sha in FILES:
        df=pd.read_csv(RAW+name,usecols=MARKET_COLS,encoding='utf-8-sig')
        for pos,r in df.iterrows():
            vals=[odds(r[c]) for c in MARKET_COLS[4:]]
            if not all(v is not None for v in vals):continue
            home=str(r['HomeTeam']).strip(); away=str(r['AwayTeam']).strip(); div=str(r['Div']).strip(); date=str(r['Date']).strip()
            if not home or not away or not div or not date:continue
            pool.append({'division':div,'source_code':code,'source_file':name,'source_blob_sha':blob_sha,
                         'date':date,'date_iso':parse_date(date),'home_team':home,'away_team':away,
                         'avg_over25_open':vals[0],'avg_under25_open':vals[1],
                         'avg_over25_close':vals[2],'avg_under25_close':vals[3],
                         'source_row_index':int(pos)})
    pool.sort(key=lambda z:(z['date_iso'],z['source_code'],z['home_team'],z['away_team'],z['source_row_index']))
    if len(pool)<TARGET_N:raise RuntimeError(f'coverage drift {len(pool)} < {TARGET_N}')
    sel=pool[:TARGET_N]
    ids=[f"{z['source_code']}|{z['date_iso']}|{z['home_team']}|{z['away_team']}|{z['source_row_index']}" for z in sel]
    sha=hashlib.sha256(('\n'.join(ids)+'\n').encode()).hexdigest()
    if len(sel)!=TARGET_N or sha!=EXPECTED_ID_SHA:
        raise RuntimeError(f'ZERO_LABEL_IDENTITY_DRIFT n={len(sel)} sha={sha}')
    for z,ident in zip(sel,ids):z['identity']=ident
    return sel,sha

def materialize_only_frozen_targets(sel):
    by_code={c:[] for c,_,_ in FILES}
    for z in sel:by_code[z['source_code']].append(z)
    out={}
    for code,name,_ in FILES:
        chosen=sorted(by_code[code],key=lambda z:z['source_row_index'])
        wanted={z['source_row_index'] for z in chosen}
        # Row-level target gate: only the already-frozen source row indices survive the parser.
        lab=pd.read_csv(
            RAW+name,
            usecols=TARGET_COLS,
            encoding='utf-8-sig',
            skiprows=lambda line_no: line_no>0 and (line_no-1) not in wanted,
        )
        if len(lab)!=len(chosen):raise RuntimeError(f'target projection count drift {code}: {len(lab)} != {len(chosen)}')
        for z,(_,r) in zip(chosen,lab.iterrows()):
            if str(r['Div']).strip()!=z['division'] or parse_date(r['Date'])!=z['date_iso'] or str(r['HomeTeam']).strip()!=z['home_team'] or str(r['AwayTeam']).strip()!=z['away_team']:
                raise RuntimeError(f'target identity mismatch {z["identity"]}')
            try:hg=int(r['FTHG']); ag=int(r['FTAG'])
            except Exception:raise RuntimeError(f'invalid target {z["identity"]}')
            if hg<0 or ag<0:raise RuntimeError(f'negative target {z["identity"]}')
            out[z['identity']]={'FTHG':hg,'FTAG':ag,'total_goals':hg+ag}
    if len(out)!=TARGET_N:raise RuntimeError(f'target rows {len(out)} != {TARGET_N}')
    return out

def poisson_tail_ge3(mu):
    return 1-math.exp(-mu)*(1+mu+mu*mu/2)

def market_mu(q):
    return float(brentq(lambda m:poisson_tail_ge3(m)-float(q),0.05,8.0,xtol=1e-12,rtol=1e-12))

def nb2_logpmf(y,mu,alpha):
    y=np.asarray(y,float); mu=np.asarray(mu,float); r=1/alpha
    return gammaln(y+r)-gammaln(r)-gammaln(y+1)+r*(math.log(r)-np.log(r+mu))+y*(np.log(mu)-np.log(r+mu))

def fit(anchor,movement,y,candidate):
    anchor=np.asarray(anchor,float); x=np.asarray(movement,float); y=np.asarray(y,float)
    mean=float(x.mean()); sd=float(x.std())
    if sd<1e-10:sd=1.0
    z=(x-mean)/sd
    p0=np.array([0.0,0.0,math.log(0.1)]) if candidate else np.array([0.0,math.log(0.1)])
    def obj(p):
        if candidate:
            b0,g,la=p; eta=np.log(anchor)+b0+g*z; pen=RIDGE_LAMBDA*g*g
        else:
            b0,la=p; eta=np.log(anchor)+b0; pen=0.0
        if np.any(np.abs(eta)>8):return 1e12+1e9*float(np.sum(np.maximum(np.abs(eta)-8,0)))
        a=math.exp(la)
        return -float(nb2_logpmf(y,np.exp(eta),a).sum())+pen
    bounds=[(None,None)]*(len(p0)-1)+[(math.log(1e-4),math.log(3.0))]
    r=minimize(obj,p0,method='L-BFGS-B',bounds=bounds,options={'maxiter':3000,'ftol':1e-12,'gtol':1e-8})
    if not r.success:raise RuntimeError(f'OPTIMIZER_FAIL candidate={candidate}: {r.message}')
    if candidate:b0,g,la=r.x
    else:b0,la=r.x; g=0.0
    return {'beta0':float(b0),'gamma':float(g),'alpha':float(math.exp(la)),'movement_mean':mean,'movement_sd':sd,'fun':float(r.fun),'nit':int(r.nit)}

def predict(model,anchor,movement,candidate):
    anchor=np.asarray(anchor,float); x=np.asarray(movement,float)
    z=(x-model['movement_mean'])/model['movement_sd']
    eta=np.log(anchor)+model['beta0']+(model['gamma']*z if candidate else 0.0)
    mu=np.exp(eta); alpha=model['alpha']; r=1/alpha
    p=np.zeros((len(mu),8),float)
    for k in range(7):
        p[:,k]=np.exp(gammaln(k+r)-gammaln(r)-gammaln(k+1)+r*(np.log(r)-np.log(r+mu))+k*(np.log(mu)-np.log(r+mu)))
    p[:,7]=1-p[:,:7].sum(axis=1)
    if np.any(~np.isfinite(p)) or np.min(p)<-1e-12 or np.max(np.abs(p.sum(axis=1)-1))>1e-10:
        raise RuntimeError('PROBABILITY_AUDIT_FAIL')
    p=np.maximum(p,0); p=p/p.sum(axis=1,keepdims=True)
    return p,mu

def match_metrics(p,y):
    y=np.asarray(y,int); one=np.eye(8)[y]
    ll=-np.log(np.clip(p[np.arange(len(y)),y],1e-15,1))
    brier=np.sum((p-one)**2,axis=1)
    rps=np.sum((np.cumsum(p,axis=1)[:,:-1]-np.cumsum(one,axis=1)[:,:-1])**2,axis=1)/7
    top1=(p.argmax(axis=1)==y).astype(float)
    rank=np.argsort(p,axis=1)[:,-3:]
    top3=np.array([float(y[i] in rank[i]) for i in range(len(y))])
    return {'ll':ll,'brier':brier,'rps':rps,'top1':top1,'top3':top3}

def aggregate(m):
    return {'n':int(len(m['ll'])),'logloss':float(np.mean(m['ll'])),'brier':float(np.mean(m['brier'])),'rps':float(np.mean(m['rps'])),'top1':float(np.mean(m['top1'])),'top3':float(np.mean(m['top3']))}

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    s={'project':'football3','experiment':'C072-N19R1','classification':'REPLICATION_REPRODUCTION_ONLY','formal_weight':0,
       'mirror_repo':MIRROR_REPO,'mirror_revision':MIRROR_REV,'expected_identity_sha256':EXPECTED_ID_SHA,
       'C070F_confirmation1597_opened':False,'N17_reserve_opened':False,'N18_confirmation150_opened':False,'C073_C077_scientific_results_used':False,
       'bootstrap_reps':BOOT_REPS,'bootstrap_seed':BOOT_SEED}
    try:
        sel,sha=reproduce_lock(); s['identity_sha256_reproduced']=sha
        targets=materialize_only_frozen_targets(sel); s['target_rows_materialized']=len(targets); s['target_values_materialized']=2*len(targets)
        qclose=[]; movement=[]; totals=[]; classes=[]; codes=[]
        for z in sel:
            qo=devig_over(z['avg_over25_open'],z['avg_under25_open']); qc=devig_over(z['avg_over25_close'],z['avg_under25_close'])
            qclose.append(qc); movement.append(logit(qc)-logit(qo)); t=targets[z['identity']]['total_goals']; totals.append(t); classes.append(min(t,7)); codes.append(z['source_code'])
        anchor=np.array([market_mu(q) for q in qclose],float); movement=np.asarray(movement,float); totals=np.asarray(totals,int); y=np.asarray(classes,int); codes=np.asarray(codes,object)
        fold_summ=[]; pred_rows=[]; pooled={'b':{k:[] for k in ('ll','brier','rps','top1','top3')},'c':{k:[] for k in ('ll','brier','rps','top1','top3')},'idx':[]}
        for fi,(train_end,test_end) in enumerate(FOLDS,1):
            tr=np.arange(train_end); te=np.arange(train_end,test_end)
            b=fit(anchor[tr],movement[tr],totals[tr],False); c=fit(anchor[tr],movement[tr],totals[tr],True)
            pb,mub=predict(b,anchor[te],movement[te],False); pc,muc=predict(c,anchor[te],movement[te],True)
            mb=match_metrics(pb,y[te]); mc=match_metrics(pc,y[te]); ab=aggregate(mb); ac=aggregate(mc)
            d={k:ac[k]-ab[k] for k in ('logloss','brier','rps','top1','top3')}
            fold_summ.append({'fold':fi,'train_n':int(len(tr)),'test_n':int(len(te)),'baseline':ab,'candidate':ac,'delta':d,'baseline_fit':b,'candidate_fit':c})
            for k in pooled['b']:pooled['b'][k].extend(mb[k].tolist()); pooled['c'][k].extend(mc[k].tolist())
            pooled['idx'].extend(te.tolist())
            for j,ix in enumerate(te):
                pred_rows.append({'rank':int(ix+1),'identity':sel[ix]['identity'],'source_code':str(codes[ix]),'T':int(y[ix]),'total_goals':int(totals[ix]),
                                  'q_over_close':float(qclose[ix]),'movement_logit':float(movement[ix]),'mu_market':float(anchor[ix]),
                                  'mu_baseline':float(mub[j]),'mu_candidate':float(muc[j]),'baseline_probs':pb[j].tolist(),'candidate_probs':pc[j].tolist()})
        mb={k:np.asarray(v) for k,v in pooled['b'].items()}; mc={k:np.asarray(v) for k,v in pooled['c'].items()}; pidx=np.asarray(pooled['idx'],int)
        ab=aggregate(mb); ac=aggregate(mc); delta={k:ac[k]-ab[k] for k in ('logloss','brier','rps','top1','top3')}
        dll=mc['ll']-mb['ll']; rng=np.random.default_rng(BOOT_SEED); boots=np.empty(BOOT_REPS,float)
        for i in range(BOOT_REPS):
            take=rng.integers(0,len(dll),len(dll)); boots[i]=float(np.mean(dll[take]))
        ci=[float(np.quantile(boots,.05)),float(np.quantile(boots,.95))]
        div={}
        for code,_,_ in FILES:
            m=codes[pidx]==code
            div[code]={'n':int(m.sum()),'dlogloss':float(np.mean(dll[m])) if m.any() else None}
        fold_wins=sum(x['delta']['logloss']<0 for x in fold_summ)
        div_wins=sum(v['n']>0 and v['dlogloss']<0 for v in div.values())
        represented=sum(v['n']>0 for v in div.values())
        gates={'dlogloss_lt0':delta['logloss']<0,'bootstrap90_upper_lt0':ci[1]<0,'dbrier_le0':delta['brier']<=0,'drps_le0':delta['rps']<=0,
               'fold_wins_ge3of4':fold_wins>=3,'source_wins_ge6of10':div_wins>=6 and represented==10,'probability_valid':True,'identity_lock_reproduced':True}
        passed=all(gates.values())
        s.update({'oos_n':len(dll),'folds':fold_summ,'pooled':{'baseline':ab,'candidate':ac,'delta':delta},
                  'bootstrap90_dlogloss':ci,'bootstrap_mean_dlogloss':float(boots.mean()),'p_dlogloss_lt0':float(np.mean(boots<0)),
                  'source_code_dlogloss':div,'source_codes_represented':represented,'source_code_logloss_wins':div_wins,'fold_logloss_wins':fold_wins,
                  'gates':gates,'strong_replication_screen':bool(passed and delta['logloss']<=-0.005 and delta['rps']<=-0.0005 and fold_wins==4),
                  'terminal':'C072N19R1_REPLICATION_PASS' if passed else 'C072N19R1_REPLICATION_PARK'})
        with PRED.open('w',encoding='utf-8',newline='\n') as f:
            for r in pred_rows:f.write(json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n')
    except Exception as e:
        s['terminal']='C072N19R1_TECHNICAL_STOP'; s['error']=f'{type(e).__name__}:{e}'
    SUMMARY.write_text(json.dumps(s,indent=2,ensure_ascii=False,sort_keys=True)+'\n',encoding='utf-8')
    print(json.dumps(s,indent=2,ensure_ascii=False,sort_keys=True))
    if s['terminal']=='C072N19R1_TECHNICAL_STOP':raise SystemExit(1)
if __name__=='__main__':main()
