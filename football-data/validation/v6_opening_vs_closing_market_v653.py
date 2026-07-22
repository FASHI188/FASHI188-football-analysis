#!/usr/bin/env python3
"""V6.5.3 opening-vs-closing market audit on fixed sampled identities.

Football-Data documents non-C odds since 2019/20 as the first set collected after market
opening and C-suffixed odds as closing. This experiment measures how much of V6.5.0's market
advantage is already present earlier versus arriving near close.

Scope: 8 main European domains in the fixed V6.2.5-r4 panel, 50 older + 50 newer per domain
= 800 matches maximum. No model fitting. Older400 independently selects Wilson90>=65%
confidence thresholds for opening and closing; newer400 tests them unchanged.
"""
from __future__ import annotations

import csv, difflib, io, json, math, sys, urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
VALIDATION=ROOT/'validation'
if str(VALIDATION) not in sys.path: sys.path.insert(0,str(VALIDATION))
import v6_sampled_15domain_market_anchor_v647 as anchor
from platform_core import normalize_team_token

CACHE=ROOT/'manifests'/'v6_sampled_17domain_pooled_scored_cache_v625_r4.json'
OUT=ROOT/'manifests'/'v6_opening_vs_closing_market_v653_status.json'
DOMAINS=anchor.MAIN
Z90=1.6448536269514722
CONF_GRID=tuple(i/100 for i in range(0,61))
MIN_SELECTED=40


