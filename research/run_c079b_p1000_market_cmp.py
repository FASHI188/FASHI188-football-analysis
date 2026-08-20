#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, math
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse
import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from scipy.optimize import brentq, minimize
from scipy.special import gammaln, logsumexp
from scipy.stats import poisson

from audit_c079p1000_public_multiline import stream_post_marker

EXPECTED_SHA='ce2af86f206077255ea489242a3e8473e34b89f140cc9528f2ad9594593c3413'
SLUGS=sorted([
 'australia-a-league','copa-libertadores','croatia-hnl','argentina-liga-profesional','colombia-primera-a',
 'belgium-jupiler-pro-league','austria-bundesliga','brazil-serie-a','denmark-superliga','europe-champions-league',
 'england-premier-league','germany-bundesliga','europe-conference-league','europe-europa-league','netherlands-eredivisie',
 'italy-serie-a','france-ligue-1','greece-super-league','saudi-professional-league','scotland-premiership',
 'portugal-liga-portugal','turkey-super-lig','spain-laliga','world-cup','usa-mls',
])
URLS={s:f'https://footiqo.com/database/leagues/{s}/' for s in SLUGS}
PRICE=['O25','U25','O35','U35','O45','U45']
UA='Mozilla/5.0 C079-B P1000 frozen-development audit'
KMAX=120
KS=np.arange(KMAX+1,dtype=float)
LOGFACT=gammaln(KS+1.0)
BOUNDS=[(math.log(0.1),math.log(8.0)),(math.log(0.6),math.log(3.0))]


def devig(o,u):
    io=1.0/o; iu=1.0/u; return io/(io+iu)
def logit(x):
    x=np.clip(x,1e-8,1-1e-8); return np.log(x/(1-x))
def ids_sha(keys):
    return hashlib.sha256(('\n'.join(sorted(map(str,keys)))+'\n').encode()).hexdigest()


def rebuild_frozen_market(workers:int):
    rows=[]; reports={}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        fut={ex.submit(stream_post_marker,u):s for s,u in URLS.items()}
        for f in as_completed(fut):
            s=fut[f]
            try:
                rr,nt,status,err=f.result(); rows.extend(rr); reports[s]={'status':status,'tables':nt,'rows':len(rr),'error':err}
            except Exception as e:
                reports[s]={'status':None,'tables':0,'rows':0,'error':repr(e)}
    d=pd.DataFrame(rows)
    if d.empty: return d,reports,None
    d=d.drop_duplicates('identity_key',keep='first').copy()
    num=pd.DataFrame({c:pd.to_numeric(d[c],errors='coerce') for c in PRICE},index=d.index)
    valid=num.notna().all(axis=1)&(num>1.0).all(axis=1)
    d=d.loc[valid].copy()
    d['selection_hash']=d.identity_key.astype(str).map(lambda x:hashlib.sha256(x.encode()).hexdigest())
    d=d.sort_values(['selection_hash','identity_key']).head(1000).reset_index(drop=True)
    return d,reports,ids_sha(d.identity_key.tolist()) if len(d)==1000 else None


def fetch_selected_results(slug:str, selected_by_id:dict):
    url=URLS[slug]
    r=requests.get(url,timeout=60,headers={'User-Agent':UA}); r.raise_for_status()
    soup=BeautifulSoup(r.content,'lxml'); found=[]; tables=0
    for t in soup.find_all('table'):
        trs=t.find_all('tr')
        if not trs: continue
        hdr=[x.get_text(' ',strip=True) for x in trs[0].find_all(['th','td'])]
        needed=['id','matchDate','homeTeam','awayTeam','FTHG','FTAG']
        if not all(c in hdr for c in needed): continue
        tables+=1; pos={h:i for i,h in enumerate(hdr)}
        for tr in trs[1:]:
            cells=tr.find_all(['td','th'])
            if len(cells)<len(hdr): continue
            rid=cells[pos['id']].get_text(' ',strip=True)
            if rid not in selected_by_id: continue
            exp=selected_by_id[rid]
            date=cells[pos['matchDate']].get_text(' ',strip=True)
            home=cells[pos['homeTeam']].get_text(' ',strip=True)
            away=cells[pos['awayTeam']].get_text(' ',strip=True)
            hg_txt=cells[pos['FTHG']].get_text(' ',strip=True)
            ag_txt=cells[pos['FTAG']].get_text(' ',strip=True)
            if not hg_txt or not ag_txt: continue
            try: hg=int(float(hg_txt)); ag=int(float(ag_txt))
            except Exception: continue
            identity_ok=(date==exp['matchDate'] and home==exp['homeTeam'] and away==exp['awayTeam'])
            found.append({'identity_key':exp['identity_key'],'id':rid,'FTHG':hg,'FTAG':ag,'identity_ok':identity_ok})
    return found,tables,int(r.status_code)


