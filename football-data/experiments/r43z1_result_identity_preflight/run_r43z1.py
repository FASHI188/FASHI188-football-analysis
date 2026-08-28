#!/usr/bin/env python3
from __future__ import annotations

import json, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
VALID=ROOT/'validation'
ENGINE=ROOT/'engine'
for p in (VALID,ENGINE):
    if str(p) not in sys.path:sys.path.insert(0,str(p))
import v6_pristine_forward_result_resolver_v612 as common
import v6_market_first_result_resolver_v651 as market_resolver
from platform_core import parse_iso_datetime

U1=ROOT/'forward'/'r43u1_pristine_forward_events.json'
OUT=ROOT/'experiments'/'r43z1_result_identity_preflight'/'results'/'summary_r43z1_result_identity_preflight.json'
N=41

def load(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def now():return datetime.now(timezone.utc).replace(microsecond=0)

def names(row):
    return common.competitor_names(row)

def kleague_identity_only(fx,kickoff,cache):
    home_expected=market_resolver._expected_kleague_short(fx.get('home_team'))
    away_expected=market_resolver._expected_kleague_short(fx.get('away_team'))
    if not home_expected or not away_expected:
        return [],{'reason':'kleague_team_alias_unmapped','mapped_home_short':home_expected,'mapped_away_short':away_expected}
    local=kickoff.astimezone(market_resolver.SEOUL);date_token=local.strftime('%Y/%m/%d')
    try:
        rows,urls=market_resolver._kleague_year_rows(local.year,cache)
    except Exception as exc:
        return [],{'reason':'kleague_official_fetch_error','error':f'{type(exc).__name__}: {exc}','date_token':date_token}
    matches=[]
    for row in rows:
        if str(row.get('gameDate') or '')!=date_token:continue
        home=str(row.get('homeTeamName') or '');away=str(row.get('awayTeamName') or '')
        hk=market_resolver._ko_team_key(home);ak=market_resolver._ko_team_key(away)
        if market_resolver._ko_team_key(home_expected) not in hk and hk not in market_resolver._ko_team_key(home_expected):continue
        if market_resolver._ko_team_key(away_expected) not in ak and ak not in market_resolver._ko_team_key(away_expected):continue
        matches.append({'game_date':date_token,'official_home_team':home,'official_away_team':away,'round_id':row.get('roundId'),'field_name':row.get('fieldNameFull'),'score_fields_read':False,'result_fields_read':False,'source_urls':urls})
    uniq={(x['game_date'],x['official_home_team'],x['official_away_team'],str(x.get('round_id') or '')):x for x in matches}
    return list(uniq.values()),{'date_token':date_token,'official_rows_fetched':len(rows),'mapped_home_short':home_expected,'mapped_away_short':away_expected,'official_api_urls':urls}

def run():
    t=now();events=[e for e in load(U1).get('events',[]) if e.get('event_type')=='PREDICTION_FROZEN'][:N]
    if len(events)!=N:raise RuntimeError(f'need sealed41, got {len(events)}')
    cache={};kcache={};rows=[]
    for e in events:
        p=e.get('payload') or {};fx=p.get('fixture_identity') or {};cid=str(fx.get('competition_id') or '')
        kickoff=parse_iso_datetime(str(fx.get('kickoff_at') or ''),'kickoff_at')
        if t>=kickoff:
            rows.append({'match_id':e.get('match_id'),'competition_id':cid,'home_team':fx.get('home_team'),'away_team':fx.get('away_team'),'kickoff_at':fx.get('kickoff_at'),'status':'SKIP_ALREADY_STARTED_FOR_ZERO_LABEL_PREFLIGHT','candidate_rows':[]})
            continue
        if cid not in common.DOMAINS:
            rows.append({'match_id':e.get('match_id'),'competition_id':cid,'home_team':fx.get('home_team'),'away_team':fx.get('away_team'),'kickoff_at':fx.get('kickoff_at'),'status':'DOMAIN_UNMAPPED','candidate_rows':[]})
            continue
        exact=[];near=[]
        for date_token,payload,url in common.fetch_scoreboards(cid,kickoff,cache):
            for raw in payload.get('events') or []:
                if not isinstance(raw,dict):continue
                try:ek=parse_iso_datetime(str(raw.get('date') or ''),'espn_event_date')
                except Exception:continue
                if abs(ek-kickoff)>common.KICKOFF_TOLERANCE:continue
                comps=raw.get('competitions') or []
                if not isinstance(comps,list) or not comps or not isinstance(comps[0],dict):continue
                comp=comps[0];cs=comp.get('competitors') or []
                if not isinstance(cs,list):continue
                h=next((r for r in cs if isinstance(r,dict) and r.get('homeAway')=='home'),None)
                a=next((r for r in cs if isinstance(r,dict) and r.get('homeAway')=='away'),None)
                if not isinstance(h,dict) or not isinstance(a,dict):continue
                rec={'espn_event_id':str(raw.get('id') or ''),'event_kickoff_at':ek.isoformat(),'kickoff_difference_seconds':abs((ek-kickoff).total_seconds()),'espn_home_names':names(h),'espn_away_names':names(a),'score_fields_read':False,'result_fields_read':False,'source_url':url,'date_token':date_token}
                near.append(rec)
                if common.team_matches(cid,h,str(fx.get('home_team') or '')) and common.team_matches(cid,a,str(fx.get('away_team') or '')):
                    exact.append(rec)
        uniq={ (x['espn_event_id'],x['event_kickoff_at']):x for x in exact }
        official=[];official_audit={}
        if len(uniq)==0 and cid=='KOR_KLeague1':
            official,official_audit=kleague_identity_only(fx,kickoff,kcache)
        if len(uniq)==1:status='IDENTITY_RESOLVES_ESPN_PREMATCH'
        elif len(uniq)>1:status='IDENTITY_AMBIGUOUS_ESPN_PREMATCH'
        elif len(official)==1:status='IDENTITY_RESOLVES_KLEAGUE_OFFICIAL_PREMATCH'
        elif len(official)>1:status='IDENTITY_AMBIGUOUS_KLEAGUE_OFFICIAL_PREMATCH'
        else:status='IDENTITY_NOT_FOUND_PREMATCH'
        rows.append({'match_id':e.get('match_id'),'competition_id':cid,'home_team':fx.get('home_team'),'away_team':fx.get('away_team'),'kickoff_at':fx.get('kickoff_at'),'status':status,'espn_exact_match_count':len(uniq),'espn_exact_matches':list(uniq.values()),'espn_candidate_rows':near,'kleague_official_exact_match_count':len(official),'kleague_official_exact_matches':official,'kleague_official_audit':official_audit})
    counts={}
    for r in rows:counts[r['status']]=counts.get(r['status'],0)+1
    resolved=sum(v for k,v in counts.items() if k.startswith('IDENTITY_RESOLVES_'))
    out={'schema_version':'football3-r43z1-zero-label-result-identity-preflight-v2','status':'COMPLETE','classification':'PRE_OUTCOME_RESULT_IDENTITY_ONLY_AUDIT','formal_weight':0,'generated_at_utc':t.isoformat(),'governance':{'sealed_prediction_fixture_identities_only':True,'outcome_access':False,'score_fields_read':False,'result_fields_read':False,'probability_change':False,'prediction_change':False,'alias_change':False,'fuzzy_matching':False,'espn_primary':True,'kleague_official_identity_fallback_checked_without_scores':True,'main_merge':False,'publication':False},'coverage':{'sealed41':len(events),'rows':len(rows),'resolved_by_existing_result_chain':resolved,'unresolved_by_existing_result_chain':len(rows)-resolved,'status_counts':counts},'rows':rows,'action':'RESULT_IDENTITY_CHAIN_PREFLIGHT_COMPLETE_NO_ALIAS_CHANGE' if resolved==len(rows) else 'ADD_ONLY_EXACT_PREMATCH_RESOLVER_ALIASES_FOR_UNIQUE_IDENTITY_FAILURES_THEN_RERUN_PREFLIGHT'}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':out['status'],'generated_at_utc':out['generated_at_utc'],'coverage':out['coverage'],'failures':[r for r in rows if not r['status'].startswith('IDENTITY_RESOLVES_')]},ensure_ascii=False,indent=2))
    return out

def verify():
    x=load(OUT);g=x['governance'];assert x['status']=='COMPLETE' and g['outcome_access'] is False and g['score_fields_read'] is False and g['result_fields_read'] is False and g['prediction_change'] is False and g['fuzzy_matching'] is False
    print('R43Z1 zero-label result identity preflight verified')

if __name__=='__main__':
    cmd=sys.argv[1] if len(sys.argv)>1 else 'run'
    if cmd=='run':run()
    elif cmd=='verify':verify()
    else:raise SystemExit(cmd)
