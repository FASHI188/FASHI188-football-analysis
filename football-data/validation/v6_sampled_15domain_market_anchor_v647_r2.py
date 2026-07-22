#!/usr/bin/env python3
"""V6.4.7 r2: adapt Football-Data extra-league Home/Away field names; logic unchanged."""
from __future__ import annotations
import difflib, json, sys
from collections import Counter, defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];VALIDATION=ROOT/'validation'
if str(VALIDATION) not in sys.path:sys.path.insert(0,str(VALIDATION))
import v6_sampled_15domain_market_anchor_v647 as r1
from platform_core import normalize_team_token
OUT=ROOT/'manifests'/'v6_sampled_15domain_market_anchor_v647_r2_status.json'

def team_field(row,side):
    return row.get('HomeTeam') or row.get('Home') if side=='home' else row.get('AwayTeam') or row.get('Away')
def match_market(panel,raw_rows):
    by=defaultdict(list)
    for x in raw_rows:
        d=r1.parse_date(x.get('Date'))
        if d:by[d].append(x)
    out={};stats=Counter()
    for r in panel:
        cand=by.get(str(r['date']),[])
        if not cand:stats['date_unmatched']+=1;continue
        h=str(r['home_team']);a=str(r['away_team'])
        exact=[x for x in cand if r1.same(team_field(x,'home'),h) and r1.same(team_field(x,'away'),a)]
        chosen=exact[0] if len(exact)==1 else None
        if chosen is None:
            ranked=[]
            for x in cand:
                hs=difflib.SequenceMatcher(None,normalize_team_token(str(team_field(x,'home'))),normalize_team_token(h)).ratio();as_=difflib.SequenceMatcher(None,normalize_team_token(str(team_field(x,'away'))),normalize_team_token(a)).ratio();ranked.append(((hs+as_)/2,x))
            ranked.sort(key=lambda z:z[0],reverse=True)
            if ranked and ranked[0][0]>=.82 and (len(ranked)==1 or ranked[0][0]-ranked[1][0]>=.08):chosen=ranked[0][1];stats['fuzzy']+=1
        if chosen is None:stats['identity_unmatched']+=1;continue
        q,fam=r1.market(chosen)
        if q is None:stats['missing_complete_1x2']+=1;continue
        out[str(r['identity'])]={'q':q,'family':fam};stats['matched']+=1
    return out,dict(stats)

def main():
    r1.OUT=OUT;r1.match_market=match_market
    code=r1.main()
    if OUT.exists():
        x=json.loads(OUT.read_text(encoding='utf-8'));x['schema_version']='V6.4.7-sampled-15domain-market-anchor-r2-extra-schema';x.setdefault('governance',{})['r1_logic_unchanged']=True;x['governance']['extra_schema_fix_only']=True;OUT.write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding='utf-8')
    return code
if __name__=='__main__':raise SystemExit(main())
