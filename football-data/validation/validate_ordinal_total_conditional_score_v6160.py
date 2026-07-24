#!/usr/bin/env python3
"""V6.16.0 research-only ordinal total-goals -> conditional goal-difference challenger.

Total goals are modelled directly through seven cumulative binary targets:
P(T>0),...,P(T>6). Their predicted exceedance probabilities are forced monotone and
differenced into P(T=0),...,P(T=6),P(T=7+).

Features are strictly prematch/PIT within the historical CSV ordering: de-vigged 1X2,
O/U2.5, competition rolling total-goal mean, and lagged team goals-for/goals-against/
match-total summaries computed before the current match is added to history.

For exact score, a separate conditional model predicts D=home_goals-away_goals given
T=t and the same prematch features. Only t<=6 can map to a unique finite score set;
7+ remains unavailable for exact score.

Gate selection uses 2022/23-2024/25 chronological validation folds. The whole 2025/26
season is untouched during selection and is evaluated both in aggregate and in 100-match
blocks. Historical odds lack original quote timestamps, so formal_weight=0.
"""
from __future__ import annotations
import json, math, sys
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; V=ROOT/'validation'
if str(V) not in sys.path: sys.path.insert(0,str(V))
import validate_total_score_external_v6153 as source

from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

OUT=ROOT/'manifests'/'v6_ordinal_total_conditional_score_v6160_status.json'
VALS=('2022/23','2023/24','2024/25'); TEST='2025/26'
PGRID=(.20,.22,.24,.26,.28,.30,.32,.34,.36)
GGRID=(0,.01,.02,.03,.04,.05)
MINSEL=120; Z=1.6448536269514722
TEAM_WINDOW=10; LEAGUE_WINDOW=100


def sy(s): return int(str(s)[:4])
def avg(xs,default): return sum(xs)/len(xs) if xs else default
def wilson(h,n):
    if not n:return None
    p=h/n;z2=Z*Z;d=1+z2/n;c=p+z2/(2*n);q=Z*math.sqrt((p*(1-p)+z2/(4*n))/n);return (c-q)/d

def enrich(rows):
    by_comp=defaultdict(list)
    for r in rows: by_comp[r['competition_id']].append(r)
    out=[]
    for cid,group in by_comp.items():
        group=sorted(group,key=lambda r:(r['date'],r['home'],r['away']))
        league=deque(maxlen=LEAGUE_WINDOW); team=defaultdict(lambda:deque(maxlen=TEAM_WINDOW))
        for r in group:
            lg=avg(list(league),2.6)
            hh=list(team[r['home']]); ah=list(team[r['away']])
            def tf(hist,idx): return avg([x[idx] for x in hist],lg if idx==2 else lg/2)
            ph,pd,pa=[float(x) for x in r['one_x_two']]
            x=[ph,pd,pa,float(r['p_over']),lg,
               tf(hh,0),tf(hh,1),tf(hh,2),tf(ah,0),tf(ah,1),tf(ah,2)]
            hg,ag=r['score']; t=hg+ag
            item=dict(r);item['x']=x;item['total_exact']=t;item['total_bucket']=min(7,t);item['d']=hg-ag;out.append(item)
            league.append(t);team[r['home']].append((hg,ag,t));team[r['away']].append((ag,hg,t))
    return sorted(out,key=lambda r:(r['date'],r['competition_id'],r['home'],r['away']))

def fit_models(train):
    X=[r['x'] for r in train]
    total_models=[]
    for k in range(7):
        y=[1 if r['total_exact']>k else 0 for r in train]
        m=make_pipeline(StandardScaler(),LogisticRegression(C=1.0,max_iter=600,solver='lbfgs'))
        m.fit(X,y);total_models.append(m)
    dmodels={}
    for t in range(7):
        sub=[r for r in train if r['total_exact']==t]
        classes=sorted({r['d'] for r in sub})
        if len(classes)<=1:
            dmodels[t]=('CONST',classes[0] if classes else 0);continue
        m=make_pipeline(StandardScaler(),LogisticRegression(C=1.0,max_iter=600,solver='lbfgs'))
        m.fit([r['x'] for r in sub],[r['d'] for r in sub]);dmodels[t]=m
    return total_models,dmodels

def total_dist(x,models):
    q=[]
    for m in models:q.append(float(m.predict_proba([x])[0][1]))
    # Monotone exceedance projection: q0>=q1>=...>=q6.
    for i in range(1,7): q[i]=min(q[i-1],q[i])
    p=[max(0.0,1-q[0])]
    for i in range(1,7):p.append(max(0.0,q[i-1]-q[i]))
    p.append(max(0.0,q[6]));s=sum(p);return [v/s for v in p]
def pred_total(r,models):
    p=total_dist(r['x'],models);rank=sorted(enumerate(p),key=lambda z:(-z[1],z[0]));return rank[0][0],rank[0][1],rank[0][1]-rank[1][1],p

