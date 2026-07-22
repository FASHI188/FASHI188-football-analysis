#!/usr/bin/env python3
"""V6.4.7: align the fixed 1700 sampled panel with historical bookmaker 1X2 prices.

15/17 project domains have Football-Data historical odds coverage. Main European leagues use
season CSVs; extra leagues use Football-Data's all-seasons-in-one CSV files. K League 1 and
UEFA Champions League remain untouched V6 fallbacks because this source does not provide a
compatible historical league archive.

No model fitting. This is a source/value audit answering whether the primary 17-domain baseline
should be market-anchored wherever a verifiable historical market exists.
"""
from __future__ import annotations
import csv, difflib, io, json, math, sys, urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1];VALIDATION=ROOT/'validation'
if str(VALIDATION) not in sys.path:sys.path.insert(0,str(VALIDATION))
import v6_market_residual_fusion_v620 as mkt
from platform_core import normalize_team_token
CACHE=ROOT/'manifests'/'v6_sampled_17domain_pooled_scored_cache_v625_r4.json'
OUT=ROOT/'manifests'/'v6_sampled_15domain_market_anchor_v647_status.json'
MAIN={'ENG_PremierLeague':'E0','ESP_LaLiga':'SP1','GER_Bundesliga':'D1','ITA_SerieA':'I1','FRA_Ligue1':'F1','NED_Eredivisie':'N1','POR_PrimeiraLiga':'P1','SCO_Premiership':'SC0'}
EXTRA={'ARG_Primera':'ARG','BRA_SerieA':'BRA','JPN_J1':'JPN','NOR_Eliteserien':'NOR','SWE_Allsvenskan':'SWE','SUI_SuperLeague':'SWZ','USA_MLS':'USA'}
UNAVAILABLE={'KOR_KLeague1','UEFA_ChampionsLeague'}
EPS=1e-12