def load(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def f(row,key):
    try:x=float(str(row.get(key) or '').strip())
    except:return None
    return x if math.isfinite(x) and x>1 else None

def devig_odds(vals):
    inv=[1/x for x in vals];s=sum(inv);return {'home':inv[0]/s,'draw':inv[1]/s,'away':inv[2]/s}
def opening(row):
    for keys,label in ((('AvgH','AvgD','AvgA'),'average_opening'),(('B365H','B365D','B365A'),'bet365_opening'),(('MaxH','MaxD','MaxA'),'maximum_opening')):
        vals=[f(row,k) for k in keys]
        if all(v is not None for v in vals): return devig_odds(vals),label
    return None,None
def closing(row):
    for keys,label in ((('AvgCH','AvgCD','AvgCA'),'average_closing'),(('B365CH','B365CD','B365CA'),'bet365_closing'),(('MaxCH','MaxCD','MaxCA'),'maximum_closing')):
        vals=[f(row,k) for k in keys]
        if all(v is not None for v in vals): return devig_odds(vals),label
    return None,None
def same(a,b): return normalize_team_token(str(a))==normalize_team_token(str(b))
def pick(q): return max(('home','draw','away'),key=lambda k:float(q[k]))
def conf(q):
    v=sorted((float(q[k]) for k in ('home','draw','away')),reverse=True);return v[0]-v[1]
def wilson(h,n):
    if not n:return None
    p=h/n;z2=Z90*Z90;den=1+z2/n;ctr=p+z2/(2*n);spr=Z90*math.sqrt((p*(1-p)+z2/(4*n))/n);return (ctr-spr)/den

def download(url):
    req=urllib.request.Request(url,headers={'User-Agent':'football-v6.5-market-timing/1.0'})
    with urllib.request.urlopen(req,timeout=60) as r:txt=r.read().decode('utf-8-sig',errors='replace')
    return list(csv.DictReader(io.StringIO(txt)))

def align(panel,raws):
    by=defaultdict(list)
    for x in raws:
        d=anchor.parse_date(x.get('Date'))
        if d:by[d].append(x)
    out=[];stats=Counter()
    for r in panel:
        cand=by.get(str(r['date']),[])
        if not cand:stats['date_unmatched']+=1;continue
        exact=[x for x in cand if same(x.get('HomeTeam'),r['home_team']) and same(x.get('AwayTeam'),r['away_team'])]
        chosen=exact[0] if len(exact)==1 else None
        if chosen is None:
            ranked=[]
            for x in cand:
                hs=difflib.SequenceMatcher(None,normalize_team_token(str(x.get('HomeTeam'))),normalize_team_token(str(r['home_team']))).ratio();as_=difflib.SequenceMatcher(None,normalize_team_token(str(x.get('AwayTeam'))),normalize_team_token(str(r['away_team']))).ratio();ranked.append(((hs+as_)/2,x))
            ranked.sort(key=lambda z:z[0],reverse=True)
            if ranked and ranked[0][0]>=.82 and (len(ranked)==1 or ranked[0][0]-ranked[1][0]>=.08):chosen=ranked[0][1];stats['fuzzy']+=1
        if chosen is None:stats['identity_unmatched']+=1;continue
        qo,of=opening(chosen);qc,cf=closing(chosen)
        if qo is None or qc is None:stats['missing_open_or_close']+=1;continue
        x=dict(r);x['opening_q']=qo;x['closing_q']=qc;x['opening_family']=of;x['closing_family']=cf;out.append(x);stats['matched']+=1
    return out,dict(stats)

def metrics(rows,key):
    h=sum(pick(r[key])==r['actual_result'] for r in rows);return {'count':len(rows),'hits':h,'accuracy':h/len(rows) if rows else None,'predicted':dict(Counter(pick(r[key]) for r in rows))}
def v6metrics(rows):
    h=sum(r['pick']==r['actual_result'] for r in rows);return {'count':len(rows),'hits':h,'accuracy':h/len(rows) if rows else None,'predicted':dict(Counter(r['pick'] for r in rows))}
def selected(rows,key,thr): return [r for r in rows if pick(r[key])!='draw' and conf(r[key])>=thr]
def summary(rows,key,total):
    h=sum(pick(r[key])==r['actual_result'] for r in rows);return {'count':len(rows),'hits':h,'accuracy':h/len(rows) if rows else None,'wilson90_lower':wilson(h,len(rows)),'coverage':len(rows)/total if total else 0.0,'competitions_represented':len(set(r['competition_id'] for r in rows))}
def choose(rows,key):
    cand=[]
    for t in CONF_GRID:
        s=summary(selected(rows,key,t),key,len(rows))
        if s['count']>=MIN_SELECTED and s['wilson90_lower'] is not None and s['wilson90_lower']>=.65:cand.append({'threshold':t,'validation':s})
    if not cand:return None
    cand.sort(key=lambda x:(-x['validation']['coverage'],-x['validation']['accuracy'],x['threshold']));return cand[0]

def main():
    cache=load(CACHE);allrows=cache['rows'];by=defaultdict(list)
    for r in allrows:
        if r['competition_id'] in DOMAINS:by[r['competition_id']].append(r)
    rows=[];audit={}
    for cid,code in DOMAINS.items():
        audit[cid]={};seasons=defaultdict(list)
        for r in by[cid]:seasons[str(r['season'])].append(r)
        for season,panel in seasons.items():
            sc=anchor.season_code(season);url=f'https://www.football-data.co.uk/mmz4281/{sc}/{code}.csv';raw=download(url);got,st=align(panel,raw);rows+=got;audit[cid][season]={'url':url,'csv_rows':len(raw),'stats':st}
    older=[r for r in rows if r['role']=='older'];newer=[r for r in rows if r['role']=='newer']
    open_rule=choose(older,'opening_q');close_rule=choose(older,'closing_q')
    out={'schema_version':'V6.5.3-opening-vs-closing-market-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','design':{'domains':len(DOMAINS),'matched_total':len(rows),'older_selection_count':len(older),'newer_test_count':len(newer),'opening_semantics':'first odds set collected after market opening per Football-Data documentation','closing_semantics':'C-suffixed closing odds','newer_used_for_selection':False},'older':{'v6':v6metrics(older),'opening':metrics(older,'opening_q'),'closing':metrics(older,'closing_q')},'newer':{'v6':v6metrics(newer),'opening':metrics(newer,'opening_q'),'closing':metrics(newer,'closing_q')},'opening_gain_pp_vs_v6_newer':100*(metrics(newer,'opening_q')['accuracy']-v6metrics(newer)['accuracy']),'closing_gain_pp_vs_v6_newer':100*(metrics(newer,'closing_q')['accuracy']-v6metrics(newer)['accuracy']),'closing_gain_pp_vs_opening_newer':100*(metrics(newer,'closing_q')['accuracy']-metrics(newer,'opening_q')['accuracy']),'opening_selector_rule':open_rule,'opening_selector_newer':summary(selected(newer,'opening_q',open_rule['threshold']),'opening_q',len(newer)) if open_rule else None,'closing_selector_rule':close_rule,'closing_selector_newer':summary(selected(newer,'closing_q',close_rule['threshold']),'closing_q',len(newer)) if close_rule else None,'source_audit':audit,'governance':{'market_timing_audit_only':True,'test_used_for_selection':False,'automatic_promotion':False,'formal_weight_change':False,'runtime_probability_change':False,'current_rule_change':False}}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
