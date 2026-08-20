#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, html as html_lib, json, math, re
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from scipy.optimize import brentq
from scipy.stats import poisson
from sklearn.linear_model import LogisticRegression

EXPECTED_NEW_SHA='a49e61df94d0f9c368b314829901f0d64d69ad25c51813551a298307e15e56cf'
EXPECTED_OLD_SHA='65491bb169bc1257ac802970a9e235324b55085863ba53fdf6c84a74b275a559'
EXPECTED_NEW_N=1000
EXPECTED_TRAIN_N=1734
EXPECTED_OLD_N=2000
PRICE_PAIRS=[('O05','U05'),('O15','U15'),('O25','U25'),('O35','U35'),('O45','U45')]
RESULT_HEADERS=['id','matchDate','Country','League','Season','homeTeam','awayTeam','referee','FTHG','FTAG','FTR']
PAGES={
 'TR':'https://footiqo.com/database/leagues/turkey-super-lig/',
 'GR':'https://footiqo.com/database/leagues/greece-super-league/',
 'BR':'https://footiqo.com/database/leagues/brazil-serie-a/',
 'MLS':'https://footiqo.com/database/leagues/usa-mls/',
}
AJAX='https://footiqo.com/wp-admin/admin-ajax.php'
ACTION='get_wdtable'; NONCE_FIELD='wdtNonce'; PAGE_SIZE=500; MAX_POST_REQUESTS=140


def find_one(root:Path,name:str)->Path:
    hits=list(root.rglob(name))
    if len(hits)!=1: raise RuntimeError(f'{name}: expected one hit, got {len(hits)}')
    return hits[0]

def ordered_sha(xs)->str:
    return hashlib.sha256(('\n'.join(map(str,xs))+'\n').encode()).hexdigest()

def norm(x)->str:
    if x is None:return ''
    s=html_lib.unescape(str(x)).strip()
    if '<' in s and '>' in s:s=BeautifulSoup(s,'html.parser').get_text(' ',strip=True)
    return re.sub(r'\s+',' ',s).strip()

def parse_price(x):
    try:v=float(str(x).strip().replace(',','.'))
    except Exception:return None
    return v if math.isfinite(v) and v>1 else None

def devig_pair(o,u):
    o=parse_price(o); u=parse_price(u)
    if o is None or u is None:return None
    a=1.0/o; b=1.0/u
    return a/(a+b)

def logit(q):
    q=np.clip(np.asarray(q,dtype=float),1e-6,1-1e-6)
    return np.log(q/(1-q))

def pava_decreasing(vals):
    blocks=[]
    for i,v in enumerate([float(x) for x in vals]):
        blocks.append([v,1.0,i,i])
        while len(blocks)>=2 and blocks[-2][0] < blocks[-1][0]-1e-15:
            a=blocks[-2]; b=blocks[-1]; w=a[1]+b[1]
            blocks[-2:]=[[(a[0]*a[1]+b[0]*b[1])/w,w,a[2],b[3]]]
    out=np.empty(len(vals),dtype=float)
    for m,w,s,e in blocks: out[s:e+1]=m
    return np.clip(out,1e-6,1-1e-6)

def fit_line_calibrators(train):
    models=[]; report=[]; T=train['T'].to_numpy(int)
    for k,(oc,uc) in enumerate(PRICE_PAIRS,start=1):
        q=np.array([devig_pair(o,u) for o,u in zip(train[oc],train[uc])],dtype=object)
        ok=np.array([x is not None for x in q]); x=np.array([float(z) for z in q[ok]],dtype=float); y=(T[ok]>=k).astype(int)
        if len(x)<200 or len(np.unique(y))<2: raise RuntimeError(f'calibrator support failure k={k} n={len(x)}')
        m=LogisticRegression(C=1.0,penalty='l2',solver='lbfgs',max_iter=2000,class_weight=None,random_state=0)
        m.fit(logit(x).reshape(-1,1),y); models.append(m)
        report.append({'k':k,'n':int(len(x)),'positives':int(y.sum()),'intercept':float(m.intercept_[0]),'slope':float(m.coef_[0,0])})
    return models,report

