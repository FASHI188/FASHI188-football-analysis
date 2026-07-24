#!/usr/bin/env python3
"""V6.15.3 frozen-rule external validation for exact total -> conditional score.

The V6.15.1 rule is frozen ex ante: k=75, O/U distance weight=2.0,
exact-total confidence >=0.32, top1-top2 gap >=0.03. No parameter is selected
on any panel evaluated here.

Panels:
1) fresh non-overlapping 100-match blocks from the 2025/26 five-league sample,
   excluding the final 100 already reported by V6.15.1;
2) any non-development competition with >=300 earlier complete-market rows and
   >=100 2025/26 complete-market rows.

Historical odds lack original quote timestamps, so this remains research-only.
"""
from __future__ import annotations
import csv, heapq, json, math, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ENGINE=ROOT/'engine'; VALIDATION=ROOT/'validation'
for p in (ENGINE,VALIDATION):
    if str(p) not in sys.path: sys.path.insert(0,str(p))
from platform_core import canonical_team_name, load_aliases, parse_match_date

OUT=ROOT/'manifests'/'v6_total_score_external_v6153_status.json'
DEV_COMPS={'ENG_PremierLeague','GER_Bundesliga','ITA_SerieA','FRA_Ligue1','ESP_LaLiga'}
TRAIN_SEASONS={'2021/22','2022/23','2023/24','2024/25'}
TEST='2025/26'
K=75; W=2.0; PMIN=.32; GAPMIN=.03


def f(v):
    try:x=float(str(v).strip())
    except:return None
    return x if x>1 and math.isfinite(x) else None

def devig(vals):
    q=[1/x for x in vals];s=sum(q);return tuple(x/s for x in q)

def load_all():
    aliases=load_aliases();out=[];provider_counts=Counter()
    for d in sorted((ROOT/'processed').iterdir()):
        if not d.is_dir():continue
        cid=d.name
        for path in sorted(d.glob('*.csv')):
            with path.open('r',encoding='utf-8-sig',newline='') as fh:
                rd=csv.DictReader(fh);fields=set(rd.fieldnames or [])
                ou_choices=[]
                for cols,label in [(("P>2.5","P<2.5"),'Pinnacle'),(("B365>2.5","B365<2.5"),'Bet365'),(("Avg>2.5","Avg<2.5"),'Average')]:
                    if all(c in fields for c in cols):ou_choices.append((cols,label))
                for r0 in rd:
                    r={str(k):'' if v is None else str(v) for k,v in r0.items() if k}
                    season=str(r.get('season') or r.get('Season') or '').strip()
                    if season not in TRAIN_SEASONS|{TEST}:continue
                    if not r.get('HomeTeam') or not r.get('AwayTeam') or not r.get('Date'):continue
                    try:hg=int(float(r.get('FTHG','')));ag=int(float(r.get('FTAG','')))
                    except:continue
                    one=None;one_label=None
                    for cols,label in [(("PSH","PSD","PSA"),'Pinnacle'),(("B365H","B365D","B365A"),'Bet365'),(("AvgH","AvgD","AvgA"),'Average')]:
                        vals=[f(r.get(c)) for c in cols]
                        if all(v is not None for v in vals):one=devig(vals);one_label=label;break
                    if one is None:continue
                    two=None;ou_label=None
                    for cols,label in ou_choices:
                        vals=[f(r.get(c)) for c in cols]
                        if all(v is not None for v in vals):two=devig(vals);ou_label=label;break
                    if two is None:continue
                    try:dt=parse_match_date(r['Date'],season)
                    except:continue
                    out.append({'competition_id':cid,'season':season,'date':dt.isoformat(),
                                'home':canonical_team_name(cid,r['HomeTeam'],aliases),
                                'away':canonical_team_name(cid,r['AwayTeam'],aliases),
                                'score':(hg,ag),'one_x_two':one,'p_over':two[0]})
                    provider_counts[f'{one_label}+{ou_label}']+=1
    return sorted(out,key=lambda r:(r['date'],r['competition_id'],r['home'],r['away'])),dict(provider_counts)

def total(r):return min(7,sum(r['score']))
def vec(r):h,d,a=r['one_x_two'];return (float(h),float(d),float(a),float(r['p_over']))
def dist(a,b):
    x=vec(a);y=vec(b);return (x[0]-y[0])**2+(x[1]-y[1])**2+(x[2]-y[2])**2+W*(x[3]-y[3])**2

