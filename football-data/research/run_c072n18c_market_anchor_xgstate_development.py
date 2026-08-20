#!/usr/bin/env python3
from __future__ import annotations

import gzip
import hashlib
import json
import math
import re
from pathlib import Path

import numpy as np
import requests
from bs4 import BeautifulSoup
from scipy.optimize import brentq, minimize
from scipy.special import gammaln

ZERO_DIR=Path('football-data/research/_c072n18b_zero_label_join')
TARGET_PATH=ZERO_DIR/'c072n18b_target550_zero_label.jsonl.gz'
DEV_IDS_PATH=ZERO_DIR/'c072n18b_dev400_ids.txt'
CONF_IDS_PATH=ZERO_DIR/'c072n18b_confirmation150_ids.txt'
N18B_SUMMARY=ZERO_DIR/'c072n18b_summary.json'
OUTDIR=Path('football-data/research/_c072n18c_development')
SUMMARY_PATH=OUTDIR/'c072n18c_summary.json'
PRED_PATH=OUTDIR/'c072n18c_oos_predictions.jsonl.gz'

EXPECTED_TARGET550_SEMANTIC_SHA='b72fd9225d51178db533bee129bc9406a794d127b511bdcaed4b65ffd2339b9a'
EXPECTED_DEV_IDS_SHA='55181a078d39d9ac53881aa0c377d6c6cb819c06053bd75609841a13caa1dbdf'
EXPECTED_CONF_IDS_SHA='774be269e30254af29614210401b52c23b0f3a4e79a7945e98014d50590ea90f'
RIDGE_LAMBDA=1.0; BOOT_REPS=3000; BOOT_SEED=72018
RESULT_HEADERS=['id','matchDate','Country','League','Season','homeTeam','awayTeam','referee','FTHG','FTAG','FTR']
AJAX='https://footiqo.com/wp-admin/admin-ajax.php'; ACTION='get_wdtable'; NONCE_FIELD='wdtNonce'
PAGES={
'EPL':('https://footiqo.com/database/leagues/england-premier-league/','2024/2025'),
'LALIGA':('https://footiqo.com/database/leagues/spain-laliga/','2024/2025'),
'BUNDESLIGA':('https://footiqo.com/database/leagues/germany-bundesliga/','2024/2025'),
'SERIEA':('https://footiqo.com/database/leagues/italy-serie-a/','2024/2025'),
'LIGUE1':('https://footiqo.com/database/leagues/france-ligue-1/','2024/2025'),
'MLS':('https://footiqo.com/database/leagues/usa-mls/','2024')}


