#!/usr/bin/env python3
"""V6.15.3a frozen V6.15.1 rule on fresh 2025/26 100-match blocks.

No rule selection. The final 100 matches already seen by V6.15.1 are excluded.
Every reported block is therefore unused by the V6.15.1/V6.15.2 test diagnostics.
"""
from __future__ import annotations
import json,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];V=ROOT/'validation'
if str(V) not in sys.path:sys.path.insert(0,str(V))
import validate_score_market_state_fast100_v6145 as src
import validate_total_score_knn_fast100_v6151 as knn
OUT=ROOT/'manifests'/'v6_total_score_freshblocks_v6153a_status.json'
K=75;W=2.0;P=.32;G=.03

def evaluate(rows,train):
    pred=knn.predict_set(rows,train,K,W);sel=knn.filt(pred,P,G);te=knn.ev(sel)
    s1=s3=cond=cond1=cond3=0
    for r,t,pp,gg,th in sel:
        ranks=knn.ps(r,t,train,K,W);actual=tuple(r['score']);h1=actual in ranks[:1];h3=actual in ranks[:3]
        s1+=h1;s3+=h3
        if th:cond+=1;cond1+=h1;cond3+=h3
    n=len(sel)
    return {'all_matches':len(rows),'selected':n,'coverage':n/len(rows) if rows else 0,
            'total_hits':te['hits'],'total_accuracy':te['accuracy'],'wilson90_lower':te['wilson90_lower'],
            'score_top1_hits':s1,'score_top1_accuracy':s1/n if n else None,
            'score_top3_hits':s3,'score_top3_accuracy':s3/n if n else None,
            'conditional_total_correct_count':cond,
            'conditional_score_top1':cond1/cond if cond else None,
            'conditional_score_top3':cond3/cond if cond else None}

def main():
    rows=src.load();train=[r for r in rows if knn.sy(r['season'])<2025];test=[r for r in rows if r['season']=='2025/26']
    fresh=test[:-100]
    blocks=[]
    for i in range(0,len(fresh)-99,100):
        chunk=fresh[i:i+100];e=evaluate(chunk,train);e.update({'start':i,'stop':i+100,'first_date':chunk[0]['date'],'last_date':chunk[-1]['date']});blocks.append(e)
    selected=sum(x['selected'] for x in blocks);hits=sum(x['total_hits'] for x in blocks);s1=sum(x['score_top1_hits'] for x in blocks);s3=sum(x['score_top3_hits'] for x in blocks);cond=sum(x['conditional_total_correct_count'] for x in blocks)
    agg={'block_count':len(blocks),'all_matches':100*len(blocks),'selected':selected,'coverage':selected/(100*len(blocks)) if blocks else 0,
         'total_hits':hits,'total_accuracy':hits/selected if selected else None,
         'score_top1_hits':s1,'score_top1_accuracy':s1/selected if selected else None,
         'score_top3_hits':s3,'score_top3_accuracy':s3/selected if selected else None,
         'conditional_total_correct_count':cond,
         'blocks_total_ge_30pct':sum(1 for x in blocks if x['total_accuracy'] is not None and x['total_accuracy']>=.30),
         'blocks_total_ge_40pct':sum(1 for x in blocks if x['total_accuracy'] is not None and x['total_accuracy']>=.40)}
    out={'schema_version':'V6.15.3a-freshblocks-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_RESEARCH_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP','frozen_rule':{'k':K,'over_weight':W,'pmin':P,'gapmin':G,'no_reselection':True},'fresh_blocks':blocks,'aggregate':agg,'governance':{'research_only':True,'formal_weight':0,'current_rule_change':False,'automatic_promotion':False,'previously_seen_final100_excluded':True}}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