def nearest(r,train,k=K,total_value=None):
    pool=[q for q in train if total_value is None or total(q)==total_value]
    return heapq.nsmallest(min(k,len(pool)),pool,key=lambda q:dist(r,q))
def predict_total(r,train):
    nn=nearest(r,train);c=Counter(total(q) for q in nn);den=len(nn)+4
    p=[(c[i]+.5)/den for i in range(8)];rank=sorted(enumerate(p),key=lambda x:(-x[1],x[0]))
    return rank[0][0],rank[0][1],rank[0][1]-rank[1][1]
def score_ranks(r,pred_total,train):
    nn=nearest(r,train,max(40,K//2),pred_total);c=Counter(tuple(q['score']) for q in nn)
    return [s for s,_ in sorted(c.items(),key=lambda x:(-x[1],sum(x[0]),x[0]))]
def accepted(r,train):
    t,p,g=predict_total(r,train);return (t,p,g) if p>=PMIN and g>=GAPMIN else None

def evaluate(rows,train):
    n=h=s1=s3=cond=cond1=cond3=0
    for r in rows:
        pred=accepted(r,train)
        if pred is None:continue
        t,_,_=pred;n+=1;th=(t==total(r));h+=th
        ranks=score_ranks(r,t,train);actual=tuple(r['score']);a1=actual in ranks[:1];a3=actual in ranks[:3]
        s1+=a1;s3+=a3
        if th:cond+=1;cond1+=a1;cond3+=a3
    return {'all_matches':len(rows),'selected':n,'coverage':n/len(rows) if rows else 0,
            'total_hits':h,'total_accuracy':h/n if n else None,
            'score_top1_hits':s1,'score_top1_accuracy':s1/n if n else None,
            'score_top3_hits':s3,'score_top3_accuracy':s3/n if n else None,
            'conditional_total_correct_count':cond,
            'conditional_score_top1':cond1/cond if cond else None,
            'conditional_score_top3':cond3/cond if cond else None}

def main():
    rows,providers=load_all()
    dev_train=[r for r in rows if r['competition_id'] in DEV_COMPS and r['season'] in TRAIN_SEASONS]
    dev_test=[r for r in rows if r['competition_id'] in DEV_COMPS and r['season']==TEST]
    # Exclude the final 100 already observed in V6.15.1. Everything before it was not used for tuning.
    fresh=dev_test[:-100] if len(dev_test)>=200 else []
    blocks=[]
    for i in range(0,len(fresh)-99,100):
        block=fresh[i:i+100];e=evaluate(block,dev_train);e.update({'start':i,'stop':i+100,'first_date':block[0]['date'],'last_date':block[-1]['date']});blocks.append(e)
    ext={}
    comps=sorted({r['competition_id'] for r in rows}-DEV_COMPS)
    excluded={}
    for cid in comps:
        tr=[r for r in rows if r['competition_id']==cid and r['season'] in TRAIN_SEASONS]
        te=[r for r in rows if r['competition_id']==cid and r['season']==TEST]
        if len(tr)<300 or len(te)<100:
            excluded[cid]={'train_rows':len(tr),'test_rows':len(te),'reason':'INSUFFICIENT_COMPLETE_MARKET_ROWS'};continue
        ext[cid]=evaluate(te,tr)
    agg_ext=None
    if ext:
        keys=['all_matches','selected','total_hits','score_top1_hits','score_top3_hits','conditional_total_correct_count']
        sums={k:sum(v[k] for v in ext.values()) for k in keys};n=sums['selected'];c=sums['conditional_total_correct_count']
        agg_ext={**sums,'coverage':n/sums['all_matches'] if sums['all_matches'] else 0,
                 'total_accuracy':sums['total_hits']/n if n else None,
                 'score_top1_accuracy':sums['score_top1_hits']/n if n else None,
                 'score_top3_accuracy':sums['score_top3_hits']/n if n else None}
    payload={'schema_version':'V6.15.3-total-score-external-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
             'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_RESEARCH_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP',
             'frozen_rule':{'k':K,'over_weight':W,'pmin':PMIN,'gapmin':GAPMIN,'source':'V6.15.1','no_reselection':True},
             'provider_counts':providers,'fresh_dev_domain_blocks':blocks,'external_competitions':ext,'external_excluded':excluded,'external_aggregate':agg_ext,
             'governance':{'research_only':True,'formal_weight':0,'current_rule_change':False,'automatic_promotion':False,'final100_v6151_excluded_from_fresh_blocks':True}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'blocks':blocks,'external':ext,'aggregate':agg_ext},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