def open_exact_labels(market:pd.DataFrame,workers:int):
    by_slug={}
    for _,r in market.iterrows():
        by_slug.setdefault(r.domain,{})[str(r.id)]={'identity_key':r.identity_key,'matchDate':str(r.matchDate),'homeTeam':str(r.homeTeam),'awayTeam':str(r.awayTeam)}
    allr=[]; reports={}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        fut={ex.submit(fetch_selected_results,s,ids):s for s,ids in by_slug.items()}
        for f in as_completed(fut):
            s=fut[f]
            try:
                rr,nt,status=f.result(); allr.extend(rr); reports[s]={'http_status':status,'result_tables_seen':nt,'selected_rows_seen':len(rr)}
            except Exception as e:
                reports[s]={'http_status':None,'result_tables_seen':0,'selected_rows_seen':0,'error':repr(e)}
    rd=pd.DataFrame(allr)
    if rd.empty: return rd,reports,0,0
    conflict=0
    for _,g in rd.groupby('identity_key'):
        if len(g[['FTHG','FTAG']].drop_duplicates())>1: conflict+=1
    rd=rd.sort_values('identity_key').drop_duplicates('identity_key',keep='first')
    return rd,reports,int(conflict),int((~rd.identity_ok).sum())


def solve_mu(q3:float):
    return float(brentq(lambda m:poisson.sf(2,m)-q3,1e-5,20.0,xtol=1e-12,rtol=1e-12,maxiter=100))


def cmp_pmf(loglam:float,lognu:float):
    lam=math.exp(loglam); nu=math.exp(lognu)
    lw=KS*loglam-nu*LOGFACT
    lz=logsumexp(lw); p=np.exp(lw-lz)
    ratio=lam/((KMAX+1.0)**nu)
    if ratio>=1: residual_upper=1.0
    else: residual_upper=float(p[-1]*ratio/(1.0-ratio))
    return p,residual_upper


def fit_cmp(qs,mu0):
    target=logit(np.asarray(qs,float))
    def obj(x):
        p,_=cmp_pmf(float(x[0]),float(x[1]))
        mt=np.array([p[3:].sum(),p[4:].sum(),p[5:].sum()])
        d=logit(mt)-target
        return float(np.dot(d,d))
    x0=np.array([math.log(min(8.0,max(0.1,mu0))),0.0])
    res=minimize(obj,x0,method='L-BFGS-B',bounds=BOUNDS,options={'maxiter':250,'ftol':1e-13,'gtol':1e-9})
    p,tail=cmp_pmf(float(res.x[0]),float(res.x[1]))
    hit=any(abs(res.x[i]-BOUNDS[i][0])<1e-5 or abs(res.x[i]-BOUNDS[i][1])<1e-5 for i in range(2))
    mt=np.array([p[3:].sum(),p[4:].sum(),p[5:].sum()])
    rmse=float(np.sqrt(np.mean((logit(mt)-target)**2)))
    return p,{'success':bool(res.success),'lambda':math.exp(float(res.x[0])),'nu':math.exp(float(res.x[1])),'objective':float(res.fun),'market_logit_rmse':rmse,'bound_hit':bool(hit),'residual_tail_upper':tail,'nit':int(getattr(res,'nit',-1))}


def collapsed8_from_arr(p):
    first=np.asarray(p[:7],float); return np.r_[first,max(0.0,1.0-first.sum())]
def onehot8(t):
    y=np.zeros(8); y[min(int(t),7)]=1.; return y