def load(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def download(url):
    req=urllib.request.Request(url,headers={'User-Agent':'football-v6.4-market-audit/1.0'})
    with urllib.request.urlopen(req,timeout=60) as r:raw=r.read()
    text=raw.decode('utf-8-sig',errors='replace');return list(csv.DictReader(io.StringIO(text)))
def parse_date(v):
    v=str(v or '').strip()
    for fmt in ('%d/%m/%Y','%d/%m/%y','%Y-%m-%d'):
        try:return datetime.strptime(v,fmt).date().isoformat()
        except:pass
    return None
def same(a,b):return normalize_team_token(str(a))==normalize_team_token(str(b))
def season_code(season):
    s=str(season)
    if '/' in s:
        a,b=s.split('/');return a[-2:]+b[-2:]
    return None
def market(raw):
    q,fam=mkt._closing_market(raw)
    return q,fam
def baseline_pick(r):return str(r['pick'])
def top(q):return max(('home','draw','away'),key=lambda k:float(q[k]))
def match_market(panel,raw_rows):
    by=defaultdict(list)
    for x in raw_rows:
        d=parse_date(x.get('Date'))
        if d:by[d].append(x)
    out={};stats=Counter()
    for r in panel:
        cand=by.get(str(r['date']),[])
        if not cand:stats['date_unmatched']+=1;continue
        h=str(r['home_team']);a=str(r['away_team'])
        exact=[x for x in cand if same(x.get('HomeTeam'),h) and same(x.get('AwayTeam'),a)]
        chosen=exact[0] if len(exact)==1 else None
        if chosen is None:
            ranked=[]
            for x in cand:
                hs=difflib.SequenceMatcher(None,normalize_team_token(str(x.get('HomeTeam'))),normalize_team_token(h)).ratio();as_=difflib.SequenceMatcher(None,normalize_team_token(str(x.get('AwayTeam'))),normalize_team_token(a)).ratio();ranked.append(((hs+as_)/2,x))
            ranked.sort(key=lambda z:z[0],reverse=True)
            if ranked and ranked[0][0]>=.82 and (len(ranked)==1 or ranked[0][0]-ranked[1][0]>=.08):chosen=ranked[0][1];stats['fuzzy']+=1
        if chosen is None:stats['identity_unmatched']+=1;continue
        q,fam=market(chosen)
        if q is None:stats['missing_complete_1x2']+=1;continue
        out[str(r['identity'])]={'q':q,'family':fam};stats['matched']+=1
    return out,dict(stats)
def metrics(rows,pick_fn):
    n=len(rows);h=sum(pick_fn(r)==r['actual_result'] for r in rows);return {'count':n,'hits':h,'accuracy':h/n if n else None,'predicted':dict(Counter(pick_fn(r) for r in rows))}
def main():
    cache=load(CACHE);rows=list(cache['rows']);bydom=defaultdict(list)
    for r in rows:bydom[str(r['competition_id'])].append(r)
    matches={};sources={}
    # main season-by-season archives
    for cid,code in MAIN.items():
        domain=bydom[cid];matches[cid]={};sources[cid]={}
        byseason=defaultdict(list)
        for r in domain:byseason[str(r['season'])].append(r)
        for season,subset in byseason.items():
            sc=season_code(season)
            if not sc:sources[cid][season]={'status':'unsupported_season'};continue
            url=f'https://www.football-data.co.uk/mmz4281/{sc}/{code}.csv'
            try:
                raw=download(url);got,st=match_market(subset,raw);matches[cid].update(got);sources[cid][season]={'url':url,'csv_rows':len(raw),'stats':st}
            except Exception as e:sources[cid][season]={'url':url,'error':f'{type(e).__name__}: {e}'}
    # extra leagues: all seasons in one file
    for cid,code in EXTRA.items():
        domain=bydom[cid];url=f'https://www.football-data.co.uk/new/{code}.csv';matches[cid]={}
        try:
            raw=download(url);got,st=match_market(domain,raw);matches[cid].update(got);sources[cid]={'url':url,'csv_rows':len(raw),'stats':st}
        except Exception as e:sources[cid]={'url':url,'error':f'{type(e).__name__}: {e}'}
    market_rows=[];hybrid=[];by_domain={}
    for cid,domain in sorted(bydom.items()):
        m=matches.get(cid,{})
        aligned=[]
        for r in domain:
            z=m.get(str(r['identity']))
            x=dict(r)
            if z:x['market_q']=z['q'];x['market_pick']=top(z['q']);aligned.append(x);market_rows.append(x)
            x['hybrid_pick']=x.get('market_pick',baseline_pick(r));hybrid.append(x)
        base=metrics(domain,lambda r:baseline_pick(r));marketm=metrics(aligned,lambda r:r['market_pick']) if aligned else {'count':0,'hits':0,'accuracy':None,'predicted':{}}
        hybridm=metrics([x for x in hybrid if x['competition_id']==cid],lambda r:r['hybrid_pick'])
        by_domain[cid]={'source_available':cid not in UNAVAILABLE,'sample_count':len(domain),'market_matched':len(aligned),'baseline':base,'market_on_matched':marketm,'hybrid':hybridm}
    base_all=metrics(rows,lambda r:baseline_pick(r));market_all=metrics(market_rows,lambda r:r['market_pick']);base_same=metrics(market_rows,lambda r:baseline_pick(r));hybrid_all=metrics(hybrid,lambda r:r['hybrid_pick'])
    old=[r for r in hybrid if r['role']=='older'];new=[r for r in hybrid if r['role']=='newer']
    out={'schema_version':'V6.4.7-sampled-15domain-market-anchor-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','coverage':{'project_domains':17,'market_source_domains':15,'unavailable_domains':sorted(UNAVAILABLE),'fixed_sample_count':len(rows),'market_matched_count':len(market_rows),'market_match_rate':len(market_rows)/len(rows)},'overall':{'v601_all_1700':base_all,'v601_on_market_matched':base_same,'market_on_same_matched':market_all,'hybrid_market_else_v601_1700':hybrid_all,'hybrid_older_850':metrics(old,lambda r:r['hybrid_pick']),'hybrid_newer_850':metrics(new,lambda r:r['hybrid_pick']),'market_gain_pp_on_same_rows':100*(market_all['accuracy']-base_same['accuracy']) if market_all['accuracy'] is not None else None,'hybrid_gain_pp_all_1700':100*(hybrid_all['accuracy']-base_all['accuracy'])},'by_domain':by_domain,'source_audit':sources,'governance':{'sample_identities_changed':False,'no_model_fitting':True,'market_observation_is_historical_archive':True,'not_pristine_promotion_evidence':True,'formal_weight_change':False,'runtime_probability_change':False,'current_rule_change':False}}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
