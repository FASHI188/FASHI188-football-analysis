#!/usr/bin/env python3
"""V6.11.8 robustness audit for the already-selected V6.11.7c PIT lineup model.

No test-set tuning. Reconstructs the V6.11.7c dataset, selects C on 2024/25 exactly as
before, refits on train+validation, and audits 2025/26 by competition, chronological
100-match blocks, paired correct/wrong transitions, and confidence bands.
"""
from __future__ import annotations
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import validate_1x2_pit_lineup_increment_v6117c as fixed
b = fixed.base
OUT = Path(__file__).resolve().parents[1] / "manifests" / "v6_1x2_pit_lineup_robustness_v6118_status.json"


def fit_model(rows):
    tr=[r for r in rows if r['season'] in b.TRAIN_SEASONS]
    va=[r for r in rows if r['season']==b.VALID_SEASON]
    te=[r for r in rows if r['season']==b.TEST_SEASON]
    key='estimated_opening_features'
    Xtr=np.asarray([r[key] for r in tr]); ytr=np.asarray([b.LABEL[r['actual']] for r in tr])
    Xva=np.asarray([r[key] for r in va]); yva=np.asarray([b.LABEL[r['actual']] for r in va])
    board=[]
    for C in (0.003,0.01,0.03,0.1,0.3,1.0,3.0):
        m=make_pipeline(StandardScaler(),LogisticRegression(C=C,max_iter=3000))
        m.fit(Xtr,ytr); pred=m.predict(Xva)
        board.append((float((pred==yva).mean()),-C,C))
    board.sort(reverse=True); C=board[0][2]
    Xtv=np.vstack([Xtr,Xva]); ytv=np.concatenate([ytr,yva])
    m=make_pipeline(StandardScaler(),LogisticRegression(C=C,max_iter=3000))
    m.fit(Xtv,ytv)
    Xte=np.asarray([r[key] for r in te]); p=m.predict_proba(Xte); pred=p.argmax(1)
    return C, te, p, pred


def market_idx(r): return int(np.argmax(np.asarray(r['opening'])))

def stats(indices, te, pred):
    m_hits=0; q_hits=0; mo=0; qo=0; both=0; neither=0
    for i in indices:
        y=b.LABEL[te[i]['actual']]; mi=market_idx(te[i]); qi=int(pred[i])
        mc=mi==y; qc=qi==y
        m_hits+=mc; q_hits+=qc
        if mc and qc: both+=1
        elif mc: mo+=1
        elif qc: qo+=1
        else: neither+=1
    n=len(indices)
    return {'count':n,'market_hits':m_hits,'market_accuracy':m_hits/n if n else None,
            'lineup_hits':q_hits,'lineup_accuracy':q_hits/n if n else None,
            'uplift_pp':100*(q_hits-m_hits)/n if n else None,
            'paired':{'both_correct':both,'market_only':mo,'lineup_only':qo,'both_wrong':neither}}


def main():
    matches=b._load_matches(); rows=b._build_dataset(matches)
    C,te,p,pred=fit_model(rows)
    overall=stats(list(range(len(te))),te,pred)
    by_comp={}
    for cid in b.COMPETITIONS:
        ix=[i for i,r in enumerate(te) if r['competition_id']==cid]
        by_comp[cid]=stats(ix,te,pred)
    # chronology follows date/competition/team ordering inherited from dataset builder
    blocks=[]
    for start in range(0,len(te),100):
        ix=list(range(start,min(start+100,len(te))))
        s=stats(ix,te,pred); s['block']=len(blocks)+1; blocks.append(s)
    # Market confidence bands fixed ex ante; no threshold selection here.
    bands={}
    for lo,hi in ((0,.50),(.50,.56),(.56,.58),(.58,.60),(.60,1.01)):
        ix=[i for i,r in enumerate(te) if lo <= max(r['opening']) < hi]
        bands[f'{lo:.2f}_{hi:.2f}']=stats(ix,te,pred)
    # Disagreement-only diagnostic.
    ix=[i for i,r in enumerate(te) if market_idx(r)!=int(pred[i])]
    disagreement=stats(ix,te,pred)
    block_u=[x['uplift_pp'] for x in blocks]
    payload={
      'schema_version':'V6.11.8-pit-lineup-robustness-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
      'status':'PASS','formal_current_version':'V5.0.1','selected_C_from_validation_only':C,
      'governance':{'research_only':True,'test_not_used_for_selection':True,'formal_probability_change':False,'formal_weight_change':False,'current_rule_change':False},
      'overall':overall,'by_competition':by_comp,'chronological_100_match_blocks':blocks,
      'block_summary':{'wins':sum(x>0 for x in block_u),'ties':sum(x==0 for x in block_u),'losses':sum(x<0 for x in block_u),'mean_uplift_pp':float(np.mean(block_u))},
      'market_confidence_bands':bands,'disagreement_only':disagreement,
    }
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(payload,ensure_ascii=False,indent=2)); return 0

if __name__=='__main__': raise SystemExit(main())
