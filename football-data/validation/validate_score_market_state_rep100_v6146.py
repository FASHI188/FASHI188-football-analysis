#!/usr/bin/env python3
from __future__ import annotations
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import validate_score_market_state_fast100_v6145 as b

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'manifests'/'v6_score_market_state_rep100_v6146_status.json'

def run(test,counters,mode):
    glob,c0,c1,_,_=counters
    h1=h3=0;preds=Counter()
    for r in test:
        cid=r['competition_id']
        chain=[c0[(cid,)],glob] if mode=='league' else [c1[(cid,r['result_pick'])],c0[(cid,)],glob]
        c=b.choose(chain);t1=b.topk(c,1);t3=b.topk(c,3);sc=r['score']
        if t1: preds[t1[0]]+=1
        h1+=int(sc in t1);h3+=int(sc in t3)
    n=len(test)
    return {'count':n,'top1_hits':h1,'top1_accuracy':h1/n,'top3_hits':h3,'top3_accuracy':h3/n,'top1_prediction_distribution':{f'{x[0]}-{x[1]}':v for x,v in preds.most_common()},'largest_single_top1_share':max(preds.values())/n if preds else None}

def main():
    rows=b.load();train=[r for r in rows if r['season'] in b.TRAIN];alltest=[r for r in rows if r['season']==b.TEST]
    if len(alltest)<200: raise RuntimeError('insufficient test rows')
    test=alltest[-200:-100];discovery=alltest[-100:]
    counters=b.train_counters(train)
    result={'league':run(test,counters,'league'),'result':run(test,counters,'result')}
    payload={'schema_version':'V6.14.6-score-market-state-rep100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_RESEARCH_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP','governance':{'fixed_training_2021_22_to_2024_25':True,'disjoint_previous_100':True,'no_test_selection':True,'formal_probability_change':False,'formal_weight_change':False,'current_rule_change':False},'sample':{'train_rows':len(train),'replication_first':test[0]['date'],'replication_last':test[-1]['date'],'discovery_first':discovery[0]['date'],'discovery_last':discovery[-1]['date']},'test':result}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2));return 0
if __name__=='__main__': raise SystemExit(main())
