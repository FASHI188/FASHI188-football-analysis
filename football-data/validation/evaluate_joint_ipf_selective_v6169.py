#!/usr/bin/env python3
"""Strict prior-season abstention-gate audit for V6.16.4 exact total and score."""
from __future__ import annotations
import json,math
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
CACHE=ROOT/'validation'/'cache'/'v6169_joint_ipf_selection_rows.json';OUT=ROOT/'manifests'/'v6_joint_ipf_selective_v6169_status.json'
SEASONS=('2022/23','2023/24','2024/25','2025/26');TESTS=SEASONS[1:];Z=1.6448536269514722
TP=(.20,.22,.24,.26,.28,.30,.32,.34,.36,.38);TG=(0,.01,.02,.03,.04,.05,.06,.08)
SP=(.06,.08,.10,.12,.14,.16,.18,.20,.22,.24);SG=(0,.005,.01,.015,.02,.03,.04,.05)
MINN=100;MINC=.05

def wilson(h,n):
    if not n:return None
    p=h/n;z2=Z*Z;d=1+z2/n;c=p+z2/(2*n);r=Z*math.sqrt((p*(1-p)+z2/(4*n))/n);return (c-r)/d
def ev(rows,k,p,g):
    s=[r for r in rows if r[k+'_p']>=p and r[k+'_gap']>=g];n=len(s);h=sum(r[k+'_hit'] for r in s)
    return {'count':n,'hits':h,'accuracy':h/n if n else None,'wilson90_lower':wilson(h,n),'coverage':n/len(rows) if rows else 0,'pmin':p,'gapmin':g}
def choose(rows,k):
    pg,gg=(TP,TG) if k=='total' else (SP,SG);cand=[]
    for p in pg:
      for g in gg:
        e=ev(rows,k,p,g);e['admissible']=e['count']>=MINN and e['coverage']>=MINC and e['wilson90_lower'] is not None;cand.append(e)
    a=[x for x in cand if x['admissible']]
    if not a:return max(cand,key=lambda x:(x['count'],x['accuracy'] or 0))
    return max(a,key=lambda x:(x['wilson90_lower'],x['accuracy'],x['count']))
def records(rows,k,rule,fold):
    return [{'fold':fold,'date':r['date'],'competition_id':r['competition_id'],'hit':r[k+'_hit']} for r in rows if r[k+'_p']>=rule['pmin'] and r[k+'_gap']>=rule['gapmin']]
def agg(r):
    n=len(r);h=sum(x['hit'] for x in r);return {'count':n,'hits':h,'accuracy':h/n if n else None,'wilson90_lower':wilson(h,n)}
def blocks(r):
    r=sorted(r,key=lambda x:(x['date'],x['competition_id']));b=[]
    for i in range(0,len(r)-99,100):
        c=r[i:i+100];h=sum(x['hit'] for x in c);b.append({'start':i,'stop':i+100,'first_date':c[0]['date'],'last_date':c[-1]['date'],'hits':h,'accuracy':h/100})
    a=[x['accuracy'] for x in b]
    return {'blocks':b,'summary':{'block_count':len(b),'worst_accuracy':min(a) if a else None,'mean_accuracy':sum(a)/len(a) if a else None,'blocks_ge_30pct':sum(x>=.30 for x in a),'blocks_ge_40pct':sum(x>=.40 for x in a),'blocks_ge_50pct':sum(x>=.50 for x in a)}}
def main():
    rows=json.loads(CACHE.read_text(encoding='utf-8'))['rows'];folds=[];tr=[];sr=[]
    for test in TESTS:
        y=int(test[:4]);dev=[r for r in rows if int(r['season'][:4])<y];te=[r for r in rows if r['season']==test];rt=choose(dev,'total');rs=choose(dev,'score');et=ev(te,'total',rt['pmin'],rt['gapmin']);es=ev(te,'score',rs['pmin'],rs['gapmin']);folds.append({'test_season':test,'development_count':len(dev),'test_count':len(te),'total_rule':rt,'total_test':et,'score_rule':rs,'score_test':es});tr+=records(te,'total',rt,test);sr+=records(te,'score',rs,test)
    out={'schema_version':'V6.16.9-joint-ipf-selective-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_MARKET_RESEARCH_NO_ORIGINAL_QUOTE_TIMESTAMP','design':{'base':'V6.16.4','strict_prior_season_selection':True,'objective':'maximize development Wilson90 lower; minimum count 100 and coverage 5%','test_used_for_gate_selection':False},'folds':folds,'aggregate_selected_total':agg(tr),'aggregate_selected_score':agg(sr),'selected100_total':blocks(tr),'selected100_score':blocks(sr),'governance':{'research_only':True,'formal_weight':0,'current_rule_change':False,'automatic_promotion':False}}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'folds':folds,'total':out['aggregate_selected_total'],'score':out['aggregate_selected_score'],'tb':out['selected100_total']['summary'],'sb':out['selected100_score']['summary']},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
