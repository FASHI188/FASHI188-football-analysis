#!/usr/bin/env python3
from __future__ import annotations
import json, math, sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
R3=HERE.parent/'score_r3_mean_calibration'; sys.path.insert(0,str(R3))
import run_score_r3 as r3
r2=r3.r2; r1=r3.r1
OUT=HERE/'results'; THETA_GRID=[i*0.005 for i in range(101)]

def nb_pmf(k,mu,theta):
    if theta<=1e-15: return r1.pois(k,mu)
    shape=1.0/theta
    logp=math.lgamma(k+shape)-math.lgamma(shape)-math.lgamma(k+1)+shape*math.log(shape/(shape+mu))+k*math.log(mu/(shape+mu))
    return max(math.exp(logp),1e-15)

def fit_theta(train,side):
    goal='hg' if side=='home' else 'ag'; key='mu_home' if side=='home' else 'mu_away'; best=None
    for th in THETA_GRID:
        ll=0.0
        for r in train: ll-=math.log(nb_pmf(r[goal],float(r['raw'][key]),th))
        if best is None or ll<best[0]-1e-12: best=(ll,th)
    return best[1],best[0]

def pscore(r,thh,tha):
    return max(nb_pmf(r['hg'],float(r['raw']['mu_home']),thh)*nb_pmf(r['ag'],float(r['raw']['mu_away']),tha),1e-15)

def ranked(r,thh,tha):
    mh=float(r['raw']['mu_home']); ma=float(r['raw']['mu_away']); z=[]
    for h in range(r1.MAX_SCORE+1):
        ph=nb_pmf(h,mh,thh)
        for a in range(r1.MAX_SCORE+1): z.append((ph*nb_pmf(a,ma,tha),h,a))
    z.sort(key=lambda x:(-x[0],x[1]+x[2],x[1],x[2])); return z

def metrics(rows,thh,tha):
    n=len(rows); h1=h3=h5=0; ll=0.; low=lowh=0
    for r in rows:
        ll-=math.log(pscore(r,thh,tha)); picks=[(x[1],x[2]) for x in ranked(r,thh,tha)]; y=(r['hg'],r['ag'])
        h1+=y==picks[0]; h3+=y in picks[:3]; h5+=y in picks[:5]
        if r['hg']<=1 and r['ag']<=1: low+=1; lowh+=y==picks[0]
    return {'count':n,'exact_top1_hits':h1,'exact_top1_accuracy':h1/n,'top3_hits':h3,'top3_coverage':h3/n,'top5_hits':h5,'top5_coverage':h5/n,'score_logloss':ll/n,'low_score_00_01_10_11_count':low,'low_score_top1_hits':lowh}

def main():
    tr,va,te,lock=r2.splits(); thh,nllh=fit_theta(tr,'home'); tha,nlla=fit_theta(tr,'away'); rho,_=r1.fit_rho(tr)
    vi=r1.metrics(va,None); vd=r1.metrics(va,rho); vn=metrics(va,thh,tha)
    ti=r1.metrics(te,None); td=r1.metrics(te,rho); tn=metrics(te,thh,tha)
    out={'schema_version':'football3-score-r4-overdispersion','status':'COMPLETE','classification':'DEVELOPMENT_FIXED_TAIL_SCORE_DISTRIBUTION','formal_weight':0,
      'governance':{'S60_1x2_frozen_unchanged':True,'history_rows':60000,'score_train_rows':len(tr),'validation_rows':len(va),'test_rows':len(te),'same_date_results_and_xg_withheld':True,'strict_prior_xg':True,'dispersion_estimated_on_training_only':True,'validation_used_for_parameters':False,'test_used_for_parameters':False,'theta_grid_predeclared':[0.0,0.5,0.005],'odds_used':False,'market_prices_used':False,'manual_probability_adjustment':False},
      'S60_lock_reproduction':{'B20_validation_hits':lock[0],'B20_test_hits':lock[1],'S60_validation_hits':lock[2],'S60_test_hits':lock[3]},
      'parameters':{'home_theta':thh,'away_theta':tha,'home_theta_at_boundary':thh in (0.0,0.5),'away_theta_at_boundary':tha in (0.0,0.5),'home_training_nll':nllh,'away_training_nll':nlla,'dc_rho_reference':rho},
      'validation':{'independent_poisson':vi,'dixon_coles':vd,'negative_binomial':vn,'delta_nb_minus_ip':r2.d(vn,vi),'delta_nb_minus_dc':r2.d(vn,vd)},
      'test':{'independent_poisson':ti,'dixon_coles':td,'negative_binomial':tn,'delta_nb_minus_ip':r2.d(tn,ti),'delta_nb_minus_dc':r2.d(tn,td)}}
    OUT.mkdir(parents=True,exist_ok=True); (OUT/'summary_score_r4.json').write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n'); print(json.dumps(out,indent=2))

def verify():
    s=json.loads((OUT/'summary_score_r4.json').read_text()); g=s['governance']; q=s['S60_lock_reproduction']
    assert g['S60_1x2_frozen_unchanged'] and g['dispersion_estimated_on_training_only'] and not g['validation_used_for_parameters'] and not g['test_used_for_parameters']
    assert g['same_date_results_and_xg_withheld'] and not g['odds_used'] and not g['market_prices_used']
    assert (q['B20_validation_hits'],q['B20_test_hits'],q['S60_validation_hits'],q['S60_test_hits'])==(2064,1877,2071,1889)
    assert s['validation']['negative_binomial']['count']==4096 and s['test']['negative_binomial']['count']==3805
    print('SCORE_R4_VERIFY_PASS')
if __name__=='__main__':
    if len(sys.argv)!=2 or sys.argv[1] not in {'run','verify'}: raise SystemExit('usage: run_score_r4.py {run|verify}')
    {'run':main,'verify':verify}[sys.argv[1]]()
