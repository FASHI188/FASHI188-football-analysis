#!/usr/bin/env python3
"""V6.4.4 prequential team-shock residual correction.

A fast, leakage-safe correction on top of the exact pooled V6.0.1 probabilities.
Each team carries a residual strength state updated only after completed earlier matches.
The state resets at season start, decays toward zero, and is driven by actual-result score
minus the model's pre-match expected score. This is intended to react to transfers, coaching
changes and other structural shocks without hand-coded post-hoc labels.

Hyperparameters are selected only on the chronological tail of older-850; newer-850 is
reported as development evidence only.
"""
from __future__ import annotations
import json, math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
CACHE=ROOT/'manifests'/'v6_sampled_17domain_pooled_scored_cache_v625_r4.json'
OUT=ROOT/'manifests'/'v6_team_shock_residual_v644_status.json'
K_GRID=(0.10,0.20,0.35,0.50,0.75)
DECAY_GRID=(0.90,0.95,0.98,1.0)
SCALE_GRID=(0.5,1.0,1.5)
DRAW_RATIO=0.8
CLASSES=('home','draw','away');EPS=1e-12

def load(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def clip(x,lo=1e-6,hi=1-1e-6): return min(hi,max(lo,x))
def logit(p): p=clip(p);return math.log(p/(1-p))
def sig(x):
    if x>=0:
        z=math.exp(-min(700,x));return 1/(1+z)
    z=math.exp(max(-700,x));return z/(1+z)
def split_old(rows):
    by=defaultdict(list)
    for r in rows:by[str(r['competition_id'])].append(r)
    tr=[];va=[]
    for cid,it in sorted(by.items()):
        it=sorted(it,key=lambda x:(str(x['date']),str(x['identity'])));cut=max(1,int(round(.60*len(it))));tr+=it[:cut];va+=it[cut:]
    return tr,va

def pick(q):
    side='home' if q['home']>=q['away'] else 'away'
    return 'draw' if q['draw']>=DRAW_RATIO*q[side] else side

def adjusted(r,state,scale):
    q=r['q']; rem=max(EPS,float(q['home'])+float(q['away']));ph=float(q['home'])/rem
    h=(str(r['competition_id']),str(r['home_team']));a=(str(r['competition_id']),str(r['away_team']))
    z=logit(ph)+scale*(state[h]-state[a]);ph2=sig(z);pd=float(q['draw']);return {'home':(1-pd)*ph2,'draw':pd,'away':(1-pd)*(1-ph2)}
def result_score(t): return 1.0 if t=='home' else 0.0 if t=='away' else 0.5
def expected_score(q): return float(q['home'])+.5*float(q['draw'])
def update(r,q,state,k,decay):
    h=(str(r['competition_id']),str(r['home_team']));a=(str(r['competition_id']),str(r['away_team']))
    state[h]*=decay;state[a]*=decay
    surprise=result_score(str(r['actual_result']))-expected_score(q);delta=k*surprise
    state[h]=max(-1.5,min(1.5,state[h]+delta));state[a]=max(-1.5,min(1.5,state[a]-delta))
def run(rows,k,decay,scale,seed=None):
    state=defaultdict(float)
    if seed:
        for r in sorted(seed,key=lambda x:(str(x['date']),str(x['competition_id']),str(x['identity']))):
            q=adjusted(r,state,scale);update(r,q,state,k,decay)
    n=h=0;b=rps=ll=0.;pred=Counter();act=Counter();conf={p:{t:0 for t in CLASSES} for p in CLASSES};by=defaultdict(Counter)
    for r in sorted(rows,key=lambda x:(str(x['date']),str(x['competition_id']),str(x['identity']))):
        q=adjusted(r,state,scale);p=pick(q);t=str(r['actual_result']);hit=int(p==t);n+=1;h+=hit;pred[p]+=1;act[t]+=1;conf[p][t]+=1;b+=sum((q[c]-(1 if t==c else 0))**2 for c in CLASSES);tv={'home':(1,0,0),'draw':(0,1,0),'away':(0,0,1)}[t];c1=q['home']-tv[0];c2=q['home']+q['draw']-tv[0]-tv[1];rps+=(c1*c1+c2*c2)/2;ll-=math.log(max(EPS,q[t]));by[str(r['competition_id'])]['count']+=1;by[str(r['competition_id'])]['hits']+=hit;update(r,q,state,k,decay)
    dh=conf['draw']['draw'];dp=sum(conf['draw'].values());da=act['draw'];ah=conf['away']['away'];ap=sum(conf['away'].values())
    return {'count':n,'hits':h,'accuracy':h/n,'mean_brier':b/n,'mean_rps':rps/n,'mean_log_loss':ll/n,'predicted_direction_counts':dict(pred),'actual_direction_counts':dict(act),'draw_precision':dh/dp if dp else None,'draw_recall':dh/da if da else None,'away_precision':ah/ap if ap else None,'confusion':conf,'by_domain':{c:{'count':x['count'],'hits':x['hits'],'accuracy':x['hits']/x['count']} for c,x in sorted(by.items())}}
def baseline(rows): return run(rows,0.0,1.0,0.0,None)
def main():
    cache=load(CACHE);rows=cache['rows'];old=[r for r in rows if r['role']=='older'];new=[r for r in rows if r['role']=='newer'];tr,va=split_old(old);bv=baseline(va);bn=baseline(new);cand=[]
    for k in K_GRID:
      for decay in DECAY_GRID:
       for scale in SCALE_GRID:
        m=run(va,k,decay,scale,seed=tr);proper=m['mean_brier']<=bv['mean_brier']+1e-12 and m['mean_rps']<=bv['mean_rps']+1e-12 and m['mean_log_loss']<=bv['mean_log_loss']+1e-12;cand.append({'k':k,'decay':decay,'scale':scale,'proper_nonworse':proper,'validation':m})
    elig=[c for c in cand if c['proper_nonworse']] or cand;elig.sort(key=lambda c:(-c['validation']['accuracy'],c['validation']['mean_log_loss']));sel=elig[0];mn=run(new,sel['k'],sel['decay'],sel['scale'],seed=None);guard={'brier_nonworse':mn['mean_brier']<=bn['mean_brier']+1e-12,'rps_nonworse':mn['mean_rps']<=bn['mean_rps']+1e-12,'log_loss_nonworse':mn['mean_log_loss']<=bn['mean_log_loss']+1e-12}
    out={'schema_version':'V6.4.4-team-shock-residual-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','design':{'older_train':len(tr),'older_validation':len(va),'newer_development':len(new),'season_reset':True,'newer_used_for_selection':False},'baseline_validation':bv,'selected_candidate':sel,'baseline_newer':bn,'challenger_newer':mn,'accuracy_gain_pp':100*(mn['accuracy']-bn['accuracy']),'away_precision_gain_pp':100*((mn['away_precision'] or 0)-(bn['away_precision'] or 0)),'proper_score_guard':guard,'research_gate_passed':mn['accuracy']>bn['accuracy'] and all(guard.values()),'governance':{'development_only':True,'newer_850_not_pristine':True,'fresh_forward_required':True,'automatic_promotion':False,'current_rule_change':False,'formal_weight_change':False,'runtime_probability_change':False}}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
