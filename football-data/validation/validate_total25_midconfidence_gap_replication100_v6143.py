#!/usr/bin/env python3
"""V6.14.3 fixed disjoint replication of the V6.14.2 O/U2.5 mid-confidence anomaly.

Frozen rule from discovery: using three-book mean de-vigged O/U2.5 probability, exclude
matches whose selected-side confidence is in [0.56, 0.60). No threshold optimization,
direction split, league filter, or model fitting is allowed here.

Replication block = the 100 triple-provider matches immediately preceding the V6.14.2
100-match discovery block. Research only; historical quote timestamps unavailable.
"""
from __future__ import annotations
import json
from datetime import datetime,timezone
from pathlib import Path
import validate_total25_multibook_fast100_v6141 as base

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'manifests'/'v6_total25_midconfidence_gap_replication100_v6143_status.json'

def pick(r):return 1 if r['consensus']>=0.5 else 0
def stat(rows,gate=lambda r:True):
    s=[r for r in rows if gate(r)];h=sum(int(pick(r)==r['actual']) for r in s)
    return {'count':len(s),'hits':h,'accuracy':h/len(s) if s else None,'coverage':len(s)/len(rows) if rows else 0.0}
def main():
    rows,_=base.load()
    if len(rows)<200:raise RuntimeError(f'need >=200 triple-provider rows, found {len(rows)}')
    discovery=rows[-100:];test=rows[-200:-100]
    if {id(r) for r in discovery} & {id(r) for r in test}:raise RuntimeError('replication slice overlap detected')
    def midgap(r):
        c=max(r['consensus'],1-r['consensus']);return 0.56<=c<0.60
    allm=stat(test);filtered=stat(test,lambda r:not midgap(r));gap=stat(test,midgap)
    uplift=None if allm['accuracy'] is None or filtered['accuracy'] is None else (filtered['accuracy']-allm['accuracy'])*100.0
    payload={'schema_version':'V6.14.3-total25-midconfidence-gap-replication100-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','classification':'RETROSPECTIVE_RESEARCH_ONLY_NO_ORIGINAL_QUOTE_TIMESTAMP','governance':{'fixed_confidence_exclusion':[0.56,0.60],'upper_bound_exclusive':True,'three_book_mean_consensus':True,'no_direction_split':True,'no_league_filter':True,'no_model_fit':True,'disjoint_by_nonoverlapping_source_row_slices':True,'formal_probability_change':False,'formal_weight_change':False,'current_rule_change':False},'sample':{'triple_provider_rows':len(rows),'replication_count':100,'replication_first':test[0]['date'],'replication_last':test[-1]['date'],'discovery_first':discovery[0]['date'],'discovery_last':discovery[-1]['date']},'test':{'all':allm,'excluded_midconfidence_band':gap,'after_exclusion':filtered},'uplift_pp':uplift}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'sample':payload['sample'],'test':payload['test'],'uplift_pp':uplift},indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
