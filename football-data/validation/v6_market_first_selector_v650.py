#!/usr/bin/env python3
"""V6.5.0 market-first selective architecture on the fixed 1700 sample.

Uses historical closing 1X2 as the primary direction wherever Football-Data covers the domain
(15/17 domains, 1500 fixed sampled matches). V6.0.1 is not blended into market probabilities;
it is only an optional agreement signal. K League 1 and UEFA Champions League are excluded
from historical market-selector fitting because this archive lacks compatible odds there.

Older-season 750 market-matched rows select rules. Newer-season 750 rows are untouched test.
Primary reliability rule maximizes validation coverage subject to Wilson90 lower >=65%.
Pre-registered arms:
 A: market non-draw, confidence threshold;
 B: A + V6 direction agreement;
 C: market home only;
 D: market away only.
No test-set tuning, no formal/runtime/CURRENT mutation.
"""
from __future__ import annotations
import json, math, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1];VALIDATION=ROOT/'validation'
if str(VALIDATION) not in sys.path:sys.path.insert(0,str(VALIDATION))
import v6_sampled_15domain_market_anchor_v647 as r1
import v6_sampled_15domain_market_anchor_v647_r2 as r2
CACHE=ROOT/'manifests'/'v6_sampled_17domain_pooled_scored_cache_v625_r4.json'
OUT=ROOT/'manifests'/'v6_market_first_selector_v650_status.json'
Z90=1.6448536269514722
CONF_GRID=tuple(i/100 for i in range(0,61))
MIN_SELECTED=60
TARGET_LB=.65

def load(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def wilson(h,n):
    if n<=0:return None
    p=h/n;z2=Z90*Z90;den=1+z2/n;ctr=p+z2/(2*n);spr=Z90*math.sqrt((p*(1-p)+z2/(4*n))/n);return (ctr-spr)/den

def align():
    cache=load(CACHE);rows=list(cache['rows']);bydom=defaultdict(list)
    for x in rows:bydom[str(x['competition_id'])].append(x)
    matches={}
    for cid,code in r1.MAIN.items():
        matches[cid]={};byseason=defaultdict(list)
        for x in bydom[cid]:byseason[str(x['season'])].append(x)
        for season,subset in byseason.items():
            sc=r1.season_code(season)
            if not sc:continue
            raw=r1.download(f'https://www.football-data.co.uk/mmz4281/{sc}/{code}.csv');got,_=r2.match_market(subset,raw);matches[cid].update(got)
    for cid,code in r1.EXTRA.items():
        raw=r1.download(f'https://www.football-data.co.uk/new/{code}.csv');got,_=r2.match_market(bydom[cid],raw);matches[cid]=got
    aligned=[]
    for cid,domain in sorted(bydom.items()):
        for row in domain:
            z=matches.get(cid,{}).get(str(row['identity']))
            if not z:continue
            q=z['q'];vals=sorted(((float(q[k]),k) for k in ('home','draw','away')),reverse=True);mpick=vals[0][1];conf=vals[0][0]-vals[1][0]
            aligned.append({
                'identity':row['identity'],'competition_id':cid,'role':row['role'],'truth':row['actual_result'],
                'market_q':q,'market_pick':mpick,'confidence':conf,'v6_pick':row['pick'],
                'market_v6_agree':mpick==row['pick'],
            })
    return aligned

def select(rows,arm,thr):
    out=[]
    for r in rows:
        if r['confidence']<thr or r['market_pick']=='draw':continue
        if arm=='B_agreement' and not r['market_v6_agree']:continue
        if arm=='C_home' and r['market_pick']!='home':continue
        if arm=='D_away' and r['market_pick']!='away':continue
        out.append(r)
    return out

def summary(rows,total):
    h=sum(r['market_pick']==r['truth'] for r in rows);by={}
    for d in ('home','away'):
        s=[r for r in rows if r['market_pick']==d];dh=sum(r['market_pick']==r['truth'] for r in s);by[d]={'count':len(s),'hits':dh,'accuracy':dh/len(s) if s else None,'wilson90_lower':wilson(dh,len(s))}
    return {'count':len(rows),'hits':h,'accuracy':h/len(rows) if rows else None,'wilson90_lower':wilson(h,len(rows)),'coverage':len(rows)/total if total else 0.0,'competitions_represented':len(set(r['competition_id'] for r in rows)),'by_direction':by}

def choose(rows,arm):
    cand=[]
    for thr in CONF_GRID:
        s=summary(select(rows,arm,thr),len(rows))
        if s['count']>=MIN_SELECTED and s['wilson90_lower'] is not None and s['wilson90_lower']>=TARGET_LB:cand.append({'threshold':thr,'validation':s})
    if not cand:return None
    cand.sort(key=lambda x:(-x['validation']['coverage'],-x['validation']['accuracy'],x['threshold']))
    return cand[0]

def main():
    rows=align();older=[r for r in rows if r['role']=='older'];newer=[r for r in rows if r['role']=='newer']
    arms={}
    for arm in ('A_market','B_agreement','C_home','D_away'):
        rule=choose(older,arm)
        arms[arm]={'selected_rule':rule,'newer_test':summary(select(newer,arm,rule['threshold']),len(newer)) if rule else None}
    # Pick a primary arm by validation coverage only; newer test never chooses the arm.
    valid=[(name,data) for name,data in arms.items() if data['selected_rule']]
    valid.sort(key=lambda item:(-item[1]['selected_rule']['validation']['coverage'],-item[1]['selected_rule']['validation']['accuracy'],item[0]))
    primary_name=valid[0][0] if valid else None
    primary=arms.get(primary_name) if primary_name else None
    payload={
        'schema_version':'V6.5.0-market-first-selector-r1',
        'generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        'status':'PASS' if primary else 'NO_WILSON65_RULE',
        'design':{
            'fixed_panel_total':1700,'historical_market_domains':15,'market_matched_total':len(rows),
            'older_selection_count':len(older),'newer_test_count':len(newer),
            'primary_source':'de-vigged historical closing 1X2','v6_role':'agreement diagnostic only, no probability blending',
            'selection_target':'maximum coverage with Wilson90 lower >=65%','minimum_selection':MIN_SELECTED,
            'newer_used_for_threshold_or_arm_selection':False,
        },
        'arms':arms,'primary_arm_selected_on_older_only':primary_name,'primary_newer_test':primary['newer_test'] if primary else None,
        'primary_newer_raw65_met':bool(primary and primary['newer_test'] and primary['newer_test']['accuracy'] is not None and primary['newer_test']['accuracy']>=.65),
        'primary_newer_wilson65_met':bool(primary and primary['newer_test'] and primary['newer_test']['wilson90_lower'] is not None and primary['newer_test']['wilson90_lower']>=.65),
        'governance':{
            'development_only':True,'historical_market_archive':True,'test_not_used_for_selection':True,
            'automatic_promotion':False,'fresh_forward_required':True,'formal_weight_change':False,'runtime_probability_change':False,'current_rule_change':False,
        },
    }
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(payload,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
