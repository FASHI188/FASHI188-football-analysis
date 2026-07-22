#!/usr/bin/env python3
"""V6.5.4 fresh disjoint confirmation for the frozen V6.5.0 market rule.

Builds a SECOND outcome-blind fixed panel from the same two evaluation seasons:
15 market-covered domains x 2 seasons x 50 matches = 1500 matches, explicitly excluding all
V6.2.5-r4 identities used to derive V6.5.0.

The V6.5.0 primary rule is applied unchanged:
- de-vigged historical closing 1X2 top1;
- draws excluded;
- confidence >= 0.35.
No threshold/domain/arm tuning is permitted on this confirmation panel.
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
ENGINE=ROOT/'engine';VALIDATION=ROOT/'validation'
for p in (ENGINE,VALIDATION):
    if str(p) not in sys.path:sys.path.insert(0,str(p))

import v6_sampled_15domain_market_anchor_v647 as a1
import v6_sampled_15domain_market_anchor_v647_r2 as a2
from platform_core import read_processed_matches

OLD=ROOT/'manifests'/'v6_sampled_17domain_pooled_scored_cache_v625_r4.json'
RULE=ROOT/'manifests'/'v6_market_first_selector_v650_status.json'
OUT=ROOT/'manifests'/'v6_market_first_fresh_confirmation_v654_status.json'
PANEL=ROOT/'manifests'/'v6_market_first_fresh_confirmation_v654_panel.json'
SEED='V6.5.4-market-first-disjoint-15domain-v1'
THRESHOLD=.35
N=50
Z90=1.6448536269514722


def load(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def identity(cid,season,date,home,away):return f'{cid}|{season}|{date}|{home}|{away}'
def hashkey(s):return hashlib.sha256((SEED+'|'+s).encode()).hexdigest()
def actual(hg,ag):return 'home' if hg>ag else 'away' if hg<ag else 'draw'
def top(q):
    vals=sorted(((float(q[k]),k) for k in ('home','draw','away')),reverse=True);return vals[0][1],vals[0][0]-vals[1][0]
def wilson(h,n):
    import math
    if not n:return None
    p=h/n;z2=Z90*Z90;den=1+z2/n;ctr=p+z2/(2*n);spr=Z90*math.sqrt((p*(1-p)+z2/(4*n))/n);return (ctr-spr)/den

def build_processed(cid,season):
    out=[]
    for m in read_processed_matches(cid):
        if str(m.season)!=str(season):continue
        ident=identity(cid,season,m.date.date().isoformat(),m.home_team,m.away_team)
        out.append({'identity':ident,'competition_id':cid,'season':str(season),'date':m.date.date().isoformat(),'home_team':m.home_team,'away_team':m.away_team,'actual_result':actual(int(m.home_goals),int(m.away_goals))})
    return out

def source_rows(cid,code,season,rows):
    if cid in a1.MAIN:
        sc=a1.season_code(season);raw=a1.download(f'https://www.football-data.co.uk/mmz4281/{sc}/{code}.csv')
    else:
        raw=a1.download(f'https://www.football-data.co.uk/new/{code}.csv')
    got,stats=a2.match_market(rows,raw);return got,stats

def summary(rows):
    selected=[r for r in rows if r['market_pick']!='draw' and r['confidence']>=THRESHOLD];h=sum(r['market_pick']==r['actual_result'] for r in selected);dirs={}
    for d in ('home','away'):
        s=[r for r in selected if r['market_pick']==d];dh=sum(r['market_pick']==r['actual_result'] for r in s);dirs[d]={'count':len(s),'hits':dh,'accuracy':dh/len(s) if s else None,'wilson90_lower':wilson(dh,len(s))}
    return {'total_count':len(rows),'selected_count':len(selected),'hits':h,'accuracy':h/len(selected) if selected else None,'wilson90_lower':wilson(h,len(selected)),'coverage':len(selected)/len(rows) if rows else 0.0,'competitions_represented':len(set(r['competition_id'] for r in selected)),'by_direction':dirs}

def main():
    old=load(OLD);rule=load(RULE)
    if abs(float(rule['arms']['A_market']['selected_rule']['threshold'])-THRESHOLD)>1e-12:raise RuntimeError('V6.5.0 threshold drift')
    excluded={str(r['identity']) for r in old['rows']}
    roles=defaultdict(dict)
    for r in old['rows']:
        roles[str(r['competition_id'])][str(r['role'])]=str(r['season'])
    codes=dict(a1.MAIN);codes.update(a1.EXTRA)
    panel=[];audit={}
    for cid,code in sorted(codes.items()):
        audit[cid]={}
        for role in ('older','newer'):
            season=roles[cid][role];proc=[r for r in build_processed(cid,season) if r['identity'] not in excluded];matched,stats=source_rows(cid,code,season,proc)
            candidates=[]
            for r in proc:
                z=matched.get(r['identity'])
                if not z:continue
                q=z['q'];p,c=top(q);x=dict(r);x.update({'role':role,'market_q':q,'market_pick':p,'confidence':c});candidates.append(x)
            candidates.sort(key=lambda r:(hashkey(r['identity']),r['identity']))
            chosen=candidates[:N]
            if len(chosen)!=N:raise RuntimeError(f'{cid} {season}: only {len(chosen)} disjoint market rows')
            panel+=chosen;audit[cid][role]={'season':season,'processed_disjoint':len(proc),'market_matched_disjoint':len(candidates),'sampled':len(chosen),'match_stats':stats}
    if len(panel)!=1500 or len({r['identity'] for r in panel})!=1500:raise RuntimeError('fresh panel identity/count failure')
    if any(r['identity'] in excluded for r in panel):raise RuntimeError('old panel overlap')
    older=[r for r in panel if r['role']=='older'];newer=[r for r in panel if r['role']=='newer']
    payload={'schema_version':'V6.5.4-market-first-fresh-confirmation-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','pre_registered_design':{'domains':15,'sample_per_season':50,'fresh_total':1500,'old_identity_exclusion_count':len(excluded),'overlap_allowed':False,'sample_seed':SEED,'sample_outcome_blind':True,'frozen_threshold':THRESHOLD,'frozen_rule':'market top1 non-draw and confidence>=0.35','confirmation_parameters_tunable':False},'fresh_older_750_secondary':summary(older),'fresh_newer_750_primary':summary(newer),'combined_1500':summary(panel),'primary_raw65_met':bool(summary(newer)['accuracy'] is not None and summary(newer)['accuracy']>=.65),'primary_wilson65_met':bool(summary(newer)['wilson90_lower'] is not None and summary(newer)['wilson90_lower']>=.65),'sample_audit':audit,'governance':{'fresh_disjoint_historical_confirmation':True,'confirmation_not_used_for_tuning':True,'not_pristine_future_evidence':True,'automatic_promotion':False,'formal_weight_change':False,'runtime_probability_change':False,'current_rule_change':False}}
    PANEL.write_text(json.dumps({'schema_version':'V6.5.4-market-first-fresh-panel-r1','seed':SEED,'count':len(panel),'rows':panel},ensure_ascii=False,indent=2),encoding='utf-8');OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(payload,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
