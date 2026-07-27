#!/usr/bin/env python3
"""V6.46.8 compare historical fixed1000 selective performance with the current forward domain mix.
Read-only diagnostic; no selector or probability changes.
"""
from __future__ import annotations
import json, math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
BENCH=ROOT/'benchmarks'/'v6_1x2_neutral_fixed1000_v6131.json'
FORWARD=ROOT/'manifests'/'v6_forward_vs_historical_gap_v6466_status.json'
OUT=ROOT/'manifests'/'v6_domain_shift_v6468_status.json'
D=('home','draw','away')

def now()->str:return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
def metric(rows:list[dict[str,Any]])->dict[str,Any]:
    n=len(rows)
    if not n:return {'count':0}
    h=sum(r['pick']==r['actual'] for r in rows);ll=br=rps=0.0
    for r in rows:
        p=r['p'];y={d:1.0 if r['actual']==d else 0.0 for d in D};ll-=math.log(max(1e-15,p[r['actual']]));br+=sum((p[d]-y[d])**2 for d in D);rps+=((p['home']-y['home'])**2+((p['home']+p['draw'])-(y['home']+y['draw']))**2)/2
    return {'count':n,'hits':h,'accuracy':h/n,'log_loss':ll/n,'brier':br/n,'rps':rps/n}
def main()->int:
    b=json.loads(BENCH.read_text(encoding='utf-8'));f=json.loads(FORWARD.read_text(encoding='utf-8'));forward_comps=sorted(f.get('by_competition',{}))
    rows=[]
    for r in b.get('rows',[]):
        m=r.get('market')
        if not m:continue
        p={d:float(m['probabilities'][d]) for d in D};pick=str(m['pick']);rows.append({'competition_id':r['competition_id'],'actual':r['actual'],'p':p,'pick':pick,'pmax':float(m['pmax'])})
    def selected(r):return (r['pick']=='home' and r['pmax']>=.66) or (r['pick']=='away' and r['pmax']>=.60)
    all_sel=[r for r in rows if selected(r)];mix=[r for r in rows if r['competition_id'] in forward_comps];mix_sel=[r for r in mix if selected(r)]
    by_comp={}
    for c in sorted({r['competition_id'] for r in rows}):
        cr=[r for r in rows if r['competition_id']==c];cs=[r for r in cr if selected(r)];by_comp[c]={'all_market':metric(cr),'selected_066_060':metric(cs),'selected_coverage':len(cs)/len(cr) if cr else 0.0,'currently_in_forward_mix':c in forward_comps}
    payload={'schema_version':'V6.46.8-domain-shift-r1','generated_at_utc':now(),'status':'PASS_DIAGNOSTIC','forward_competitions':forward_comps,'historical_all_market':metric(rows),'historical_all_selected_066_060':metric(all_sel),'historical_forward_mix_market_only':metric(mix),'historical_forward_mix_selected_066_060':metric(mix_sel),'by_competition':by_comp,'forward_current_by_competition':f.get('by_competition',{}),'notes':{'KOR_KLeague1_historical_market_missing':by_comp.get('KOR_KLeague1',{}).get('all_market',{}).get('count',0)==0,'UEFA_ChampionsLeague_historical_market_missing':by_comp.get('UEFA_ChampionsLeague',{}).get('all_market',{}).get('count',0)==0,'diagnostic_only':True,'cannot_tune_selector':True},'governance':{'runtime_probability_change':False,'formal_weight_change':False,'current_rule_change':False}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding='utf-8');print(json.dumps({'forward_comps':forward_comps,'hist_all_sel':payload['historical_all_selected_066_060'],'hist_forward_mix_sel':payload['historical_forward_mix_selected_066_060'],'by_comp':{c:v['selected_066_060'] for c,v in by_comp.items() if c in forward_comps}},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
