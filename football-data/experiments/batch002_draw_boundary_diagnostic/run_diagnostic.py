#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
SRC=HERE.parent/'batch002_reveal_score'/'results'/'summary_batch002_reveal.json'
OUT=HERE/'results'/'summary_batch002_draw_boundary.json'
MODELS=('S60','S70_Robust','S80_RobustCompactDraw')
LABELS=('HOME','DRAW','AWAY')
GRID=(0.00,0.01,0.02,0.03,0.04,0.05,0.075,0.10,0.125,0.15,0.20)

def probs(r,key):
    q=r[key]; return [float(q['p_home']),float(q['p_draw']),float(q['p_away'])]

def rank_draw(p):
    return 1+sum(x>p[1] for i,x in enumerate(p) if i!=1)

def qtile(xs,q):
    z=sorted(xs)
    if not z:return None
    pos=(len(z)-1)*q; lo=int(pos); hi=min(lo+1,len(z)-1); w=pos-lo
    return z[lo]*(1-w)+z[hi]*w

def summarize(rows,key):
    actual_draw=[r for r in rows if r['actual']=='DRAW']
    nondraw=[r for r in rows if r['actual']!='DRAW']
    def margins(group):
        out=[]
        for r in group:
            p=probs(r,key); out.append(max(p[0],p[2])-p[1])
        return out
    dm=margins(actual_draw); nm=margins(nondraw)
    ranks={str(k):sum(rank_draw(probs(r,key))==k for r in actual_draw) for k in (1,2,3)}
    near={str(t):sum(m<=t for m in dm) for t in (0.01,0.02,0.03,0.05,0.075,0.10,0.15,0.20)}
    sims=[]
    for d in GRID:
        hits=draw_hits=draw_picks=false_draw=0
        for r in rows:
            p=probs(r,key); adj=[p[0],p[1]+d,p[2]]; pred=max(range(3),key=lambda i:adj[i]); y=LABELS.index(r['actual'])
            hits+=pred==y; draw_picks+=pred==1; draw_hits+=(pred==1 and y==1); false_draw+=(pred==1 and y!=1)
        sims.append({'draw_add':d,'hits':hits,'accuracy':hits/len(rows),'draw_picks':draw_picks,'draw_hits':draw_hits,'draw_recall':draw_hits/len(actual_draw),'false_draw_picks':false_draw})
    return {
        'actual_draw_count':len(actual_draw),
        'actual_draw_rank':ranks,
        'actual_draw_margin_top_nondraw_minus_draw':{'mean':sum(dm)/len(dm),'q25':qtile(dm,.25),'median':qtile(dm,.5),'q75':qtile(dm,.75),'max':max(dm)},
        'nondraw_margin_top_nondraw_minus_draw':{'mean':sum(nm)/len(nm),'q25':qtile(nm,.25),'median':qtile(nm,.5),'q75':qtile(nm,.75),'min':min(nm)},
        'actual_draw_within_margin':near,
        'global_additive_draw_sweep_DIAGNOSTIC_ONLY':sims,
    }

def main():
    s=json.loads(SRC.read_text(encoding='utf-8'))
    assert s['status']=='BATCH002_REVEALED_SCORED'
    rows=s['scored_rows']; assert len(rows)==100 and sum(r['actual']=='DRAW' for r in rows)==24
    out={
      'schema_version':'football3-batch002-draw-boundary-diagnostic-v1',
      'status':'BATCH002_DRAW_BOUNDARY_DIAGNOSTIC_COMPLETE',
      'classification':'POST_REVEAL_DIAGNOSTIC_ONLY_NOT_MODEL_SELECTION',
      'source_reveal_commit':'4bc8c8238012f061ffe76de1dfc321a8c6447cf8',
      'governance':{
        'Batch002_already_revealed':True,
        'diagnostic_may_explain_failure_modes':True,
        'diagnostic_must_not_be_used_to_claim_fresh_improvement':True,
        'next_candidate_must_be_locked_before_next_fresh_batch_reveal':True
      },
      'models':{k:summarize(rows,k) for k in MODELS}
    }
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(out,indent=2))

if __name__=='__main__':main()
