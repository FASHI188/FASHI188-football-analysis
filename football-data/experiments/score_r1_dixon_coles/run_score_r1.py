#!/usr/bin/env python3
from __future__ import annotations
import json, math, sys
from collections import defaultdict
from pathlib import Path

HERE=Path(__file__).resolve().parent
R25=HERE.parent/'top1_r25_fresh_s60_confirmation'
sys.path.insert(0,str(R25))
import run_experiment_r25 as r25
r24=r25.r24; r23=r25.r23; r9=r25.r9
OUT=HERE/'results'
EXTRA_N=40000; TRAIN_MULT=3; MAX_SCORE=12
RHO_GRID=[-0.20+i*0.0025 for i in range(161)]


def score_history(rows):
    st=r9.S(); pred=[]; by=defaultdict(list)
    for x in rows: by[x['date']].append(x)
    for d in sorted(by):
        pending=[]
        for x in sorted(by[d],key=lambda z:z['game_id']):
            raw=st.pred(x)
            pred.append({'date':d,'game_id':x['game_id'],'hg':int(x['home_goals']),'ag':int(x['away_goals']),'raw':raw})
            pending.append((x,raw))
        for x,raw in pending: st.update(x,raw)
    return pred


def pois(k,mu): return math.exp(-mu)*(mu**k)/math.factorial(k)

def tau(h,a,lh,la,rho):
    if h==0 and a==0: return 1-lh*la*rho
    if h==0 and a==1: return 1+lh*rho
    if h==1 and a==0: return 1+la*rho
    if h==1 and a==1: return 1-rho
    return 1.0

def prob_score(r,rho=None):
    h,a=r['hg'],r['ag']; lh=float(r['raw']['mu_home']); la=float(r['raw']['mu_away'])
    p=pois(h,lh)*pois(a,la)
    if rho is not None: p*=tau(h,a,lh,la,rho)
    return max(p,1e-15)

def fit_rho(train):
    best=None
    for rho in RHO_GRID:
        nll=0.0; ok=True
        for r in train:
            t=tau(r['hg'],r['ag'],float(r['raw']['mu_home']),float(r['raw']['mu_away']),rho)
            if t<=0 or not math.isfinite(t): ok=False; break
            nll-=math.log(t)
        if ok and (best is None or nll<best[0]-1e-12): best=(nll,rho)
    if best is None: raise RuntimeError('no valid Dixon-Coles rho on predeclared grid')
    return best[1],best[0]

def ranked(r,rho=None):
    lh=float(r['raw']['mu_home']); la=float(r['raw']['mu_away']); z=[]
    for h in range(MAX_SCORE+1):
        ph=pois(h,lh)
        for a in range(MAX_SCORE+1):
            p=ph*pois(a,la)
            if rho is not None: p*=tau(h,a,lh,la,rho)
            if p<0: raise RuntimeError('negative score probability')
            z.append((p,h,a))
    z.sort(key=lambda x:(-x[0],x[1]+x[2],x[1],x[2]))
    return z

def metrics(rows,rho=None):
    n=len(rows); top1=top3=top5=0; ll=0.0; low=0; lowhit=0
    for r in rows:
        ll-=math.log(prob_score(r,rho)); rank=ranked(r,rho); actual=(r['hg'],r['ag'])
        picks=[(x[1],x[2]) for x in rank]
        top1+=actual==picks[0]; top3+=actual in picks[:3]; top5+=actual in picks[:5]
        if r['hg']<=1 and r['ag']<=1:
            low+=1; lowhit+=actual==picks[0]
    return {'count':n,'exact_top1_hits':top1,'exact_top1_accuracy':top1/n,'top3_hits':top3,'top3_coverage':top3/n,'top5_hits':top5,'top5_coverage':top5/n,'score_logloss':ll/n,'low_score_00_01_10_11_count':low,'low_score_top1_hits':lowhit}

def delta(dc,ip):
    return {'exact_top1_hits':dc['exact_top1_hits']-ip['exact_top1_hits'],'exact_top1_pp':100*(dc['exact_top1_accuracy']-ip['exact_top1_accuracy']),'top3_hits':dc['top3_hits']-ip['top3_hits'],'top3_pp':100*(dc['top3_coverage']-ip['top3_coverage']),'top5_hits':dc['top5_hits']-ip['top5_hits'],'top5_pp':100*(dc['top5_coverage']-ip['top5_coverage']),'score_logloss':dc['score_logloss']-ip['score_logloss']}

