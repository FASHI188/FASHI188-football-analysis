#!/usr/bin/env python3
from __future__ import annotations
import json, math, sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
R2=HERE.parent/'score_r2_bivariate_poisson'; sys.path.insert(0,str(R2))
import run_score_r2 as r2
r1=r2.r1; r25=r1.r25
OUT=HERE/'results'

def fit_scales(train):
    mh=sum(float(r['raw']['mu_home']) for r in train); ma=sum(float(r['raw']['mu_away']) for r in train)
    gh=sum(r['hg'] for r in train); ga=sum(r['ag'] for r in train)
    if mh<=0 or ma<=0: raise RuntimeError('invalid mean totals')
    return gh/mh,ga/ma

def mu(r,sh,sa): return float(r['raw']['mu_home'])*sh,float(r['raw']['mu_away'])*sa

def fit_rho_scaled(train,sh,sa):
    best=None
    for rho in r1.RHO_GRID:
        nll=0.0; ok=True
        for r in train:
            mh,ma=mu(r,sh,sa); t=r1.tau(r['hg'],r['ag'],mh,ma,rho)
            if t<=0 or not math.isfinite(t): ok=False; break
            nll-=math.log(t)
        if ok and (best is None or nll<best[0]-1e-12): best=(nll,rho)
    if best is None: raise RuntimeError('no valid scaled DC rho')
    return best[1],best[0]

def pscore(r,sh,sa,rho=None):
    mh,ma=mu(r,sh,sa); p=r1.pois(r['hg'],mh)*r1.pois(r['ag'],ma)
    if rho is not None: p*=r1.tau(r['hg'],r['ag'],mh,ma,rho)
    return max(p,1e-15)

def ranked(r,sh,sa,rho=None):
    mh,ma=mu(r,sh,sa); z=[]
    for h in range(r1.MAX_SCORE+1):
        ph=r1.pois(h,mh)
        for a in range(r1.MAX_SCORE+1):
            p=ph*r1.pois(a,ma)
            if rho is not None: p*=r1.tau(h,a,mh,ma,rho)
            if p<0: raise RuntimeError('negative probability')
            z.append((p,h,a))
    z.sort(key=lambda x:(-x[0],x[1]+x[2],x[1],x[2])); return z

def metrics(rows,sh,sa,rho=None):
    n=len(rows); h1=h3=h5=0; ll=0.0; low=lowh=0
    for r in rows:
        ll-=math.log(pscore(r,sh,sa,rho)); picks=[(x[1],x[2]) for x in ranked(r,sh,sa,rho)]; y=(r['hg'],r['ag'])
        h1+=y==picks[0]; h3+=y in picks[:3]; h5+=y in picks[:5]
        if r['hg']<=1 and r['ag']<=1: low+=1; lowh+=y==picks[0]
    return {'count':n,'exact_top1_hits':h1,'exact_top1_accuracy':h1/n,'top3_hits':h3,'top3_coverage':h3/n,'top5_hits':h5,'top5_coverage':h5/n,'score_logloss':ll/n,'low_score_00_01_10_11_count':low,'low_score_top1_hits':lowh}

def d(a,b): return r2.d(a,b)

def main():
    tr,va,te,lock=r2.splits(); sh,sa=fit_scales(tr); rho0,_=r1.fit_rho(tr); rhos,_=fit_rho_scaled(tr,sh,sa)
    vi=r1.metrics(va,None); vd=r1.metrics(va,rho0); vs=metrics(va,sh,sa,None); vsd=metrics(va,sh,sa,rhos)
    ti=r1.metrics(te,None); td=r1.metrics(te,rho0); ts=metrics(te,sh,sa,None); tsd=metrics(te,sh,sa,rhos)
    out={'schema_version':'football3-score-r3-mean-calibration','status':'COMPLETE','classification':'DEVELOPMENT_FIXED_TAIL_SCORE_DISTRIBUTION','formal_weight':0,
      'governance':{'S60_1x2_frozen_unchanged':True,'history_rows':60000,'score_train_rows':len(tr),'validation_rows':len(va),'test_rows':len(te),'same_date_results_and_xg_withheld':True,'strict_prior_xg':True,'mean_scales_estimated_on_training_only':True,'rho_estimated_on_training_only':True,'validation_used_for_parameters':False,'test_used_for_parameters':False,'odds_used':False,'market_prices_used':False,'manual_probability_adjustment':False},
      'S60_lock_reproduction':{'B20_validation_hits':lock[0],'B20_test_hits':lock[1],'S60_validation_hits':lock[2],'S60_test_hits':lock[3]},
      'parameters':{'home_mean_scale':sh,'away_mean_scale':sa,'unscaled_dc_rho':rho0,'scaled_dc_rho':rhos},
      'validation':{'independent_poisson':vi,'dixon_coles':vd,'scaled_poisson':vs,'scaled_dixon_coles':vsd,'delta_scaled_dc_minus_ip':d(vsd,vi),'delta_scaled_dc_minus_dc':d(vsd,vd)},
      'test':{'independent_poisson':ti,'dixon_coles':td,'scaled_poisson':ts,'scaled_dixon_coles':tsd,'delta_scaled_dc_minus_ip':d(tsd,ti),'delta_scaled_dc_minus_dc':d(tsd,td)}}
    OUT.mkdir(parents=True,exist_ok=True); (OUT/'summary_score_r3.json').write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n'); print(json.dumps(out,indent=2))

def verify():
    s=json.loads((OUT/'summary_score_r3.json').read_text()); g=s['governance']; q=s['S60_lock_reproduction']
    assert g['S60_1x2_frozen_unchanged'] and g['mean_scales_estimated_on_training_only'] and g['rho_estimated_on_training_only'] and not g['validation_used_for_parameters'] and not g['test_used_for_parameters']
    assert g['same_date_results_and_xg_withheld'] and not g['odds_used'] and not g['market_prices_used']
    assert (q['B20_validation_hits'],q['B20_test_hits'],q['S60_validation_hits'],q['S60_test_hits'])==(2064,1877,2071,1889)
    assert s['validation']['scaled_dixon_coles']['count']==4096 and s['test']['scaled_dixon_coles']['count']==3805
    print('SCORE_R3_VERIFY_PASS')
if __name__=='__main__':
    if len(sys.argv)!=2 or sys.argv[1] not in {'run','verify'}: raise SystemExit('usage: run_score_r3.py {run|verify}')
    {'run':main,'verify':verify}[sys.argv[1]]()
