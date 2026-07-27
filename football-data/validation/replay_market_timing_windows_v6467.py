#!/usr/bin/env python3
"""V6.46.7 replay archived prospective Kambi snapshots by fixed pre-kickoff timing windows.

Purpose: test whether V6.5.1's earliest-eligible 1-72h freeze is the main source of the
historical/prospective gap. The windows are declared before this run: 1-6h, 1-12h,
1-24h, 24-48h, 48-72h. For each settled fixture and each window, choose the latest
(observed closest to kickoff) already-archived prospective snapshot inside that window.

This is retrospective timing diagnosis after outcomes exist. It cannot select a runtime
window or count as promotion evidence. Any chosen near-kickoff rule must start a new
post-freeze prospective epoch.
"""
from __future__ import annotations
import json, math, statistics, sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
VALIDATION=ROOT/'validation';ENGINE=ROOT/'engine'
for p in (VALIDATION,ENGINE):
    if str(p) not in sys.path:sys.path.insert(0,str(p))
import v6_market_first_forward_v651 as market
from platform_core import load_json, normalize_team_token, parse_iso_datetime

LEDGER=ROOT/'forward'/'v6_market_first_events_v651.json'
EVIDENCE=ROOT/'evidence'/'markets_prospective'
OUT=ROOT/'manifests'/'v6_market_timing_replay_v6467_status.json'
DIRECTIONS=('home','draw','away')
WINDOWS={'H1_6':(1.0,6.0),'H1_12':(1.0,12.0),'H1_24':(1.0,24.0),'H24_48':(24.0,48.0),'H48_72':(48.0,72.0)}


def now()->str:return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def ident(cid:str,kickoff:str,home:str,away:str)->tuple[str,str,str,str]:return (cid,kickoff,normalize_team_token(home),normalize_team_token(away))

def scores(rows:list[dict[str,Any]])->dict[str,Any]:
    n=len(rows)
    if not n:return {'count':0}
    hits=sum(r['pick']==r['actual'] for r in rows);ll=br=rps=0.0;eps=1e-15
    for r in rows:
        p=r['p'];y={d:1.0 if r['actual']==d else 0.0 for d in DIRECTIONS}
        ll-=math.log(max(eps,p[r['actual']]));br+=sum((p[d]-y[d])**2 for d in DIRECTIONS)
        rps+=((p['home']-y['home'])**2+((p['home']+p['draw'])-(y['home']+y['draw']))**2)/2
    return {'count':n,'hits':hits,'accuracy':hits/n,'log_loss':ll/n,'brier':br/n,'rps':rps/n,'mean_lead_hours':statistics.mean(r['lead'] for r in rows),'mean_pmax':statistics.mean(max(r['p'].values()) for r in rows)}

def settled_truth()->dict[tuple[str,str,str,str],dict[str,Any]]:
    ledger=load_json(LEDGER);pred={};sett={}
    for e in ledger.get('events',[]):
        if e.get('event_type')=='MARKET_PREDICTION_FROZEN':pred[str(e['match_id'])]=e
        elif e.get('event_type')=='RESULT_SETTLED':sett[str(e['match_id'])]=e
    out={}
    for mid,se in sett.items():
        pe=pred.get(mid)
        if not pe:continue
        fi=pe['payload']['fixture_identity'];res=se['payload']['result'];key=ident(str(fi['competition_id']),str(fi['kickoff_at']),str(fi['home_team']),str(fi['away_team']))
        out[key]={'actual':str(res['actual_result']),'match_id':mid,'competition_id':fi['competition_id']}
    return out

def main()->int:
    truth=settled_truth();snapshots:dict[tuple[str,str,str,str],list[dict[str,Any]]]=defaultdict(list);seen=0;matched=0
    for path in sorted(EVIDENCE.glob('*.json')) if EVIDENCE.exists() else []:
        seen+=1
        try:r=load_json(path);key=ident(str(r.get('competition_id') or ''),str(r.get('kickoff_utc') or ''),str(r.get('home_team') or ''),str(r.get('away_team') or ''))
        except Exception:continue
        if key not in truth or not isinstance(r.get('one_x_two'),dict):continue
        try:obs=parse_iso_datetime(str(r.get('source_observed_at_utc') or r.get('freeze_utc') or ''),'observed');ko=parse_iso_datetime(str(r.get('kickoff_utc') or ''),'kickoff')
        except Exception:continue
        lead=(ko-obs).total_seconds()/3600
        if lead<1 or lead>72:continue
        try:q=market.devig(r['one_x_two'])
        except Exception:continue
        pick,_=market.top_pick(q);snapshots[key].append({'observed':obs,'lead':lead,'p':q,'pick':pick,'path':str(path.relative_to(ROOT))});matched+=1
    results={}
    for name,(lo,hi) in WINDOWS.items():
        rows=[];by_comp=defaultdict(list)
        for key,t in truth.items():
            cand=[s for s in snapshots.get(key,[]) if lo<=s['lead']<=hi]
            if not cand:continue
            chosen=min(cand,key=lambda s:(s['lead'],s['observed'])) # closest pre-kickoff snapshot
            row={**chosen,'actual':t['actual'],'competition_id':t['competition_id'],'match_id':t['match_id']};rows.append(row);by_comp[str(t['competition_id'])].append(row)
        results[name]={'window_hours':[lo,hi],'selection':'latest_archived_snapshot_closest_to_kickoff','metrics':scores(rows),'coverage_of_61_settled':len(rows)/len(truth) if truth else 0.0,'by_competition':{c:scores(v) for c,v in sorted(by_comp.items())}}
    # Also replay the original earliest eligible 1-72 rule from archive as a consistency check.
    original=[]
    for key,t in truth.items():
        cand=[s for s in snapshots.get(key,[]) if 1<=s['lead']<=72]
        if not cand:continue
        chosen=max(cand,key=lambda s:(s['lead'], -s['observed'].timestamp())) # earliest observed = largest lead
        original.append({**chosen,'actual':t['actual'],'competition_id':t['competition_id'],'match_id':t['match_id']})
    payload={'schema_version':'V6.46.7-market-timing-replay-r1','generated_at_utc':now(),'status':'PASS_DIAGNOSTIC','settled_fixture_count':len(truth),'evidence_files_seen':seen,'matched_snapshot_records':matched,'original_earliest_1_72_replay':scores(original),'fixed_window_replays':results,'interpretation_guardrails':{'post_outcome_replay_only':True,'cannot_select_runtime_window':True,'cannot_promote':True,'new_window_requires_new_postfreeze_epoch':True,'windows_predeclared_before_run':list(WINDOWS)},'governance':{'runtime_probability_change':False,'formal_weight_change':False,'current_rule_change':False}}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding='utf-8')
    print(json.dumps({'settled':len(truth),'original':payload['original_earliest_1_72_replay'],'windows':{k:v['metrics'] for k,v in results.items()}},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
