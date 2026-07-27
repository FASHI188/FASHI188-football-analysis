#!/usr/bin/env python3
"""V6.46.6 diagnose the retrospective-vs-prospective 1X2 accuracy gap.

Read-only diagnostic. It joins immutable V6.5.1 frozen market predictions to official
settlements and evaluates predeclared confidence/lead-time slices. It does not tune or
change any live selector. Historical reference numbers come from the frozen neutral
fixed1000 status and are compared only descriptively.
"""
from __future__ import annotations

import json, math, statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT=Path(__file__).resolve().parents[1]
LEDGER=ROOT/'forward'/'v6_market_first_events_v651.json'
HIST=ROOT/'manifests'/'v6_1x2_neutral_fixed1000_v6131_status.json'
OUT=ROOT/'manifests'/'v6_forward_vs_historical_gap_v6466_status.json'
DIRECTIONS=('home','draw','away')
Z95=1.959963984540054


def utc_now()->str:return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def dt(s:Any)->datetime:return datetime.fromisoformat(str(s).replace('Z','+00:00'))

def wilson(h:int,n:int)->list[float|None]:
    if n<=0:return [None,None]
    p=h/n;z2=Z95*Z95;den=1+z2/n;center=(p+z2/(2*n))/den;rad=Z95*math.sqrt((p*(1-p)+z2/(4*n))/n)/den
    return [max(0,center-rad),min(1,center+rad)]

def metrics(rows:list[dict[str,Any]])->dict[str,Any]:
    n=len(rows)
    if not n:return {'count':0}
    hits=sum(r['pick']==r['actual'] for r in rows);eps=1e-15;ll=br=rp=0.0
    for r in rows:
        p=r['prob'];a=r['actual'];y={d:1.0 if a==d else 0.0 for d in DIRECTIONS}
        ll-=math.log(max(eps,float(p[a])));br+=sum((float(p[d])-y[d])**2 for d in DIRECTIONS)
        rp+=((float(p['home'])-y['home'])**2+((float(p['home'])+float(p['draw']))-(y['home']+y['draw']))**2)/2
    return {'count':n,'hits':hits,'accuracy':hits/n,'wilson95':wilson(hits,n),'log_loss':ll/n,'brier':br/n,'rps':rp/n,'mean_pmax':statistics.mean(r['pmax'] for r in rows),'mean_margin':statistics.mean(r['margin'] for r in rows),'mean_lead_hours':statistics.mean(r['lead_hours'] for r in rows)}

def load_rows()->list[dict[str,Any]]:
    ledger=json.loads(LEDGER.read_text(encoding='utf-8'));pred={};settled={}
    for e in ledger.get('events',[]):
        if not isinstance(e,dict):continue
        mid=str(e.get('match_id') or '')
        if e.get('event_type')=='MARKET_PREDICTION_FROZEN':pred[mid]=e
        elif e.get('event_type')=='RESULT_SETTLED':settled[mid]=e
    rows=[]
    for mid,pred_e in pred.items():
        set_e=settled.get(mid)
        if not set_e:continue
        payload=pred_e.get('payload') or {};identity=payload.get('fixture_identity') or {};prediction=payload.get('prediction') or {};prob=prediction.get('probabilities') or {}
        if not all(d in prob for d in DIRECTIONS):continue
        probs={d:float(prob[d]) for d in DIRECTIONS};pick=str(prediction.get('pick') or max(DIRECTIONS,key=lambda d:probs[d]));ordered=sorted(probs.values(),reverse=True);pmax=max(probs.values());margin=ordered[0]-ordered[1]
        result=((set_e.get('payload') or {}).get('result') or {});actual=str(result.get('actual_result') or '')
        if actual not in DIRECTIONS:continue
        kickoff=dt(identity.get('kickoff_at'));observed=dt(((payload.get('market_source') or {}).get('source_observed_at_utc')) or pred_e.get('event_timestamp_utc'))
        lead=(kickoff-observed).total_seconds()/3600
        odds=((payload.get('frozen_surfaces') or {}).get('one_x_two_odds') or {})
        rows.append({'match_id':mid,'competition_id':identity.get('competition_id'),'kickoff_at':kickoff.isoformat(),'observed_at':observed.isoformat(),'lead_hours':lead,'home_team':identity.get('home_team'),'away_team':identity.get('away_team'),'prob':probs,'pick':pick,'actual':actual,'pmax':pmax,'margin':margin,'selected_arm_a':bool(prediction.get('selected_arm_a')),'odds':odds})
    return sorted(rows,key=lambda r:(r['kickoff_at'],r['match_id']))