def calibrated_tails(df,models):
    out=np.empty((len(df),5),float)
    for j,((oc,uc),m) in enumerate(zip(PRICE_PAIRS,models)):
        q=np.array([devig_pair(o,u) for o,u in zip(df[oc],df[uc])],dtype=object)
        if any(x is None for x in q): raise RuntimeError(f'test missing market pair {oc}/{uc}')
        out[:,j]=m.predict_proba(logit(np.array(q,dtype=float)).reshape(-1,1))[:,1]
    return out

def mu_from_q3(q):
    q=float(np.clip(q,1e-7,1-1e-7))
    return brentq(lambda mu:poisson.sf(2,mu)-q,0.05,10.0,maxiter=100)

def build_distributions(qcal):
    n=len(qcal); b=np.zeros((n,8)); c=np.zeros((n,8)); max_res=0.0
    for i in range(n):
        mu=mu_from_q3(qcal[i,2]); b[i,:7]=poisson.pmf(np.arange(7),mu); b[i,7]=poisson.sf(6,mu)
        qs=pava_decreasing(qcal[i]); q5=qs[4]; pge5=poisson.sf(4,mu)
        if pge5<=0: raise RuntimeError('poisson pge5 zero')
        q6=q5*poisson.sf(5,mu)/pge5; q7=q5*poisson.sf(6,mu)/pge5
        tails=np.array([qs[0],qs[1],qs[2],qs[3],qs[4],q6,q7])
        cp=np.array([1-tails[0],tails[0]-tails[1],tails[1]-tails[2],tails[2]-tails[3],tails[3]-tails[4],tails[4]-tails[5],tails[5]-tails[6],tails[6]],float)
        cp=np.where((cp<0)&(cp>=-1e-12),0,cp)
        if cp.min()<0: raise RuntimeError(f'negative candidate mass {cp.min()} row {i}')
        s=cp.sum(); max_res=max(max_res,abs(s-1.0))
        if abs(s-1.0)>1e-10: raise RuntimeError(f'prob residual {s-1}')
        c[i]=cp/s
    return b,c,{'max_probability_residual':float(max_res)}
def scores(p,y):
    eps=1e-15; n=len(y); oh=np.eye(8)[y]
    ll=float(-np.mean(np.log(np.clip(p[np.arange(n),y],eps,1)))); br=float(np.mean(np.sum((p-oh)**2,axis=1)))
    rps=float(np.mean(np.sum((np.cumsum(p,axis=1)[:,:-1]-np.cumsum(oh,axis=1)[:,:-1])**2,axis=1)/7.0))
    pred=np.argmax(p,axis=1); top1=float(np.mean(pred==y)); top3=float(np.mean([yy in np.argpartition(row,-3)[-3:] for row,yy in zip(p,y)]))
    ent=float(np.mean(-np.sum(p*np.log(np.clip(p,eps,1)),axis=1)))
    return {'LogLoss':ll,'Brier':br,'RPS':rps,'Top1':top1,'Top3':top3,'T2_mode_fraction':float(np.mean(pred==2)),'entropy':ent}
def bootstrap_dll(b,c,y,reps=5000,seed=72020):
    loss=np.log(np.clip(b[np.arange(len(y)),y],1e-15,1))-np.log(np.clip(c[np.arange(len(y)),y],1e-15,1))
    rng=np.random.default_rng(seed); vals=np.empty(reps); n=len(y)
    for r in range(reps): vals[r]=float(np.mean(loss[rng.integers(0,n,n)]))
    lo,hi=np.quantile(vals,[0.05,0.95]); return {'mean':float(vals.mean()),'lo90':float(lo),'hi90':float(hi),'p_lt_0':float(np.mean(vals<0))}

