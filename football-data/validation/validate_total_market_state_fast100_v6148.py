#!/usr/bin/env python3
"""V6.14.8 research-only Fast100: data-driven exact-total-goals market-state challenger.

No total-goal class is hand-assigned. Training-only empirical distributions predict exact
T from fixed market-state variants.
Training: 2021/22-2024/25. Test: final 100 complete 1X2+O/U2.5 matches of 2025/26.
Variants are all reported, no test selection:
  league
  league + O/U2.5 direction
  league + 1X2 direction + O/U2.5 direction
  league + 1X2 direction + O/U2.5 direction + fixed 1X2 confidence bin
Hierarchical backoff is deterministic for cells with <30 training matches.
"""
from __future__ import annotations
import json
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
import validate_score_market_state_fast100_v6145 as base

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'manifests'/'v6_total_market_state_fast100_v6148_status.json'
MIN_CELL=30

def topk(c,k):return [t for t,_ in sorted(c.items(),key=lambda kv:(-kv[1],kv[0]))[:k]]
def train(rows):
    glob=Counter();l=defaultdict(Counter);lo=defaultdict(Counter);lro=defaultdict(Counter);lroc=defaultdict(Counter)
    for r in rows:
        t=sum(r['score']);cid=r['competition_id'];glob[t]+=1;l[(cid,)][t]+=1;lo[(cid,r['ou_pick'])][t]+=1;lro[(cid,r['result_pick'],r['ou_pick'])][t]+=1;lroc[(cid,r['result_pick'],r['ou_pick'],r['conf_bin'])][t]+=1
    return glob,l,lo,lro,lroc

def choose(chain):
    for c in chain:
        if sum(c.values())>=MIN_CELL:return c
    return chain[-1]

def evaluate(test,counters,mode):
    glob,l,lo,lro,lroc=counters;h1=h2=h3=0;pred=Counter();cells=Counter()
    for r in test:
        cid=r['competition_id'];actual=sum(r['score'])
        if mode=='league':chain=[l[(cid,)],glob]
        elif mode=='league_ou':chain=[lo[(cid,r['ou_pick'])],l[(cid,)],glob]
        elif mode=='league_result_ou':chain=[lro[(cid,r['result_pick'],r['ou_pick'])],lo[(cid,r['ou_pick'])],l[(cid,)],glob]
        else:chain=[lroc[(cid,r['result_pick'],r['ou_pick'],r['conf_bin'])],lro[(cid,r['result_pick'],r['ou_pick'])],lo[(cid,r['ou_pick'])],l[(cid,)],glob]
        c=choose(chain);cells[sum(c.values())]+=1;a=topk(c,1);b=topk(c,2);d=topk(c,3)
        if a:pred[a[0]]+=1
        h1+=int(actual in a);h2+=int(actual in b);h3+=int(actual in d)
    n=len(test);return {'count':n,'top1_hits':h1,'top1_accuracy':h1/n,'top2_hits':h2,'top2_accuracy':h2/n,'top3_hits':h3,'top3_accuracy':h3/n,'top1_prediction_distribution':dict(sorted(pred.items())),'training_cell_sizes_used':dict(sorted(cells.items()))}

def main():
    rows=base.load();train_rows=[r for r in rows if r['season'] in base.TRAIN];test_all=[r for r in rows if r['season']==base.TEST]
    if len(test_all)<100:raise RuntimeError('insufficient test rows')
    test=test_all[-100:];c=train(train_rows);modes=('league','league_ou','league_result_ou','league_result_ou_conf');result={m:evaluate(test,c,m) for m in modes}
    payload={'schema_version':'V6.14.8-total-market-state-fast100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_RESEARCH_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP','governance':{'no_hand_assigned_total_class':True,'training_seasons':sorted(base.TRAIN),'test_matches':100,'fixed_min_cell':MIN_CELL,'all_variants_reported_no_test_selection':True,'formal_probability_change':False,'formal_weight_change':False,'current_rule_change':False},'sample':{'train_rows':len(train_rows),'test_season_rows':len(test_all),'test_first':test[0]['date'],'test_last':test[-1]['date']},'test':result}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(result,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
