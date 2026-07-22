#!/usr/bin/env python3
"""V6.4.2 ordered draw-band challenger.

Replaces the unstable draw-vs-not-draw decision with an ordinal latent-strength band:
away < draw < home. The latent side strength is built from pooled V6 and formal 1X2 side
log-odds. A symmetric draw band is partially adapted by competition draw propensity and,
prequentially, by current-season draw observations. Only older-850 chronological train/tail
validation selects parameters; newer-850 is development evidence only.
"""
from __future__ import annotations

import json, math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
CACHE=ROOT/'manifests'/'v6_sampled_17domain_pooled_scored_cache_v625_r4.json'
OUT=ROOT/'manifests'/'v6_ordered_draw_band_v642_status.json'
EPS=1e-12
BLEND_GRID=(0.0,0.5,1.0)
SCALE_GRID=(0.75,1.0,1.25)
TAU_GRID=(0.35,0.50,0.65,0.80,0.95)
GAMMA_GRID=(0.0,0.25,0.50)
ONLINE_PRIOR_GRID=(20.0,80.0)
CLASSES=('home','draw','away')

def load(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def clip(x,lo=1e-6,hi=1-1e-6): return min(hi,max(lo,x))
def logit(p): p=clip(p); return math.log(p/(1-p))
def sig(x):
    if x>=0:
        z=math.exp(-min(700,x)); return 1/(1+z)
    z=math.exp(max(-700,x)); return z/(1+z)

def split_old(rows):
    by=defaultdict(list)
    for r in rows: by[str(r['competition_id'])].append(r)
    tr=[];va=[]
    for cid,it in sorted(by.items()):
        it=sorted(it,key=lambda x:(str(x['date']),str(x['identity'])))
        cut=max(1,int(round(.60*len(it))))
        tr+=it[:cut];va+=it[cut:]
    return tr,va

def rates(rows, prior=30.0):
    global_rate=sum(r['actual_result']=='draw' for r in rows)/len(rows)
    by=defaultdict(lambda:[0,0])
    for r in rows:
        x=by[str(r['competition_id'])];x[1]+=1;x[0]+=int(r['actual_result']=='draw')
    out={}
    for cid,(d,n) in by.items(): out[cid]=(d+prior*global_rate)/(n+prior)
    return global_rate,out

def probs(r, blend, scale, tau, gamma, global_rate, domain_rate, online_draws, online_n, online_prior):
    q,f=r['q'],r['formal']; cid=str(r['competition_id'])
    zq=math.log(max(EPS,float(q['home'])))-math.log(max(EPS,float(q['away'])))
    zf=math.log(max(EPS,float(f['home'])))-math.log(max(EPS,float(f['away'])))
    z=scale*(blend*zq+(1-blend)*zf)
    dr=domain_rate.get(cid,global_rate)
    if online_n>0:
        current=(online_draws+online_prior*dr)/(online_n+online_prior)
    else: current=dr
    adj=gamma*(logit(current)-logit(global_rate))
    t=max(.05,min(1.8,tau+adj))
    paway=sig(-t-z)
    phome=sig(z-t)
    pdraw=max(1e-6,1-phome-paway)
    s=phome+pdraw+paway
    return {'home':phome/s,'draw':pdraw/s,'away':paway/s},t

def score(rows, pars, global_rate, domain_rate, seed=None):
    hist=defaultdict(lambda:[0,0])
    if seed:
        for r in sorted(seed,key=lambda x:(str(x['competition_id']),str(x['date']),str(x['identity']))):
            h=hist[str(r['competition_id'])];h[1]+=1;h[0]+=int(r['actual_result']=='draw')
    n=h=0;b=rps=ll=0.;pred=Counter();act=Counter();conf={p:{t:0 for t in CLASSES} for p in CLASSES};by=defaultdict(Counter);taus=[]
    for r in sorted(rows,key=lambda x:(str(x['date']),str(x['competition_id']),str(x['identity']))):
        cid=str(r['competition_id']); dh,dn=hist[cid]
        q,tau_eff=probs(r,pars['blend'],pars['scale'],pars['tau'],pars['gamma'],global_rate,domain_rate,dh,dn,pars['online_prior']);taus.append(tau_eff)
        p=max(CLASSES,key=lambda k:q[k]); truth=str(r['actual_result']); hit=int(p==truth)
        n+=1;h+=hit;pred[p]+=1;act[truth]+=1;conf[p][truth]+=1
        b+=sum((q[k]-(1 if truth==k else 0))**2 for k in CLASSES)
        tv={'home':(1,0,0),'draw':(0,1,0),'away':(0,0,1)}[truth]
        c1=q['home']-tv[0];c2=q['home']+q['draw']-tv[0]-tv[1];rps+=(c1*c1+c2*c2)/2;ll-=math.log(max(EPS,q[truth]))
        by[cid]['count']+=1;by[cid]['hits']+=hit
        hist[cid][1]+=1;hist[cid][0]+=int(truth=='draw')
    draw_hits=conf['draw']['draw'];draw_pred=sum(conf['draw'].values());draw_actual=act['draw']
    return {'count':n,'hits':h,'accuracy':h/n,'mean_brier':b/n,'mean_rps':rps/n,'mean_log_loss':ll/n,'predicted_direction_counts':dict(pred),'actual_direction_counts':dict(act),'draw_precision':draw_hits/draw_pred if draw_pred else None,'draw_recall':draw_hits/draw_actual if draw_actual else None,'confusion':conf,'mean_effective_tau':sum(taus)/len(taus),'by_domain':{c:{'count':x['count'],'hits':x['hits'],'accuracy':x['hits']/x['count']} for c,x in sorted(by.items())}}

def baseline(rows):
    n=len(rows);hits=sum(bool(r['hit']) for r in rows);pred=Counter(r['pick'] for r in rows);act=Counter(r['actual_result'] for r in rows);conf={p:{t:0 for t in CLASSES} for p in CLASSES};b=rps=ll=0.
    for r in rows:
        q=r['q'];t=r['actual_result'];p=r['pick'];conf[p][t]+=1;b+=sum((q[k]-(1 if t==k else 0))**2 for k in CLASSES);tv={'home':(1,0,0),'draw':(0,1,0),'away':(0,0,1)}[t];c1=q['home']-tv[0];c2=q['home']+q['draw']-tv[0]-tv[1];rps+=(c1*c1+c2*c2)/2;ll-=math.log(max(EPS,q[t]))
    dh=conf['draw']['draw'];dp=sum(conf['draw'].values());da=act['draw']
    return {'count':n,'hits':hits,'accuracy':hits/n,'mean_brier':b/n,'mean_rps':rps/n,'mean_log_loss':ll/n,'predicted_direction_counts':dict(pred),'actual_direction_counts':dict(act),'draw_precision':dh/dp if dp else None,'draw_recall':dh/da if da else None,'confusion':conf}

def main():
    cache=load(CACHE);rows=cache['rows'];old=[r for r in rows if r['role']=='older'];new=[r for r in rows if r['role']=='newer'];train,valid=split_old(old);gr,dr=rates(train);bv=baseline(valid);bn=baseline(new)
    cand=[]
    for blend in BLEND_GRID:
      for scale in SCALE_GRID:
       for tau in TAU_GRID:
        for gamma in GAMMA_GRID:
         for op in ONLINE_PRIOR_GRID:
          pars={'blend':blend,'scale':scale,'tau':tau,'gamma':gamma,'online_prior':op};m=score(valid,pars,gr,dr,seed=train);proper=m['mean_brier']<=bv['mean_brier']+1e-12 and m['mean_rps']<=bv['mean_rps']+1e-12 and m['mean_log_loss']<=bv['mean_log_loss']+1e-12;cand.append({'parameters':pars,'proper_nonworse':proper,'validation':m})
    elig=[c for c in cand if c['proper_nonworse']] or cand;elig.sort(key=lambda c:(-c['validation']['accuracy'],-(c['validation']['draw_recall'] or 0),c['validation']['mean_log_loss']));sel=elig[0]
    gr2,dr2=rates(old);mn=score(new,sel['parameters'],gr2,dr2,seed=None);guard={'brier_nonworse':mn['mean_brier']<=bn['mean_brier']+1e-12,'rps_nonworse':mn['mean_rps']<=bn['mean_rps']+1e-12,'log_loss_nonworse':mn['mean_log_loss']<=bn['mean_log_loss']+1e-12}
    out={'schema_version':'V6.4.2-ordered-draw-band-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','design':{'older_train':len(train),'older_validation':len(valid),'older_refit':len(old),'newer_development':len(new),'newer_used_for_selection':False,'competition_draw_rate_partial_pool_prior_matches':30.0,'ordered_structure':'away < draw < home'},'baseline_validation':bv,'selected_candidate':sel,'baseline_newer':bn,'challenger_newer':mn,'newer_accuracy_gain_pp':100*(mn['accuracy']-bn['accuracy']),'newer_draw_recall_gain_pp':100*((mn['draw_recall'] or 0)-(bn['draw_recall'] or 0)),'newer_draw_precision_gain_pp':100*((mn['draw_precision'] or 0)-(bn['draw_precision'] or 0)),'newer_proper_score_guard':guard,'research_gate_passed':mn['accuracy']>bn['accuracy'] and all(guard.values()),'governance':{'development_only':True,'newer_850_not_pristine':True,'automatic_promotion':False,'fresh_forward_required':True,'current_rule_change':False,'formal_weight_change':False,'runtime_probability_change':False}}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2));return 0
if __name__=='__main__': raise SystemExit(main())