def score_rank(r,t,dmodels):
    if t>=7:return []
    m=dmodels[t]
    if isinstance(m,tuple):ds=[m[1]]
    else:
        probs=m.predict_proba([r['x']])[0];classes=list(m.named_steps['logisticregression'].classes_)
        ds=[d for _,d in sorted(zip(probs,classes),reverse=True)]
    scores=[]
    for d in ds:
        if (t+d)%2:continue
        h=(t+d)//2;a=(t-d)//2
        if h>=0 and a>=0:scores.append((int(h),int(a)))
    return scores

def predict_rows(rows,tm,dm):
    out=[]
    for r in rows:
        t,p,g,dist=pred_total(r,tm);sr=score_rank(r,t,dm);actual=tuple(r['score'])
        out.append({'r':r,'t':t,'p':p,'g':g,'total_hit':t==r['total_bucket'],
                    'score_eligible':t<7,'score_top1':bool(sr and actual in sr[:1]),'score_top3':bool(sr and actual in sr[:3]),
                    'cond_top1':bool(t==r['total_exact'] and sr and actual in sr[:1]),
                    'cond_top3':bool(t==r['total_exact'] and sr and actual in sr[:3])})
    return out

def filt(pred,pmin,gmin):return [z for z in pred if z['p']>=pmin and z['g']>=gmin]
def summary(sel,alln):
    n=len(sel);h=sum(z['total_hit'] for z in sel);score=[z for z in sel if z['score_eligible']];sn=len(score)
    cond=[z for z in sel if z['t']==z['r']['total_exact'] and z['t']<7];cn=len(cond)
    return {'all_matches':alln,'selected':n,'coverage':n/alln if alln else 0,'total_hits':h,'total_accuracy':h/n if n else None,'wilson90_lower':wilson(h,n),
            'score_eligible':sn,'score_top1_hits':sum(z['score_top1'] for z in score),'score_top1_accuracy':sum(z['score_top1'] for z in score)/sn if sn else None,
            'score_top3_hits':sum(z['score_top3'] for z in score),'score_top3_accuracy':sum(z['score_top3'] for z in score)/sn if sn else None,
            'conditional_total_exact_count':cn,'conditional_score_top1':sum(z['cond_top1'] for z in cond)/cn if cn else None,'conditional_score_top3':sum(z['cond_top3'] for z in cond)/cn if cn else None}

def main():
    raw,_=source.load_all();rows=enrich(raw);cache={};cands=[]
    for vs in VALS:
        tr=[r for r in rows if sy(r['season'])<sy(vs)];va=[r for r in rows if r['season']==vs]
        tm,dm=fit_models(tr);cache[vs]=predict_rows(va,tm,dm)
    for p in PGRID:
      for g in GGRID:
        stats=[summary(filt(cache[v],p,g),len(cache[v])) for v in VALS]
        if all(s['selected']>=MINSEL and s['total_accuracy'] is not None for s in stats):
            n=sum(s['selected'] for s in stats);h=sum(s['total_hits'] for s in stats);worst=min(s['total_accuracy'] for s in stats)
            cands.append((worst,wilson(h,n),h/n,n,p,g,stats))
    cands.sort(reverse=True,key=lambda z:(z[0],z[1],z[2],z[3]));best=cands[0];worst,lower,acc,n,p,g,vstats=best
    train=[r for r in rows if sy(r['season'])<2025];test=[r for r in rows if r['season']==TEST]
    tm,dm=fit_models(train);pred=predict_rows(test,tm,dm);sel=filt(pred,p,g);testsum=summary(sel,len(test))
    blocks=[]
    for i in range(0,len(test)-99,100):
        chunk=pred[i:i+100];s=summary(filt(chunk,p,g),100);s.update({'start':i,'stop':i+100,'first_date':test[i]['date'],'last_date':test[i+99]['date']});blocks.append(s)
    bacc=[b['total_accuracy'] for b in blocks if b['total_accuracy'] is not None]
    block_summary={'block_count':len(blocks),'worst_total_accuracy':min(bacc) if bacc else None,'mean_total_accuracy':sum(bacc)/len(bacc) if bacc else None,
                   'blocks_ge_25pct':sum(x>=.25 for x in bacc),'blocks_ge_30pct':sum(x>=.30 for x in bacc),'blocks_lt_20pct':sum(x<.20 for x in bacc)}
    out={'schema_version':'V6.16.0-ordinal-total-conditional-score-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_RESEARCH_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP',
         'design':{'validation_seasons':list(VALS),'test_season':TEST,'team_window':TEAM_WINDOW,'league_window':LEAGUE_WINDOW,'direct_cumulative_targets':['T>0','T>1','T>2','T>3','T>4','T>5','T>6'],'monotone_projection':True,'conditional_goal_difference_given_total':True,'test_used_for_selection':False},
         'selected_gate':{'pmin':p,'gapmin':g,'validation_worst_accuracy':worst,'validation_aggregate_accuracy':acc,'validation_wilson90_lower':lower,'validation_count':n,'validation_folds':vstats},
         'test_2025_26':testsum,'test_100_blocks':blocks,'test_100_block_summary':block_summary,
         'governance':{'research_only':True,'formal_weight':0,'current_rule_change':False,'automatic_promotion':False,'historical_market_quotes_lack_original_timestamp':True}}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'gate':out['selected_gate'],'test':testsum,'blocks':block_summary},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
