#!/usr/bin/env python3
from __future__ import annotations
import json, math, sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
R2=HERE.parent/'score_r2_bivariate_poisson'; sys.path.insert(0,str(R2))
import run_score_r2 as r2
r1=r2.r1; r25=r1.r25; r23=r25.r23
OUT=HERE/'results'; SUM_MAX=20

def outcome(h,a): return 0 if h>a else 1 if h==a else 2

def base_cell(h,a,mh,ma,rho=None):
    p=r1.pois(h,mh)*r1.pois(a,ma)
    if rho is not None: p*=r1.tau(h,a,mh,ma,rho)
    return max(p,0.0)

def class_sums(mh,ma,rho=None):
    s=[0.0,0.0,0.0]
    for h in range(SUM_MAX+1):
        ph=r1.pois(h,mh)
        for a in range(SUM_MAX+1):
            p=ph*r1.pois(a,ma)
            if rho is not None: p*=r1.tau(h,a,mh,ma,rho)
            s[outcome(h,a)]+=p
    if min(s)<=0: raise RuntimeError('invalid class sum')
    return s

def anchored_prob(h,a,mh,ma,s60,sums,rho=None):
    cls=outcome(h,a); target=(s60['p_home'],s60['p_draw'],s60['p_away'])[cls]
    return max(base_cell(h,a,mh,ma,rho)*target/sums[cls],1e-15)

def ranked(r,s60,rho=None):
    mh=float(r['raw']['mu_home']); ma=float(r['raw']['mu_away']); sums=class_sums(mh,ma,rho); z=[]
    for h in range(r1.MAX_SCORE+1):
        for a in range(r1.MAX_SCORE+1): z.append((anchored_prob(h,a,mh,ma,s60,sums,rho),h,a))
    z.sort(key=lambda x:(-x[0],x[1]+x[2],x[1],x[2])); return z,sums

def metrics(rows,sm,rho=None):
    n=len(rows); h1=h3=h5=0; ll=0.; low=lowh=0; changes=0; maxerr=0.0
    for r in rows:
        s60=r23.pred(sm,r['raw']); mh=float(r['raw']['mu_home']); ma=float(r['raw']['mu_away']); sums=class_sums(mh,ma,rho)
        p=anchored_prob(r['hg'],r['ag'],mh,ma,s60,sums,rho); ll-=math.log(p)
        rr,_=ranked(r,s60,rho); picks=[(x[1],x[2]) for x in rr]; y=(r['hg'],r['ag'])
        h1+=y==picks[0]; h3+=y in picks[:3]; h5+=y in picks[:5]
        if r['hg']<=1 and r['ag']<=1: low+=1; lowh+=y==picks[0]
        # audit the anchored H/D/A marginals on the 0..20 summation grid
        rec=[0.0,0.0,0.0]
        for h in range(SUM_MAX+1):
            for a in range(SUM_MAX+1): rec[outcome(h,a)]+=anchored_prob(h,a,mh,ma,s60,sums,rho)
        tgt=[s60['p_home'],s60['p_draw'],s60['p_away']]; maxerr=max(maxerr,max(abs(rec[i]-tgt[i]) for i in range(3)))
        base_rank=r1.ranked(r,rho); changes+=(picks[0]!=(base_rank[0][1],base_rank[0][2]))
    return {'count':n,'exact_top1_hits':h1,'exact_top1_accuracy':h1/n,'top3_hits':h3,'top3_coverage':h3/n,'top5_hits':h5,'top5_coverage':h5/n,'score_logloss':ll/n,'low_score_00_01_10_11_count':low,'low_score_top1_hits':lowh,'top1_score_changed_vs_unanchored':changes,'max_abs_marginal_reconstruction_error':maxerr}

def main():
    tr,va,te,lock=r2.splits(); _,_,_,_,sm,lock2=r25.lock_models()
    if lock2!=lock: raise RuntimeError('S60 lock mismatch')
    rho,_=r1.fit_rho(tr)
    vi=r1.metrics(va,None); vd=r1.metrics(va,rho); vai=metrics(va,sm,None); vad=metrics(va,sm,rho)
    ti=r1.metrics(te,None); td=r1.metrics(te,rho); tai=metrics(te,sm,None); tad=metrics(te,sm,rho)
    out={'schema_version':'football3-score-r5-s60-marginal-anchor','status':'COMPLETE','classification':'DEVELOPMENT_FIXED_TAIL_SCORE_DISTRIBUTION','formal_weight':0,
      'governance':{'S60_1x2_frozen_unchanged':True,'score_matrix_forced_to_match_locked_S60_1x2_marginals':True,'history_rows':60000,'score_train_rows':len(tr),'validation_rows':len(va),'test_rows':len(te),'same_date_results_and_xg_withheld':True,'strict_prior_xg':True,'rho_estimated_on_training_only':True,'no_new_parameter_fit_on_validation_or_test':True,'odds_used':False,'market_prices_used':False,'manual_probability_adjustment':False},
      'S60_lock_reproduction':{'B20_validation_hits':lock[0],'B20_test_hits':lock[1],'S60_validation_hits':lock[2],'S60_test_hits':lock[3]},'parameters':{'dc_rho':rho,'class_sum_grid_max_goal':SUM_MAX},
      'validation':{'independent_poisson':vi,'dixon_coles':vd,'S60_anchored_poisson':vai,'S60_anchored_dixon_coles':vad,'delta_anchor_dc_minus_dc':r2.d(vad,vd),'delta_anchor_dc_minus_ip':r2.d(vad,vi)},
      'test':{'independent_poisson':ti,'dixon_coles':td,'S60_anchored_poisson':tai,'S60_anchored_dixon_coles':tad,'delta_anchor_dc_minus_dc':r2.d(tad,td),'delta_anchor_dc_minus_ip':r2.d(tad,ti)}}
    OUT.mkdir(parents=True,exist_ok=True); (OUT/'summary_score_r5.json').write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n'); print(json.dumps(out,indent=2))

def verify():
    s=json.loads((OUT/'summary_score_r5.json').read_text()); g=s['governance']; q=s['S60_lock_reproduction']
    assert g['S60_1x2_frozen_unchanged'] and g['score_matrix_forced_to_match_locked_S60_1x2_marginals'] and g['rho_estimated_on_training_only'] and g['no_new_parameter_fit_on_validation_or_test']
    assert g['same_date_results_and_xg_withheld'] and not g['odds_used'] and not g['market_prices_used']
    assert (q['B20_validation_hits'],q['B20_test_hits'],q['S60_validation_hits'],q['S60_test_hits'])==(2064,1877,2071,1889)
    assert s['validation']['S60_anchored_dixon_coles']['max_abs_marginal_reconstruction_error']<1e-8 and s['test']['S60_anchored_dixon_coles']['max_abs_marginal_reconstruction_error']<1e-8
    print('SCORE_R5_VERIFY_PASS')
if __name__=='__main__':
    if len(sys.argv)!=2 or sys.argv[1] not in {'run','verify'}: raise SystemExit('usage: run_score_r5.py {run|verify}')
    {'run':main,'verify':verify}[sys.argv[1]]()
