#!/usr/bin/env python3
"""V6.14.7 diagnostic-only oracle-total Fast100.

Purpose: isolate the score-allocation bottleneck from the total-goals bottleneck.
For each test match ONLY as an oracle diagnostic, reveal the actual total goals T but not
the home/away split. Predict the exact score from training-only empirical P(score | league,T)
or P(score | league,T,1X2-market-pick). This is explicitly NON-OPERATIONAL and can never
enter prematch prediction.

Training: 2021/22-2024/25. Test: final 100 complete-market matches of 2025/26.
"""
from __future__ import annotations
import json
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
import validate_score_market_state_fast100_v6145 as base

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'manifests'/'v6_score_oracle_total_fast100_v6147_status.json'
MIN_CELL=20

def topk(c,k):return [s for s,_ in sorted(c.items(),key=lambda kv:(-kv[1],kv[0]))[:k]]
def train(rows):
    lt=defaultdict(Counter);ltr=defaultdict(Counter);tg=defaultdict(Counter)
    for r in rows:
        t=sum(r['score']);sc=r['score'];cid=r['competition_id'];lt[(cid,t)][sc]+=1;ltr[(cid,t,r['result_pick'])][sc]+=1;tg[t][sc]+=1
    return lt,ltr,tg

def choose(chain):
    for c in chain:
        if sum(c.values())>=MIN_CELL:return c
    return chain[-1]

def evaluate(test,counters,with_result):
    lt,ltr,tg=counters;h1=h3=h5=0;preds=Counter();cell_sizes=Counter()
    for r in test:
        t=sum(r['score']);cid=r['competition_id']
        chain=[ltr[(cid,t,r['result_pick'])],lt[(cid,t)],tg[t]] if with_result else [lt[(cid,t)],tg[t]]
        c=choose(chain);cell_sizes[sum(c.values())]+=1;a=topk(c,1);b=topk(c,3);d=topk(c,5);sc=r['score']
        if a:preds[a[0]]+=1
        h1+=int(sc in a);h3+=int(sc in b);h5+=int(sc in d)
    n=len(test)
    return {'count':n,'top1_hits':h1,'top1_accuracy':h1/n,'top3_hits':h3,'top3_accuracy':h3/n,'top5_hits':h5,'top5_accuracy':h5/n,'top1_prediction_distribution':{f'{a}-{b}':v for (a,b),v in preds.most_common()},'training_cell_sizes_used':dict(sorted(cell_sizes.items()))}

def main():
    rows=base.load();train_rows=[r for r in rows if r['season'] in base.TRAIN];test_all=[r for r in rows if r['season']==base.TEST]
    if len(test_all)<100:raise RuntimeError('insufficient test rows')
    test=test_all[-100:];c=train(train_rows)
    result={'oracle_T_league_only':evaluate(test,c,False),'oracle_T_plus_market_result_pick':evaluate(test,c,True)}
    payload={'schema_version':'V6.14.7-score-oracle-total-fast100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'NON_OPERATIONAL_ORACLE_DIAGNOSTIC_RETROSPECTIVE_RESEARCH_ONLY','governance':{'actual_total_goals_used_only_as_oracle_diagnostic':True,'actual_home_away_split_hidden':True,'training_seasons':sorted(base.TRAIN),'test_matches':100,'never_eligible_for_formal_prediction':True,'formal_probability_change':False,'formal_weight_change':False,'current_rule_change':False},'sample':{'train_rows':len(train_rows),'test_first':test[0]['date'],'test_last':test[-1]['date']},'test':result}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(result,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