def sha256_file(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for c in iter(lambda:f.read(1<<20),b''): h.update(c)
    return h.hexdigest()

def sha256_gzip_payload(p):
    h=hashlib.sha256()
    with gzip.open(p,'rb') as f:
        for c in iter(lambda:f.read(1<<20),b''): h.update(c)
    return h.hexdigest()

def norm(x):
    s='' if x is None else str(x).strip()
    if '<' in s and '>' in s: s=BeautifulSoup(s,'html.parser').get_text(' ',strip=True)
    return re.sub(r'\s+',' ',s).strip()

def read_zero_label():
    sx=json.loads(N18B_SUMMARY.read_text(encoding='utf-8'))
    if sx.get('status')!='PASS_N18B2_ZERO_LABEL_TARGET_MARKET_JOIN': raise RuntimeError('N18B2 status mismatch')
    if sha256_gzip_payload(TARGET_PATH)!=EXPECTED_TARGET550_SEMANTIC_SHA: raise RuntimeError('target550 semantic hash mismatch')
    if sha256_file(DEV_IDS_PATH)!=EXPECTED_DEV_IDS_SHA or sha256_file(CONF_IDS_PATH)!=EXPECTED_CONF_IDS_SHA: raise RuntimeError('split hash mismatch')
    dev_ids=[int(x) for x in DEV_IDS_PATH.read_text().split()]; conf_ids=[int(x) for x in CONF_IDS_PATH.read_text().split()]
    if len(dev_ids)!=400 or len(conf_ids)!=150 or set(dev_ids)&set(conf_ids): raise RuntimeError('split identity mismatch')
    rows=[]
    with gzip.open(TARGET_PATH,'rt',encoding='utf-8') as f:
        for line in f: rows.append(json.loads(line))
    by_id={int(x['footiqo_id']):x for x in rows}
    dev=[by_id[i] for i in dev_ids]; dev.sort(key=lambda x:(x['match_time_local'],int(x['footiqo_id'])))
    return dev,set(conf_ids)

def table_headers(t):
    h=[norm(x.get_text(' ',strip=True)) for x in t.find_all('th')]
    if not h:
        tr=t.find('tr'); h=[norm(x.get_text(' ',strip=True)) for x in tr.find_all(['th','td'])] if tr else []
    return h

def visible_seasons(t,h):
    if 'Season' not in h:return []
    j=h.index('Season'); out=[]
    for tr in t.find_all('tr')[1:]:
        c=[norm(x.get_text(' ',strip=True)) for x in tr.find_all(['th','td'])]
        if len(c)>j and c[j] and c[j]!='Season':out.append(c[j])
    return sorted(set(out))
def start_year(s):
    m=re.match(r'\s*(\d{4})',s); return int(m.group(1)) if m else None

def resolve_result_table(page_html):
    soup=BeautifulSoup(page_html,'html.parser'); cand=[]
    for t in soup.find_all('table'):
        h=table_headers(t)
        if h!=RESULT_HEADERS: continue
        tid=str(t.get('data-wpdatatable_id',''))
        if not tid.isdigit(): continue
        seasons=visible_seasons(t,h); yrs=[start_year(s) for s in seasons]; yrs=[y for y in yrs if y is not None]
        if yrs: cand.append((min(yrs),t,int(tid),seasons))
    if len(cand)<2: raise RuntimeError(f'result historical protocol expected >=2 exact tables got {len(cand)}')
    my=min(x[0] for x in cand); hist=[x for x in cand if x[0]==my]
    if len(hist)!=1: raise RuntimeError(f'result historical protocol nonunique earliest got {len(hist)}')
    _,t,tid,seasons=hist[0]; return t,tid,seasons

def payload(nonce,rid,season):
    b={'draw':'1','start':'0','length':'10','search[value]':'','search[regex]':'false',NONCE_FIELD:nonce}
    for i,h in enumerate(RESULT_HEADERS):
        b[f'columns[{i}][data]']=str(i); b[f'columns[{i}][name]']=h; b[f'columns[{i}][searchable]']='true'; b[f'columns[{i}][orderable]']='true'; b[f'columns[{i}][search][value]']=str(rid) if h=='id' else season if h=='Season' else ''; b[f'columns[{i}][search][regex]']='false'
    return b
def parse_goal(x):
    try:v=int(norm(x)); return v if v>=0 else None
    except:return None

def fetch_dev_results(dev,conf):
    s=requests.Session(); s.headers.update({'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/142 Safari/537.36 football3-n18c','Accept-Language':'en-US,en;q=0.9'})
    meta={}
    for code,(url,season) in PAGES.items():
        r=s.get(url,timeout=45); r.raise_for_status(); _,tid,seasons=resolve_result_table(r.text); soup=BeautifulSoup(r.text,'html.parser'); dom=f'wdtNonceFrontendServerSide_{tid}'; nodes=[x for x in soup.find_all('input') if str(x.get('id',''))==dom and str(x.get('name',''))==dom]
        if len(nodes)!=1 or not str(nodes[0].get('value') or ''): raise RuntimeError(f'NONCE_PROTOCOL {code}')
        meta[code]={'url':url,'season':season,'tid':tid,'nonce':str(nodes[0].get('value')),'visible':seasons}
    out={}; npost=0
    base={'Accept':'application/json, text/javascript, */*; q=0.01','Content-Type':'application/x-www-form-urlencoded; charset=UTF-8','X-Requested-With':'XMLHttpRequest','Origin':'https://footiqo.com'}
    for z in dev:
        rid=int(z['footiqo_id']); code=z['source_code']
        if rid in conf: raise RuntimeError('confirmation ID reached request layer')
        m=meta[code]; body=payload(m['nonce'],rid,m['season']); hdr=dict(base); hdr['Referer']=m['url']; npost+=1
        rr=s.post(AJAX,params={'action':ACTION,'table_id':str(m['tid'])},data=body,headers=hdr,timeout=45); body[NONCE_FIELD]='<redacted>'; rr.raise_for_status(); x=rr.json(); data=x.get('data') if isinstance(x,dict) else None
        try:rf=int(x.get('recordsFiltered'))
        except: raise RuntimeError(f'AJAX_METADATA id={rid}')
        if rf!=1 or not isinstance(data,list) or len(data)!=1 or not isinstance(data[0],list) or len(data[0])!=len(RESULT_HEADERS): raise RuntimeError(f'TARGET_ONLY_FILTER_FAILED id={rid} rf={rf}')
        row=data[0]; gid=int(norm(row[0]))
        if gid!=rid or gid in conf: raise RuntimeError(f'UNAUTHORIZED_RESULT_ID requested={rid} got={gid}')
        d={RESULT_HEADERS[i]:norm(row[i]) for i in range(len(RESULT_HEADERS))}; hg,ag=parse_goal(d['FTHG']),parse_goal(d['FTAG'])
        if hg is None or ag is None: raise RuntimeError(f'INVALID_SCORE {rid}')
        if d['Season']!=z['season'] or d['homeTeam']!=z['home_team'] or d['awayTeam']!=z['away_team']: raise RuntimeError(f'IDENTITY_MISMATCH {rid}')
        out[rid]={'total_goals':hg+ag}
    if len(out)!=400: raise RuntimeError(f'development result count {len(out)}')
    return out,{'post_requests':npost,'requested_dev_ids':len(out),'returned_dev_ids':len(out),'confirmation_ids_requested':0,'confirmation_rows_returned':0}

def poisson_tail_ge3(mu): return 1-math.exp(-mu)*(1+mu+mu*mu/2)
def market_mu(q): return float(brentq(lambda m:poisson_tail_ge3(m)-float(q),0.05,8.0,xtol=1e-12,rtol=1e-12))
def nb2_logpmf(y,mu,a):
    y=np.asarray(y,float);mu=np.asarray(mu,float);r=1/a
    return gammaln(y+r)-gammaln(r)-gammaln(y+1)+r*(math.log(r)-np.log(r+mu))+y*(np.log(mu)-np.log(r+mu))
def fit(X,y,anchor,cand):
    X=np.asarray(X,float);y=np.asarray(y,float);anchor=np.asarray(anchor,float)
    if cand:mean=X.mean(0);sd=np.where(X.std(0)<1e-8,1.,X.std(0));Z=(X-mean)/sd;p0=np.r_[np.zeros(17),math.log(.1)]
    else:mean=np.zeros(16);sd=np.ones(16);Z=np.zeros_like(X);p0=np.array([0.,math.log(.1)])
    def obj(p):
        if cand:b0=p[0];b=p[1:17];la=p[17];eta=np.log(anchor)+b0+Z.dot(b);pen=RIDGE_LAMBDA*np.dot(b,b)
        else:b0=p[0];la=p[1];eta=np.log(anchor)+b0;pen=0
        if np.any(abs(eta)>8):return 1e12+1e9*np.sum(np.maximum(abs(eta)-8,0))
        ll=nb2_logpmf(y,np.exp(eta),math.exp(la));return -float(ll.sum())+float(pen)
    bd=[(None,None)]*(len(p0)-1)+[(math.log(1e-4),math.log(3.0))];r=minimize(obj,p0,method='L-BFGS-B',bounds=bd,options={'maxiter':3000,'ftol':1e-12,'gtol':1e-8})
    if not r.success:raise RuntimeError(f'OPTIMIZER_FAIL cand={cand} {r.message}')
    p=r.x
    return {'beta0':float(p[0]),'beta':p[1:17].copy() if cand else np.zeros(16),'alpha':float(math.exp(p[17] if cand else p[1])),'mean':mean,'sd':sd}
def predict(m,X,a,cand):
    X=np.asarray(X,float);a=np.asarray(a,float);eta=np.log(a)+m['beta0']+(((X-m['mean'])/m['sd']).dot(m['beta']) if cand else 0);mu=np.exp(eta);r=1/m['alpha'];p=np.zeros((len(mu),8))
    for k in range(7):p[:,k]=np.exp(gammaln(k+r)-gammaln(r)-gammaln(k+1)+r*(np.log(r)-np.log(r+mu))+k*(np.log(mu)-np.log(r+mu)))
    p[:,7]=1-p[:,:7].sum(1)
    if np.any(~np.isfinite(p)) or np.any(p< -1e-12) or np.max(abs(p.sum(1)-1))>1e-10:raise RuntimeError('PROBABILITY_AUDIT_FAIL')
    return np.maximum(p,0),mu
def metrics(p,y):
    y=np.asarray(y,int);o=np.eye(8)[y];ll=-np.log(np.clip(p[np.arange(len(y)),y],1e-15,1));br=((p-o)**2).sum(1);rps=((np.cumsum(p,1)[:,:-1]-np.cumsum(o,1)[:,:-1])**2).sum(1)/7;t1=np.argmax(p,1)==y;t3=np.array([y[i] in np.argsort(p[i])[-3:] for i in range(len(y))]);return ll,br,rps,t1,t3
def agg(m):
    return {'n':len(m[0]),'logloss':float(np.mean(m[0])),'brier':float(np.mean(m[1])),'rps':float(np.mean(m[2])),'top1':float(np.mean(m[3])),'top3':float(np.mean(m[4]))}
def main():
    OUTDIR.mkdir(parents=True,exist_ok=True);S={'project':'football3','experiment':'C072-N18C','classification':'DEVELOPMENT_NOT_CONFIRMATION','formal_weight':0,'C073_C077_scientific_results_used':False,'C070F_confirmation1597_opened':False,'sealed_reserves_opened':False,'confirmation150_result_values_materialized':0,'confirmation150_result_requests':0,'bootstrap_reps':BOOT_REPS,'bootstrap_seed':BOOT_SEED}
    try:
        dev,conf=read_zero_label();S['target550_semantic_sha256_observed']=sha256_gzip_payload(TARGET_PATH);res,trn=fetch_dev_results(dev,conf);S['transport']=trn;S['development_result_rows_materialized']=len(res);S['development_result_values_materialized']=2*len(res)
        ids=np.array([int(z['footiqo_id']) for z in dev]);X=np.array([z['features16'] for z in dev],float);q=np.array([z['q_over25'] for z in dev],float);a=np.array([market_mu(v) for v in q]);tot=np.array([res[int(i)]['total_goals'] for i in ids]);y=np.minimum(tot,7);lg=np.array([z['source_code'] for z in dev],object)
        folds=[(160,220),(220,280),(280,340),(340,400)];fs=[];pred=[];LB=[];LC=[];BB=[];BC=[];RB=[];RC=[];T1B=[];T1C=[];T3B=[];T3C=[];pi=[]
        for fi,(c,d) in enumerate(folds,1):
            tr=np.arange(c);te=np.arange(c,d);b=fit(X[tr],tot[tr],a[tr],False);cmod=fit(X[tr],tot[tr],a[tr],True);pb,mub=predict(b,X[te],a[te],False);pc,muc=predict(cmod,X[te],a[te],True);mb,mc=metrics(pb,y[te]),metrics(pc,y[te]);ab,ac=agg(mb),agg(mc);delta={k:ac[k]-ab[k] for k in ('logloss','brier','rps','top1','top3')};fs.append({'fold':fi,'train_n':len(tr),'test_n':len(te),'baseline':ab,'candidate':ac,'delta':delta,'baseline_alpha':b['alpha'],'candidate_alpha':cmod['alpha']});LB+=list(mb[0]);LC+=list(mc[0]);BB+=list(mb[1]);BC+=list(mc[1]);RB+=list(mb[2]);RC+=list(mc[2]);T1B+=list(mb[3]);T1C+=list(mc[3]);T3B+=list(mb[4]);T3C+=list(mc[4]);pi+=list(te)
            for j,ix in enumerate(te):pred.append({'footiqo_id':int(ids[ix]),'fold':fi,'source_code':str(lg[ix]),'T':int(y[ix]),'baseline_probs':pb[j].tolist(),'candidate_probs':pc[j].tolist(),'mu_market':float(a[ix]),'mu_baseline':float(mub[j]),'mu_candidate':float(muc[j])})
        AB=agg((np.array(LB),np.array(BB),np.array(RB),np.array(T1B),np.array(T3B)));AC=agg((np.array(LC),np.array(BC),np.array(RC),np.array(T1C),np.array(T3C)));D={k:AC[k]-AB[k] for k in ('logloss','brier','rps','top1','top3')};dll=np.array(LC)-np.array(LB);rng=np.random.default_rng(BOOT_SEED);boots=np.array([np.mean(dll[rng.integers(0,len(dll),len(dll))]) for _ in range(BOOT_REPS)]);ci=[float(np.quantile(boots,.05)),float(np.quantile(boots,.95))];pidx=np.array(pi);ls={}
        for league in ['EPL','LALIGA','BUNDESLIGA','SERIEA','LIGUE1','MLS']:
            m=lg[pidx]==league;ls[league]={'n':int(m.sum()),'dlogloss':float(np.mean((np.array(LC)-np.array(LB))[m])) if m.any() else None}
        fw=sum(x['delta']['logloss']<0 for x in fs);lw=sum(v['n'] and v['dlogloss']<0 for v in ls.values());g={'pooled_dlogloss_lt0':D['logloss']<0,'bootstrap90_upper_lt0':ci[1]<0,'pooled_dbrier_le0':D['brier']<=0,'pooled_drps_le0':D['rps']<=0,'fold_wins_ge3of4':fw>=3,'league_wins_ge4of6':lw>=4,'probability_audit':True,'confirmation_boundary_clean':True};passed=all(g.values());S.update({'oos_n':len(dll),'folds':fs,'pooled':{'baseline':AB,'candidate':AC,'delta':D},'bootstrap90_dlogloss':ci,'bootstrap_mean_dlogloss':float(boots.mean()),'p_dlogloss_lt0':float((boots<0).mean()),'league_dlogloss':ls,'fold_logloss_wins':fw,'league_logloss_wins':lw,'gates':g,'breakthrough_screen':bool(passed and D['logloss']<=-.01 and D['rps']<=-.001 and fw==4),'terminal':'C072N18C_DEVELOPMENT_PASS' if passed else 'C072N18C_DEVELOPMENT_PARK'})
        with gzip.open(PRED_PATH,'wt',encoding='utf-8') as f:
            for r in pred:f.write(json.dumps(r,sort_keys=True)+'\n')
    except Exception as e:S['terminal']='C072N18C_TECHNICAL_STOP';S['error']=f'{type(e).__name__}:{e}'
    SUMMARY_PATH.write_text(json.dumps(S,indent=2,sort_keys=True)+'\n',encoding='utf-8');print(json.dumps(S,indent=2,sort_keys=True))
    if S['terminal']=='C072N18C_TECHNICAL_STOP':raise SystemExit(1)
if __name__=='__main__':main()
