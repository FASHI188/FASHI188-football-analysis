#!/usr/bin/env python3
"""V6.16.1 research-only nonlinear cumulative total -> conditional score challenger.

Same PIT feature contract and chronological folds as V6.16.0. The only substantive
change is replacing linear logistic learners with fixed HistGradientBoostingClassifier
models to capture nonlinear interactions. Hyperparameters are fixed ex ante; only the
execution confidence gate is selected on 2022/23-2024/25 and tested on the whole 2025/26.
"""
from __future__ import annotations
import json,math,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];V=ROOT/'validation'
if str(V) not in sys.path:sys.path.insert(0,str(V))
import validate_ordinal_total_conditional_score_v6160 as base
from sklearn.ensemble import HistGradientBoostingClassifier

OUT=ROOT/'manifests'/'v6_hgb_total_conditional_score_v6161_status.json'
VALS=base.VALS;TEST=base.TEST;PGRID=base.PGRID;GGRID=base.GGRID;MINSEL=base.MINSEL;Z=base.Z

def wilson(h,n):return base.wilson(h,n)
def model():return HistGradientBoostingClassifier(max_iter=180,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=30,l2_regularization=1.0,random_state=20260724)

def fit_models(train):
    X=[r['x'] for r in train];tm=[]
    for k in range(7):
        y=[1 if r['total_exact']>k else 0 for r in train];m=model();m.fit(X,y);tm.append(m)
    dm={}
    for t in range(7):
        sub=[r for r in train if r['total_exact']==t];classes=sorted({r['d'] for r in sub})
        if len(classes)<=1:dm[t]=('CONST',classes[0] if classes else 0)
        else:
            m=model();m.fit([r['x'] for r in sub],[r['d'] for r in sub]);dm[t]=m
    return tm,dm

def total_dist(x,models):
    q=[float(m.predict_proba([x])[0][1]) for m in models]
    for i in range(1,7):q[i]=min(q[i-1],q[i])
    p=[max(0,1-q[0])]+[max(0,q[i-1]-q[i]) for i in range(1,7)]+[max(0,q[6])];s=sum(p);return [v/s for v in p]
def pred_total(r,models):
    p=total_dist(r['x'],models);z=sorted(enumerate(p),key=lambda a:(-a[1],a[0]));return z[0][0],z[0][1],z[0][1]-z[1][1]
def score_rank(r,t,dm):
    if t>=7:return []
    m=dm[t]
    if isinstance(m,tuple):ds=[m[1]]
    else:
        probs=m.predict_proba([r['x']])[0];ds=[d for _,d in sorted(zip(probs,m.classes_),reverse=True)]
    out=[]
    for d in ds:
        if (t+int(d))%2:continue
        h=(t+int(d))//2;a=(t-int(d))//2
        if h>=0 and a>=0:out.append((h,a))
    return out

def predict(rows,tm,dm):
    out=[]
    for r in rows:
        t,p,g=pred_total(r,tm);sr=score_rank(r,t,dm);actual=tuple(r['score']);th=t==r['total_bucket']
        out.append({'r':r,'t':t,'p':p,'g':g,'total_hit':th,'score_eligible':t<7,'score_top1':bool(sr and actual in sr[:1]),'score_top3':bool(sr and actual in sr[:3]),'cond_top1':bool(t==r['total_exact'] and sr and actual in sr[:1]),'cond_top3':bool(t==r['total_exact'] and sr and actual in sr[:3])})
    return out
def filt(rows,p,g):return [z for z in rows if z['p']>=p and z['g']>=g]
def summary(sel,alln):
    n=len(sel);h=sum(z['total_hit'] for z in sel);sc=[z for z in sel if z['score_eligible']];sn=len(sc);cond=[z for z in sel if z['t']==z['r']['total_exact'] and z['t']<7];cn=len(cond)
    return {'all_matches':alln,'selected':n,'coverage':n/alln if alln else 0,'total_hits':h,'total_accuracy':h/n if n else None,'wilson90_lower':wilson(h,n),
            'score_eligible':sn,'score_top1_hits':sum(z['score_top1'] for z in sc),'score_top1_accuracy':sum(z['score_top1'] for z in sc)/sn if sn else None,
            'score_top3_hits':sum(z['score_top3'] for z in sc),'score_top3_accuracy':sum(z['score_top3'] for z in sc)/sn if sn else None,
            'conditional_total_exact_count':cn,'conditional_score_top1':sum(z['cond_top1'] for z in cond)/cn if cn else None,'conditional_score_top3':sum(z['cond_top3'] for z in cond)/cn if cn else None}

def main():
    raw,_=base.source.load_all();rows=base.enrich(raw);cache={};cands=[]
    for vs in VALS:
        tr=[r for r in rows if base.sy(r['season'])<base.sy(vs)];va=[r for r in rows if r['season']==vs];tm,dm=fit_models(tr);cache[vs]=predict(va,tm,dm)
    for p in PGRID:
      for g in GGRID:
        stats=[summary(filt(cache[v],p,g),len(cache[v])) for v in VALS]
        if all(s['selected']>=MINSEL and s['total_accuracy'] is not None for s in stats):
            n=sum(s['selected'] for s in stats);h=sum(s['total_hits'] for s in stats);cands.append((min(s['total_accuracy'] for s in stats),wilson(h,n),h/n,n,p,g,stats))
    cands.sort(reverse=True,key=lambda z:(z[0],z[1],z[2],z[3]));best=cands[0];worst,lower,acc,n,p,g,vstats=best
    tr=[r for r in rows if base.sy(r['season'])<2025];te=[r for r in rows if r['season']==TEST];tm,dm=fit_models(tr);pred=predict(te,tm,dm);sel=filt(pred,p,g);ts=summary(sel,len(te))
    blocks=[]
    for i in range(0,len(te)-99,100):
        s=summary(filt(pred[i:i+100],p,g),100);s.update({'start':i,'stop':i+100,'first_date':te[i]['date'],'last_date':te[i+99]['date']});blocks.append(s)
    ba=[b['total_accuracy'] for b in blocks if b['total_accuracy'] is not None]
    bs={'block_count':len(blocks),'worst_total_accuracy':min(ba) if ba else None,'mean_total_accuracy':sum(ba)/len(ba) if ba else None,'blocks_ge_25pct':sum(x>=.25 for x in ba),'blocks_ge_30pct':sum(x>=.30 for x in ba),'blocks_lt_20pct':sum(x<.20 for x in ba)}
    out={'schema_version':'V6.16.1-hgb-total-conditional-score-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_RESEARCH_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP','design':{'same_features_as_v6160':True,'learner':'HistGradientBoostingClassifier','fixed_hyperparameters':{'max_iter':180,'learning_rate':.05,'max_leaf_nodes':15,'min_samples_leaf':30,'l2_regularization':1.0},'validation_seasons':list(VALS),'test_season':TEST,'test_used_for_selection':False},'selected_gate':{'pmin':p,'gapmin':g,'validation_worst_accuracy':worst,'validation_aggregate_accuracy':acc,'validation_wilson90_lower':lower,'validation_count':n,'validation_folds':vstats},'test_2025_26':ts,'test_100_blocks':blocks,'test_100_block_summary':bs,'governance':{'research_only':True,'formal_weight':0,'current_rule_change':False,'automatic_promotion':False}}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'gate':out['selected_gate'],'test':ts,'blocks':bs},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