def table_headers(t):
    h=[norm(x.get_text(' ',strip=True)) for x in t.find_all('th')]
    if not h:
        tr=t.find('tr'); h=[norm(x.get_text(' ',strip=True)) for x in tr.find_all(['th','td'])] if tr else []
    return h
def payload(nonce,start,season):
    body={'draw':'1','start':str(start),'length':str(PAGE_SIZE),'search[value]':'','search[regex]':'false',NONCE_FIELD:nonce}
    for i,h in enumerate(RESULT_HEADERS):
        body[f'columns[{i}][data]']=str(i); body[f'columns[{i}][name]']=h; body[f'columns[{i}][searchable]']='true'; body[f'columns[{i}][orderable]']='true'
        body[f'columns[{i}][search][value]']=season if h=='Season' else ''; body[f'columns[{i}][search][regex]']='false'
    return body
def as_int(x):
    try:return int(x)
    except Exception:return None
def parse_goal(x):
    try:v=int(str(x).strip())
    except Exception:return None
    return v if v>=0 else None

def fetch_test_results(sel,summary):
    sess=requests.Session(); sess.headers.update({'User-Agent':'Mozilla/5.0 football3-N20 frozen P1000 evaluation','Accept-Language':'en-US,en;q=0.9'})
    out=[]; transported=0; post=0; stats={}
    for code,url in PAGES.items():
        sub=sel.loc[sel.sourceCode==code].copy(); seasons=sorted(sub.Season.astype(str).unique().tolist()); st={'selected':int(len(sub)),'seasons':seasons,'tables':[],'post_requests':0,'selected_rows_materialized':0}
        page=sess.get(url,timeout=45,allow_redirects=True); st['page_status']=int(page.status_code); page.raise_for_status(); soup=BeautifulSoup(page.text,'html.parser'); tables=[]
        for t in soup.find_all('table'):
            if table_headers(t)!=RESULT_HEADERS: continue
            raw=str(t.get('data-wpdatatable_id',''))
            if not raw.isdigit(): continue
            tid=int(raw); nonce_id=f'wdtNonceFrontendServerSide_{tid}'; nodes=[x for x in soup.find_all('input') if str(x.get('id',''))==nonce_id and str(x.get('name',''))==nonce_id]
            if len(nodes)==1 and str(nodes[0].get('type','')).lower()=='hidden' and str(nodes[0].get('value') or ''): tables.append((tid,str(nodes[0].get('value'))))
        if not tables: raise RuntimeError(f'{code}: no result table')
        st['tables']=[x[0] for x in tables]; req_headers={'Accept':'application/json, text/javascript, */*; q=0.01','Content-Type':'application/x-www-form-urlencoded; charset=UTF-8','X-Requested-With':'XMLHttpRequest','Origin':'https://footiqo.com','Referer':url}; seen_rows=[]
        wanted_by_season={s:set(sub.loc[sub.Season.astype(str)==s,'id'].astype(str)) for s in seasons}
        for season in seasons:
            for tid,nonce in tables:
                start=0; expected=None
                while True:
                    if post>=MAX_POST_REQUESTS: raise RuntimeError('result POST budget exceeded')
                    body=payload(nonce,start,season); post+=1; st['post_requests']+=1
                    try: rr=sess.post(AJAX,params={'action':ACTION,'table_id':str(tid)},data=body,headers=req_headers,timeout=45,allow_redirects=True)
                    finally: body[NONCE_FIELD]='<redacted>'
                    if not 200<=rr.status_code<300: raise RuntimeError(f'{code}/{season}/table{tid}: HTTP {rr.status_code}')
                    x=rr.json(); rf=as_int(x.get('recordsFiltered')) if isinstance(x,dict) else None; data=x.get('data',[]) if isinstance(x,dict) else None
                    if rf is None or not isinstance(data,list): raise RuntimeError('result AJAX shape')
                    if expected is None: expected=rf
                    elif expected!=rf: raise RuntimeError('recordsFiltered drift')
                    if any(not isinstance(row,list) or len(row)!=len(RESULT_HEADERS) for row in data): raise RuntimeError('result row schema drift')
                    for row in data:
                        rid=norm(row[0]); rseason=norm(row[4])
                        if rid not in wanted_by_season[season]: transported+=1; continue
                        if rseason!=season: raise RuntimeError(f'selected season mismatch {code} {rid}')
                        hg=parse_goal(norm(row[8])); ag=parse_goal(norm(row[9]))
                        if hg is None or ag is None: continue
                        seen_rows.append({'sourceCode':code,'id':rid,'matchDate_result':norm(row[1]),'Season_result':rseason,'homeTeam_result':norm(row[5]),'awayTeam_result':norm(row[6]),'FTHG':hg,'FTAG':ag,'table_id':tid})
                    start+=len(data)
                    if start>=rf: break
                    if not data: raise RuntimeError('empty page before filtered count')
        by=defaultdict(list)
        for r in seen_rows: by[(r['sourceCode'],r['id'])].append(r)
        for key,rows in by.items():
            sig={(r['matchDate_result'],r['Season_result'],r['homeTeam_result'],r['awayTeam_result'],r['FTHG'],r['FTAG']) for r in rows}
            if len(sig)>1: raise RuntimeError(f'conflicting result rows {key}')
            out.append(rows[0])
        st['selected_rows_materialized']=len(by); stats[code]=st
    summary['result_table_post_requests']=post; summary['transported_nonselected_rows_labels_not_decoded']=transported; summary['source_fetch_stats']=stats
    return pd.DataFrame(out)

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--n16-dir',required=True); ap.add_argument('--n17-dir',required=True); ap.add_argument('--n20-dir',required=True); ap.add_argument('--out-dir',required=True); a=ap.parse_args(); out=Path(a.out_dir); out.mkdir(parents=True,exist_ok=True)
    old=pd.read_csv(find_one(Path(a.n16_dir),'c072n16r1_footiqo_new2000_zero_label.csv'),dtype=str,keep_default_na=False); trainlab=pd.read_csv(find_one(Path(a.n17_dir),'development_join_audit.csv'),dtype=str,keep_default_na=False); sel=pd.read_csv(find_one(Path(a.n20_dir),'c072n20_p1000_zero_label.csv'),dtype=str,keep_default_na=False)
    summary={'schema':'C072N20_P1000_EVALUATION_V1','classification':'POST_VIEW_DEVELOPMENT_REPLICATION_PILOT','formal_weight':0,'expected_new_identity_sha256':EXPECTED_NEW_SHA,'target_labels_opened':0,'target_values_materialized':0,'N17_reserve266_opened':False,'N18_confirmation150_opened':False,'C070F1597_opened':False,'C073_C077_scientific_results_used':False}
    if len(old)!=EXPECTED_OLD_N or ordered_sha(old.identity_sha256)!=EXPECTED_OLD_SHA: raise RuntimeError('old2000 reproduction failed')
    if len(sel)!=EXPECTED_NEW_N or ordered_sha(sel.identity_sha256)!=EXPECTED_NEW_SHA: raise RuntimeError('new1000 identity lock reproduction failed')
    if len(trainlab)!=EXPECTED_TRAIN_N or trainlab.identity_sha256.nunique()!=EXPECTED_TRAIN_N: raise RuntimeError('N17 training receipt mismatch')
    train=old.merge(trainlab[['identity_sha256','FTHG','FTAG']],on='identity_sha256',how='inner',validate='one_to_one')
    if len(train)!=EXPECTED_TRAIN_N: raise RuntimeError(f'training merge {len(train)}')
    train['hg']=pd.to_numeric(train.FTHG,errors='raise').astype(int); train['ag']=pd.to_numeric(train.FTAG,errors='raise').astype(int); train['T']=np.minimum(train.hg+train.ag,7)
    models,calrep=fit_line_calibrators(train); summary['calibrators']=calrep; summary['training_rows_reused_consumed']=int(len(train))
    res=fetch_test_results(sel,summary)
    if res.empty: raise RuntimeError('no selected results fetched')
    if res.duplicated(['sourceCode','id']).any(): raise RuntimeError('duplicate fetched results')
    joined=sel.merge(res,on=['sourceCode','id'],how='left',validate='one_to_one'); exact=(joined['matchDate'].astype(str)==joined['matchDate_result'].astype(str))&(joined['Season'].astype(str)==joined['Season_result'].astype(str))&(joined['homeTeam'].astype(str)==joined['homeTeam_result'].astype(str))&(joined['awayTeam'].astype(str)==joined['awayTeam_result'].astype(str)); complete=exact & joined.FTHG.notna() & joined.FTAG.notna()
    summary['target_join_rows']=int(complete.sum()); summary['target_identity_mismatches']=int((~exact.fillna(False)).sum()); summary['target_labels_opened']=int(complete.sum()); summary['target_values_materialized']=int(2*complete.sum())
    if int(complete.sum())!=EXPECTED_NEW_N:
        summary['terminal']='C072N20_P1000_STOP_COVERAGE'; (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,sort_keys=True)+'\n'); print(json.dumps(summary,ensure_ascii=False,indent=2)); return 0
    test=joined.loc[complete].copy(); test['hg']=test.FTHG.astype(int); test['ag']=test.FTAG.astype(int); test['T']=np.minimum(test.hg+test.ag,7).astype(int)
    if ordered_sha(test.identity_sha256.astype(str).tolist())!=EXPECTED_NEW_SHA: raise RuntimeError('joined order identity hash drift')
    qcal=calibrated_tails(test,models); b,c,paudit=build_distributions(qcal); y=test.T.to_numpy(int); bm=scores(b,y); cm=scores(c,y); boot=bootstrap_dll(b,c,y); delta={k:float(cm[k]-bm[k]) for k in ['LogLoss','Brier','RPS','Top1','Top3','T2_mode_fraction','entropy']}
    per={}
    for code in sorted(test.sourceCode.unique()):
        ix=np.where(test.sourceCode.to_numpy()==code)[0]; per[code]={'n':int(len(ix)),'dLogLoss':float(scores(c[ix],y[ix])['LogLoss']-scores(b[ix],y[ix])['LogLoss'])}
    source_wins=sum(v['dLogLoss']<0 for v in per.values()); gates={'join_exact_1000':len(test)==1000,'dLogLoss_lt_0':delta['LogLoss']<0,'bootstrap90_upper_lt_0':boot['hi90']<0,'dBrier_le_0':delta['Brier']<=0,'dRPS_le_0':delta['RPS']<=0,'source_wins_ge_3of4':source_wins>=3,'prob_residual_le_1e10':paudit['max_probability_residual']<=1e-10,'sealed_boundaries_hold':not summary['N17_reserve266_opened'] and not summary['N18_confirmation150_opened'] and not summary['C070F1597_opened']}; terminal='C072N20_P1000_PILOT_SIGNAL' if all(gates.values()) else 'C072N20_P1000_PILOT_NO_SIGNAL'
    summary.update({'baseline':bm,'candidate':cm,'delta_candidate_minus_baseline':delta,'bootstrap_dLogLoss':boot,'per_source':per,'source_LL_wins':int(source_wins),'probability_audit':paudit,'gates':gates,'terminal':terminal}); (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8'); test[['identity_sha256','sourceCode','id','Season','matchDate','homeTeam','awayTeam','FTHG','FTAG','T']].to_csv(out/'consumed_n20_p1000_receipt.csv',index=False); print(json.dumps(summary,ensure_ascii=False,indent=2,sort_keys=True)); return 0

if __name__=='__main__': raise SystemExit(main())