def main():
    # Reproduce locked S60 stage-primary evidence first; score work must not alter 1X2.
    _,_,_,_,_,lock=r25.lock_models()
    if lock!=(2064,1877,2071,1889): raise RuntimeError(f'S60 lock mismatch {lock}')
    base=r9.load(); ex60=r25.load_extra_preserve(r25.EXTRA)
    if len(base)!=20000 or len(ex60)!=60000: raise RuntimeError('history size mismatch')
    extra=ex60[-EXTRA_N:]
    if max(x['date'] for x in extra)>=min(x['date'] for x in base): raise RuntimeError('history chronology violation')
    bp=score_history(base); b1=r9.boundary(bp,r9.TARGET_BURN); b2=r9.boundary(bp,b1+r9.TARGET_TRAIN); b3=r9.boundary(bp,b2+r9.TARGET_VAL); ntrain=b2-b1
    hp=score_history(extra+base); off=EXTRA_N
    train=hp[off+b2-TRAIN_MULT*ntrain:off+b2]; val=hp[off+b2:off+b3]; test=hp[off+b3:]
    if len(train)!=TRAIN_MULT*ntrain or len(val)!=4096 or len(test)!=3805: raise RuntimeError(f'split mismatch {len(train)}/{len(val)}/{len(test)}')
    if [x['game_id'] for x in val]!=[x['game_id'] for x in bp[b2:b3]] or [x['game_id'] for x in test]!=[x['game_id'] for x in bp[b3:]]: raise RuntimeError('fixed-tail identity mismatch')
    rho,train_corr_nll=fit_rho(train)
    vi=metrics(val,None); vd=metrics(val,rho); ti=metrics(test,None); td=metrics(test,rho)
    out={'schema_version':'football3-score-r1-dixon-coles','status':'COMPLETE','classification':'DEVELOPMENT_FIXED_TAIL_SCORE_DISTRIBUTION','formal_weight':0,
      'governance':{'S60_1x2_frozen_unchanged':True,'history_rows':60000,'score_train_rows':len(train),'validation_rows':len(val),'test_rows':len(test),'same_date_results_and_xg_withheld':True,'strict_prior_xg':True,'rho_estimated_on_training_only':True,'validation_used_for_rho':False,'test_used_for_rho':False,'rho_grid_predeclared':[-0.20,0.20,0.0025],'time_decay_for_rho':False,'odds_used':False,'market_prices_used':False,'manual_probability_adjustment':False},
      'S60_lock_reproduction':{'B20_validation_hits':lock[0],'B20_test_hits':lock[1],'S60_validation_hits':lock[2],'S60_test_hits':lock[3]},
      'dixon_coles':{'rho':rho,'rho_at_grid_boundary':abs(abs(rho)-0.20)<1e-12,'training_correction_nll':train_corr_nll},
      'validation':{'independent_poisson':vi,'dixon_coles':vd,'delta_dc_minus_ip':delta(vd,vi)},
      'test':{'independent_poisson':ti,'dixon_coles':td,'delta_dc_minus_ip':delta(td,ti)}}
    OUT.mkdir(parents=True,exist_ok=True); (OUT/'summary_score_r1.json').write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n',encoding='utf-8'); print(json.dumps(out,indent=2))

def verify():
    s=json.loads((OUT/'summary_score_r1.json').read_text(encoding='utf-8')); g=s['governance']; q=s['S60_lock_reproduction']
    assert g['S60_1x2_frozen_unchanged'] and g['rho_estimated_on_training_only'] and not g['validation_used_for_rho'] and not g['test_used_for_rho']
    assert g['same_date_results_and_xg_withheld'] and g['strict_prior_xg'] and not g['odds_used'] and not g['market_prices_used']
    assert (q['B20_validation_hits'],q['B20_test_hits'],q['S60_validation_hits'],q['S60_test_hits'])==(2064,1877,2071,1889)
    assert s['validation']['independent_poisson']['count']==4096 and s['test']['independent_poisson']['count']==3805
    print('SCORE_R1_VERIFY_PASS')

if __name__=='__main__':
    if len(sys.argv)!=2 or sys.argv[1] not in {'run','verify'}: raise SystemExit('usage: run_score_r1.py {run|verify}')
    {'run':main,'verify':verify}[sys.argv[1]]()
