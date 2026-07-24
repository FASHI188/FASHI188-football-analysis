#!/usr/bin/env python3
"""V6.15.2 research-only validation-frozen precision/coverage frontier.
Policies are defined only from 2022/23-2024/25 chronological validation folds. The already-used
2025/26 final-100 panel is diagnostic only and cannot promote any rule.
"""
from __future__ import annotations
import json,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];V=ROOT/'validation'
if str(V) not in sys.path:sys.path.insert(0,str(V))
import validate_total_score_knn_fast100_v6151 as b
OUT=ROOT/'manifests'/'v6_total_score_frontier_v6152_status.json'

def candidates(rows):
    cache={};out=[]
    for k in b.KS:
      for w in b.OW:
        folds=[]
        for vs in b.VALS:
            tr=[r for r in rows if b.sy(r['season'])<b.sy(vs)];va=[r for r in rows if r['season']==vs];pr=b.predict_set(va,tr,k,w);cache[(k,w,vs)]=pr
        for p in b.PGRID:
          for g in b.GGRID:
            st=[];ok=True
            for vs in b.VALS:
                s=b.ev(b.filt(cache[(k,w,vs)],p,g));st.append(s);ok=ok and s['count']>=b.MINSEL
            if ok:
                n=sum(x['count'] for x in st);h=sum(x['hits'] for x in st);out.append({'k':k,'w':w,'p':p,'g':g,'folds':st,'count':n,'accuracy':h/n,'lower':b.wl(h,n),'worst':min(x['accuracy'] for x in st)})
    return out

def choose(cs):
    precision=max(cs,key=lambda x:(x['worst'],x['lower'],x['accuracy'],x['count']))
    bal=[x for x in cs if x['worst']>=.25 and x['accuracy']>=.27]
    balanced=max(bal,key=lambda x:(x['count'],x['lower'],x['accuracy'])) if bal else precision
    cov=[x for x in cs if x['worst']>=.23 and x['accuracy']>=.26]
    coverage=max(cov,key=lambda x:(x['count'],x['lower'],x['accuracy'])) if cov else balanced
    return {'precision':precision,'balanced':balanced,'coverage':coverage}

def test_policy(rule,rows):
    tr=[r for r in rows if b.sy(r['season'])<b.sy(b.TEST)];te=[r for r in rows if r['season']==b.TEST][-100:]
    pr=b.predict_set(te,tr,rule['k'],rule['w']);sel=b.filt(pr,rule['p'],rule['g']);tot=b.ev(sel);sm=(tr,)
    s1=s3=cond=cond1=0
    for r,t,pp,gg,th in sel:
        ranks=b.ps(r,t,tr,rule['k'],rule['w']);actual=tuple(r['score']);h1=actual in ranks[:1];s1+=h1;s3+=actual in ranks[:3]
        if th:cond+=1;cond1+=h1
    n=len(sel);return {'selected':n,'total_hits':tot['hits'],'total_accuracy':tot['accuracy'],'score_top1_hits':s1,'score_top1_accuracy':s1/n if n else None,'score_top3_hits':s3,'score_top3_accuracy':s3/n if n else None,'conditional_total_correct_count':cond,'conditional_score_top1':cond1/cond if cond else None}

def compact(x):return {k:v for k,v in x.items() if k!='folds'}
def main():
    rows=b.src.load();cs=candidates(rows);pol=choose(cs);res={name:{'rule':compact(r),'diagnostic_test':test_policy(r,rows)} for name,r in pol.items()}
    out={'schema_version':'V6.15.2-total-score-frontier-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_RESEARCH_DIAGNOSTIC_TEST_REUSED','policy_definitions':{'precision':'maximize worst validation-season exact-total accuracy','balanced':'maximize validation count subject to worst>=25% and aggregate>=27%','coverage':'maximize validation count subject to worst>=23% and aggregate>=26%'},'candidate_count':len(cs),'policies':res,'governance':{'research_only':True,'formal_weight':0,'test_panel_previously_seen':True,'no_promotion_from_v6152':True,'current_rule_change':False}}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