def sliced(rows:list[dict[str,Any]],fn:Callable[[dict[str,Any]],bool])->dict[str,Any]:return metrics([r for r in rows if fn(r)])

def main()->int:
    rows=load_rows();hist=json.loads(HIST.read_text(encoding='utf-8'))
    by_comp={c:metrics([r for r in rows if r['competition_id']==c]) for c in sorted({str(r['competition_id']) for r in rows})}
    by_pick={d:metrics([r for r in rows if r['pick']==d]) for d in DIRECTIONS}
    lead_bins={
      'H1_6':sliced(rows,lambda r:1<=r['lead_hours']<6),
      'H6_24':sliced(rows,lambda r:6<=r['lead_hours']<24),
      'H24_48':sliced(rows,lambda r:24<=r['lead_hours']<48),
      'H48_72':sliced(rows,lambda r:48<=r['lead_hours']<=72),
      'GT72':sliced(rows,lambda r:r['lead_hours']>72),
    }
    # Predeclared diagnostic thresholds only; no selector is chosen from these results.
    pmax_grid={f'{x:.2f}':sliced(rows,lambda r,x=x:r['pick']!='draw' and r['pmax']>=x) for x in (0.55,0.60,0.62,0.64,0.66,0.68,0.70)}
    margin_grid={f'{x:.2f}':sliced(rows,lambda r,x=x:r['pick']!='draw' and r['margin']>=x) for x in (0.20,0.25,0.30,0.35,0.40,0.45)}
    frozen_066_060=sliced(rows,lambda r:(r['pick']=='home' and r['pmax']>=0.66) or (r['pick']=='away' and r['pmax']>=0.60))
    arm_a=sliced(rows,lambda r:r['selected_arm_a'])
    historical=hist.get('market_track_selective_rules') or {}
    payload={
      'schema_version':'V6.46.6-forward-vs-historical-gap-r1',
      'generated_at_utc':utc_now(),
      'status':'PASS_DIAGNOSTIC',
      'settled_forward_count':len(rows),
      'all_forward':metrics(rows),
      'by_competition':by_comp,
      'by_pick':by_pick,
      'lead_time_bins':lead_bins,
      'predeclared_pmax_grid':pmax_grid,
      'predeclared_margin_grid':margin_grid,
      'forward_frozen_066_060_rule':frozen_066_060,
      'forward_existing_arm_a_margin035':arm_a,
      'historical_fixed1000_references':{
        'market_available_count':hist.get('market_track_metrics_on_available_subset',{}).get('count'),
        'market_all_accuracy':hist.get('market_track_metrics_on_available_subset',{}).get('accuracy'),
        'frozen_066_060':historical.get('v6128_066_060'),
        'baseline_062_062':historical.get('baseline_062_062'),
      },
      'diagnostic_questions':{
        'historical_closing_like_vs_prospective_first_eligible_snapshot_mismatch':True,
        'competition_mix_shift_reported':True,
        'lead_time_shift_reported':True,
        'selector_definition_mismatch_reported':True,
        'small_forward_sample_warning':len(rows)<100,
      },
      'governance':{
        'read_only':True,'threshold_grid_predeclared':True,'threshold_selected_from_forward_results':False,'runtime_selector_change':False,'formal_weight_change':False,'current_rule_change':False,'automatic_promotion':False
      }
    }
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding='utf-8')
    print(json.dumps({'settled':len(rows),'all':payload['all_forward'],'lead':lead_bins,'frozen_066_060':frozen_066_060,'arm_a':arm_a},ensure_ascii=False,indent=2))
    return 0
if __name__=='__main__':raise SystemExit(main())