def brier(P,Y): return float(np.mean(np.sum((P-Y)**2,axis=1)))
def rps(P,Y): return float(np.mean(np.sum((np.cumsum(P,axis=1)[:,:-1]-np.cumsum(Y,axis=1)[:,:-1])**2,axis=1)/7.0))
def llcat(P,Yidx): return float(np.mean(-np.log(np.clip(P[np.arange(len(P)),Yidx],1e-15,1))))


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--out-dir',required=True); ap.add_argument('--workers',type=int,default=8); a=ap.parse_args()
    out=Path(a.out_dir); out.mkdir(parents=True,exist_ok=True)
    market,source_reports,sha=rebuild_frozen_market(a.workers)
    pre={'schema_version':'C079B_P1000_MARKET_CMP_V1','expected_identity_sha256':EXPECTED_SHA,'rebuilt_identity_sha256':sha,'market_rows':int(len(market)),'source_reports':source_reports,'formal_weight':0,'formal_gate_3000_unchanged':True,'hard_boundaries':{'C078D_late2119_opened':False,'C076D_opened':False,'C071_reserve52180_opened':False,'C070F1597_opened':False,'A05_or_protected_opened':False,'CURRENT_change':False,'unified_matrix_generated':False}}
    if len(market)!=1000 or sha!=EXPECTED_SHA:
        pre.update({'status':'STOP_IDENTITY_REPRODUCTION','labels_opened':0,'model_scored':False})
        (out/'summary.json').write_text(json.dumps(pre,ensure_ascii=False,indent=2)+'\n'); print(json.dumps(pre,ensure_ascii=False,indent=2)); return 0
    results,result_reports,conflicts,identity_mismatch=open_exact_labels(market,a.workers)
    coverage=int(len(results))
    if coverage!=1000 or conflicts or identity_mismatch:
        pre.update({'status':'STOP_COVERAGE','labels_opened':coverage,'result_reports':result_reports,'result_conflicts':conflicts,'identity_mismatch':identity_mismatch,'model_scored':False})
        (out/'summary.json').write_text(json.dumps(pre,ensure_ascii=False,indent=2)+'\n'); print(json.dumps(pre,ensure_ascii=False,indent=2)); return 0
    d=market.merge(results[['identity_key','FTHG','FTAG']],on='identity_key',how='inner',validate='one_to_one')
    d['T']=d.FTHG.astype(int)+d.FTAG.astype(int)
    if int(d['T'].max())>KMAX:
        pre.update({'status':'STOP_SUPPORT','labels_opened':1000,'max_total':int(d['T'].max()),'model_scored':False})
        (out/'summary.json').write_text(json.dumps(pre,ensure_ascii=False,indent=2)+'\n'); print(json.dumps(pre,ensure_ascii=False,indent=2)); return 0
    n=len(d); P0=np.zeros((n,8)); PC=np.zeros((n,8)); exact0=np.zeros(n); exactc=np.zeros(n); ev7_0=np.zeros(n); ev7_c=np.zeros(n); ev8_0=np.zeros(n); ev8_c=np.zeros(n); fitdiag=[]
    for i,r in d.iterrows():
        q3=devig(float(r.O25),float(r.U25)); q4=devig(float(r.O35),float(r.U35)); q5=devig(float(r.O45),float(r.U45))
        mu=solve_mu(q3)
        b=poisson.pmf(np.arange(KMAX+1),mu)
        c,fd=fit_cmp([q3,q4,q5],mu); fitdiag.append(fd)
        t=int(r['T'])
        exact0[i]=max(float(poisson.pmf(t,mu)),1e-300); exactc[i]=max(float(c[t]),1e-300)
        P0[i]=collapsed8_from_arr(b); PC[i]=collapsed8_from_arr(c)
        ev7_0[i]=float(poisson.sf(6,mu)); ev7_c[i]=float(1-c[:7].sum())
        ev8_0[i]=float(poisson.sf(7,mu)); ev8_c[i]=float(1-c[:8].sum())
    T=d['T'].to_numpy(int); yidx=np.minimum(T,7); Y=np.vstack([onehot8(t) for t in T])
    exact_ll0=float(np.mean(-np.log(exact0))); exact_llc=float(np.mean(-np.log(exactc))); dll=exact_llc-exact_ll0
    lossdiff=(-np.log(exactc))-(-np.log(exact0))
    rng=np.random.default_rng(79001); boots=np.empty(3000)
    for j in range(3000): boots[j]=float(np.mean(lossdiff[rng.integers(0,n,n)]))
    ci=[float(np.quantile(boots,.05)),float(np.quantile(boots,.95))]
    b0=brier(P0,Y); bc=brier(PC,Y); r0=rps(P0,Y); rc=rps(PC,Y); cll0=llcat(P0,yidx); cllc=llcat(PC,yidx)
    y7=(T>=7).astype(float); y8=(T>=8).astype(float)
    eb7_0=float(np.mean((ev7_0-y7)**2)); eb7_c=float(np.mean((ev7_c-y7)**2)); eb8_0=float(np.mean((ev8_0-y8)**2)); eb8_c=float(np.mean((ev8_c-y8)**2))
    tailmask=T>=7; tailn=int(tailmask.sum()); tail_ll0=None; tail_llc=None; tail_dll=None
    if tailn>=15:
        vals0=[]; valsc=[]
        for i in np.where(tailmask)[0]:
            t=int(T[i]); den0=ev7_0[i]; denc=ev7_c[i]
            vals0.append(-math.log(max(exact0[i]/den0,1e-300))); valsc.append(-math.log(max(exactc[i]/denc,1e-300)))
        tail_ll0=float(np.mean(vals0)); tail_llc=float(np.mean(valsc)); tail_dll=tail_llc-tail_ll0
    per_domain={}; wins=0
    for dom,g in d.assign(loss0=-np.log(exact0),lossc=-np.log(exactc)).groupby('domain'):
        l0=float(g.loss0.mean()); lc=float(g.lossc.mean()); win=lc<l0; wins+=int(win); per_domain[str(dom)]={'n':int(len(g)),'baseline_exact_ll':l0,'candidate_exact_ll':lc,'dLL':lc-l0,'win':bool(win)}
    success_rate=float(np.mean([x['success'] for x in fitdiag])); bound_hits=int(sum(x['bound_hit'] for x in fitdiag)); max_resid=float(max(x['residual_tail_upper'] for x in fitdiag)); prob_err=float(max(np.max(np.abs(P0.sum(axis=1)-1)),np.max(np.abs(PC.sum(axis=1)-1))))
    gates={
      'exact_dLogLoss_lt_0':dll<0,
      'bootstrap90_upper_lt_0':ci[1]<0,
      'collapsed_brier_nonworse':bc<=b0+1e-12,
      'collapsed_rps_nonworse':rc<=r0+1e-12,
      'event7_brier_nonworse':eb7_c<=eb7_0+1e-12,
      'event8_brier_nonworse':eb8_c<=eb8_0+1e-12,
      'domain_exact_ll_wins_ge_13':wins>=13,
      'optimizer_success_ge_0_99':success_rate>=0.99,
      'residual_tail_max_le_1e_8':max_resid<=1e-8,
      'probability_conservation_le_1e_10':prob_err<=1e-10,
    }
    status='PILOT_SIGNAL' if all(gates.values()) else 'PILOT_NO_SIGNAL'
    summary={**pre,'status':status,'labels_opened':1000,'result_reports':result_reports,'result_conflicts':0,'identity_mismatch':0,'model_scored':True,'metrics':{'exact_T':{'baseline_logloss':exact_ll0,'candidate_logloss':exact_llc,'dLogLoss':dll,'bootstrap3000_seed':79001,'bootstrap90_C_minus_B0':ci,'bootstrap_p_dll_lt_0':float(np.mean(boots<0))},'collapsed_0_6_7plus':{'baseline_logloss':cll0,'candidate_logloss':cllc,'dLogLoss':cllc-cll0,'baseline_brier':b0,'candidate_brier':bc,'dBrier':bc-b0,'baseline_rps':r0,'candidate_rps':rc,'dRPS':rc-r0},'tail_events':{'actual_7plus_rate':float(y7.mean()),'baseline_mean_p7plus':float(ev7_0.mean()),'candidate_mean_p7plus':float(ev7_c.mean()),'baseline_brier_7plus':eb7_0,'candidate_brier_7plus':eb7_c,'dBrier_7plus':eb7_c-eb7_0,'actual_8plus_rate':float(y8.mean()),'baseline_mean_p8plus':float(ev8_0.mean()),'candidate_mean_p8plus':float(ev8_c.mean()),'baseline_brier_8plus':eb8_0,'candidate_brier_8plus':eb8_c,'dBrier_8plus':eb8_c-eb8_0},'conditional_exact_tail_Tge7':{'n':tailn,'baseline_logloss':tail_ll0,'candidate_logloss':tail_llc,'dLogLoss':tail_dll},'domain_wins':{'wins':wins,'total_domains':len(per_domain),'details':per_domain}},'fit_audit':{'optimizer_success_rate':success_rate,'bound_hits':bound_hits,'max_residual_tail_upper':max_resid,'max_probability_sum_error':prob_err,'mean_market_logit_rmse':float(np.mean([x['market_logit_rmse'] for x in fitdiag])),'median_nu':float(np.median([x['nu'] for x in fitdiag])),'mean_nu':float(np.mean([x['nu'] for x in fitdiag]))},'gates':gates,'governance':{'development_only':True,'no_postview_family_repair_on_1000':True,'formal_3000_gate_unchanged':True,'exact_tail_formal_status':'BLOCKED','unified_exact_score_matrix':'UNAVAILABLE'}}
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    d[['identity_key','domain','id','matchDate','Country','League','Season','homeTeam','awayTeam','O25','U25','O35','U35','O45','U45','T']].to_csv(out/'consumed_development1000_receipt.csv',index=False)
    print(json.dumps(summary,ensure_ascii=False,indent=2)); return 0

if __name__=='__main__': raise SystemExit(main())
