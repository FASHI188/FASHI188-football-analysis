#!/usr/bin/env python3
from __future__ import annotations
import json, math, sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
R1=HERE.parent/'score_r1_dixon_coles'; sys.path.insert(0,str(R1))
import run_score_r1 as r1
r25=r1.r25; r9=r1.r9
OUT=HERE/'results'; ALPHA_GRID=[i*0.005 for i in range(101)]


def bv_prob(h,a,mh,ma,alpha):
    c=alpha*min(mh,ma); l1=max(mh-c,1e-12); l2=max(ma-c,1e-12); l3=max(c,0.0)
    s=0.0
    for k in range(min(h,a)+1):
        s+=(l1**(h-k))/math.factorial(h-k)*(l2**(a-k))/math.factorial(a-k)*(l3**k)/math.factorial(k)
    return max(math.exp(-(l1+l2+l3))*s,1e-15)

def fit_alpha(train):
    best=None
    for a in ALPHA_GRID:
        ll=0.0
        for r in train:
            ll-=math.log(bv_prob(r['hg'],r['ag'],float(r['raw']['mu_home']),float(r['raw']['mu_away']),a))
        if best is None or ll<best[0]-1e-12: best=(ll,a)
    return best[1],best[0]

def ranked(r,alpha):
    mh=float(r['raw']['mu_home']); ma=float(r['raw']['mu_away']); z=[]
    for h in range(r1.MAX_SCORE+1):
        for a in range(r1.MAX_SCORE+1): z.append((bv_prob(h,a,mh,ma,alpha),h,a))
    z.sort(key=lambda x:(-x[0],x[1]+x[2],x[1],x[2])); return z

def metrics(rows,alpha):
    n=len(rows); h1=h3=h5=0; ll=0.0; low=lowh=0
    for r in rows:
        p=bv_prob(r['hg'],r['ag'],float(r['raw']['mu_home']),float(r['raw']['mu_away']),alpha); ll-=math.log(p)
        rr=ranked(r,alpha); picks=[(x[1],x[2]) for x in rr]; y=(r['hg'],r['ag'])
        h1+=y==picks[0]; h3+=y in picks[:3]; h5+=y in picks[:5]
        if r['hg']<=1 and r['ag']<=1: low+=1; lowh+=y==picks[0]
    return {'count':n,'exact_top1_hits':h1,'exact_top1_accuracy':h1/n,'top3_hits':h3,'top3_coverage':h3/n,'top5_hits':h5,'top5_coverage':h5/n,'score_logloss':ll/n,'low_score_00_01_10_11_count':low,'low_score_top1_hits':lowh}

def d(a,b):
    return {'exact_top1_hits':a['exact_top1_hits']-b['exact_top1_hits'],'exact_top1_pp':100*(a['exact_top1_accuracy']-b['exact_top1_accuracy']),'top3_hits':a['top3_hits']-b['top3_hits'],'top3_pp':100*(a['top3_coverage']-b['top3_coverage']),'top5_hits':a['top5_hits']-b['top5_hits'],'top5_pp':100*(a['top5_coverage']-b['top5_coverage']),'score_logloss':a['score_logloss']-b['score_logloss']}

def splits():
    _,_,_,_,_,lock=r25.lock_models()
    if lock!=(2064,1877,2071,1889): raise RuntimeError(f'S60 lock mismatch {lock}')
    base=r9.load(); ex=r25.load_extra_preserve(r25.EXTRA)[-r1.EXTRA_N:]
    bp=r1.score_history(base); b1=r9.boundary(bp,r9.TARGET_BURN); b2=r9.boundary(bp,b1+r9.TARGET_TRAIN); b3=r9.boundary(bp,b2+r9.TARGET_VAL); n=b2-b1
    hp=r1.score_history(ex+base); off=r1.EXTRA_N
    tr=hp[off+b2-r1.TRAIN_MULT*n:off+b2]; va=hp[off+b2:off+b3]; te=hp[off+b3:]
    if [x['game_id'] for x in va]!=[x['game_id'] for x in bp[b2:b3]] or [x['game_id'] for x in te]!=[x['game_id'] for x in bp[b3:]]: raise RuntimeError('fixed-tail identity mismatch')
    return tr,va,te,lock

def main():
    tr,va,te,lock=splits(); alpha,train_nll=fit_alpha(tr); rho,_=r1.fit_rho(tr)
    vi=r1.metrics(va,None); vd=r1.metrics(va,rho); vb=metrics(va,alpha)
    ti=r1.metrics(te,None); td=r1.metrics(te,rho); tb=metrics(te,alpha)
    out={'schema_version':'football3-score-r2-bivariate-poisson','status':'COMPLETE','classification':'DEVELOPMENT_FIXED_TAIL_SCORE_DISTRIBUTION','formal_weight':0,
      'governance':{'S60_1x2_frozen_unchanged':True,'history_rows':60000,'score_train_rows':len(tr),'validation_rows':len(va),'test_rows':len(te),'same_date_results_and_xg_withheld':True,'strict_prior_xg':True,'alpha_estimated_on_training_only':True,'validation_used_for_alpha':False,'test_used_for_alpha':False,'alpha_grid_predeclared':[0.0,0.5,0.005],'odds_used':False,'market_prices_used':False,'manual_probability_adjustment':False},
      'S60_lock_reproduction':{'B20_validation_hits':lock[0],'B20_test_hits':lock[1],'S60_validation_hits':lock[2],'S60_test_hits':lock[3]},
      'parameters':{'dixon_coles_rho_reference':rho,'bivariate_shared_fraction_alpha':alpha,'alpha_at_grid_boundary':alpha in (0.0,0.5),'training_bivariate_score_nll':train_nll},
      'validation':{'independent_poisson':vi,'dixon_coles':vd,'bivariate_poisson':vb,'delta_bv_minus_ip':d(vb,vi),'delta_bv_minus_dc':d(vb,vd)},
      'test':{'independent_poisson':ti,'dixon_coles':td,'bivariate_poisson':tb,'delta_bv_minus_ip':d(tb,ti),'delta_bv_minus_dc':d(tb,td)}}
    OUT.mkdir(parents=True,exist_ok=True); (OUT/'summary_score_r2.json').write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n',encoding='utf-8'); print(json.dumps(out,indent=2))

def verify():
    s=json.loads((OUT/'summary_score_r2.json').read_text()); g=s['governance']; q=s['S60_lock_reproduction']
    assert g['S60_1x2_frozen_unchanged'] and g['alpha_estimated_on_training_only'] and not g['validation_used_for_alpha'] and not g['test_used_for_alpha']
    assert g['same_date_results_and_xg_withheld'] and not g['odds_used'] and not g['market_prices_used']
    assert (q['B20_validation_hits'],q['B20_test_hits'],q['S60_validation_hits'],q['S60_test_hits'])==(2064,1877,2071,1889)
    assert s['validation']['bivariate_poisson']['count']==4096 and s['test']['bivariate_poisson']['count']==3805
    print('SCORE_R2_VERIFY_PASS')
if __name__=='__main__':
    if len(sys.argv)!=2 or sys.argv[1] not in {'run','verify'}: raise SystemExit('usage: run_score_r2.py {run|verify}')
    {'run':main,'verify':verify}[sys.argv[1]]()
