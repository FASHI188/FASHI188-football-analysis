#!/usr/bin/env python3
"""V6.5.1 official result resolver for the immutable market-first forward epoch.

Settles only existing MARKET_PREDICTION_FROZEN events after kickoff+2h. ESPN remains the
primary result source and reuses the already-audited V6.1.2 scoreboard calendar logic
(UTC kickoff day -1/0/+1, unique team identity, kickoff-time tolerance, regulation-only
score extraction).

V6.46.5 engineering repair adds a narrow official K LEAGUE fallback for KOR_KLeague1
only when ESPN cannot uniquely identify the fixture. The fallback uses K LEAGUE's own
attendance/result AJAX endpoint, requires an exact local match date plus a strict mapped
home/away identity, and accepts only one official row with integer homeGoal/awayGoal.
It never uses third-party scores and never changes a prediction.

A compact result receipt is frozen in a separate inbox and its SHA-256 is written into the
RESULT_SETTLED event. Existing market predictions are never rewritten. Research-only;
V5.0.1 and V6.5.1's frozen market probability rule remain unchanged.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
VALIDATION=ROOT/'validation';ENGINE=ROOT/'engine'
for path in (VALIDATION,ENGINE):
    if str(path) not in sys.path:sys.path.insert(0,str(path))

import v6_market_first_forward_v651 as market
import v6_pristine_forward_result_resolver_v612 as common
from platform_core import PlatformError, atomic_write_json, load_json, parse_iso_datetime, sha256_json

RESULTS=ROOT/'forward'/'inbox'/'market_first_results_v651.json'
STATUS=ROOT/'manifests'/'v6_market_first_result_resolver_v651_status.json'
MIN_RESULT_AGE=timedelta(hours=2)
RESULT_SCHEMA='V6.5.1-market-first-result-inbox-r1'
KLEAGUE_API='https://www.kleague.com/record/audienceDetailList.do'
KLEAGUE_BASE='https://www.kleague.com/record/audienceDetail.do'
SEOUL=ZoneInfo('Asia/Seoul')
KLEAGUE_TEAM_SHORT={
    'fcanyang':'안양',
    'gangwonfc':'강원',
    'gwangjufc':'광주',
    'jejusk':'제주',
    'jejuskfc':'제주',
    'incheonunited':'인천',
    'incheonutd':'인천',
    'bucheonfc1995':'부천',
    'fcseoul':'서울',
    'ulsanhd':'울산',
    'gimcheonsangmu':'김천',
    'daejeonhanacitizen':'대전',
    'pohangsteelers':'포항',
    'jeonbukhyundaimotors':'전북',
}


def now_utc()->datetime:return datetime.now(timezone.utc).replace(microsecond=0)


def load_result_envelope()->dict[str,Any]:
    if not RESULTS.exists():return {'schema_version':RESULT_SCHEMA,'results':[]}
    x=load_json(RESULTS)
    if x.get('schema_version')!=RESULT_SCHEMA or not isinstance(x.get('results'),list):raise PlatformError('invalid V6.5.1 result inbox')
    return x


def _latin_team_key(value:Any)->str:
    return re.sub(r'[^a-z0-9]','',str(value or '').lower())


def _ko_team_key(value:Any)->str:
    s=str(value or '')
    # K LEAGUE public labels may use full sponsor/club names. Keep only stable club token.
    for token in ('FC','Utd','HD','SK','상무','현대','스틸러스','하나','시티즌'):
        s=s.replace(token,'')
    return re.sub(r'[^0-9A-Za-z가-힣]','',s).lower()


def _expected_kleague_short(value:Any)->str|None:
    return KLEAGUE_TEAM_SHORT.get(_latin_team_key(value))


def _fetch_json(url:str)->dict[str,Any]:
    req=urllib.request.Request(url,headers={
        'User-Agent':'Mozilla/5.0 (compatible; FASHI188-football-audit/1.0)',
        'Accept-Language':'ko-KR,ko;q=0.9,en;q=0.5',
        'Accept':'application/json,text/plain,*/*',
        'Referer':KLEAGUE_BASE,
    })
    with urllib.request.urlopen(req,timeout=20) as resp:
        payload=json.loads(resp.read().decode(resp.headers.get_content_charset() or 'utf-8',errors='replace'))
    if not isinstance(payload,dict) or not isinstance(payload.get('data'),dict):
        raise PlatformError('unexpected K LEAGUE result response shape')
    return payload


def _kleague_year_rows(year:int,cache:dict)->tuple[list[dict[str,Any]],list[str]]:
    key=('kleague_official_rows',year)
    if key in cache:return cache[key]
    rows:list[dict[str,Any]]=[];urls:list[str]=[]
    page=1;max_page=1
    while page<=max_page:
        query=urllib.parse.urlencode({'leagueId':'1','year':str(year),'teamId':'','type':'game','page':str(page),'limit':'300'})
        url=KLEAGUE_API+'?'+query;payload=_fetch_json(url);urls.append(url)
        data=payload['data'];result_rows=data.get('audienceResultList') or []
        if isinstance(result_rows,list):rows.extend(r for r in result_rows if isinstance(r,dict))
        vo=data.get('vo') or {}
        try:max_page=max(1,int(vo.get('maxPage') or 1))
        except Exception:max_page=1
        # Fail closed against unexpected pagination loops.
        if max_page>50:raise PlatformError(f'unexpected K LEAGUE maxPage={max_page}')
        page+=1
    cache[key]=(rows,urls);return rows,urls


def _resolve_kleague_official(event:dict[str,Any],now:datetime,kickoff:datetime,cache:dict)->tuple[dict[str,Any]|None,str,dict[str,Any]]:
    identity=(event.get('payload') or {}).get('fixture_identity') or {}
    home_expected=_expected_kleague_short(identity.get('home_team'));away_expected=_expected_kleague_short(identity.get('away_team'))
    if not home_expected or not away_expected:
        return None,'kleague_team_alias_unmapped',{'home_team':identity.get('home_team'),'away_team':identity.get('away_team')}
    local=kickoff.astimezone(SEOUL);date_token=local.strftime('%Y/%m/%d')
    try:rows,urls=_kleague_year_rows(local.year,cache)
    except Exception as exc:
        return None,'kleague_official_fetch_error',{'error':f'{type(exc).__name__}: {exc}','date_token':date_token}
    matches=[]
    for row in rows:
        if str(row.get('gameDate') or '')!=date_token:continue
        home=str(row.get('homeTeamName') or '');away=str(row.get('awayTeamName') or '')
        hk=_ko_team_key(home);ak=_ko_team_key(away)
        if _ko_team_key(home_expected) not in hk and hk not in _ko_team_key(home_expected):continue
        if _ko_team_key(away_expected) not in ak and ak not in _ko_team_key(away_expected):continue
        try:hg=int(row.get('homeGoal'));ag=int(row.get('awayGoal'))
        except Exception:continue
        stable=(str(row.get('gameDate') or ''),home,away,str(row.get('roundId') or ''),hg,ag)
        matches.append((stable,row,hg,ag))
    unique={x[0]:x for x in matches}
    audit_base={'date_token':date_token,'official_api_urls':urls,'official_rows_fetched':len(rows),'mapped_home_short':home_expected,'mapped_away_short':away_expected}
    if not unique:return None,'kleague_official_identity_not_found',audit_base
    if len(unique)>1:return None,'kleague_official_identity_ambiguous',{**audit_base,'candidate_count':len(unique),'candidate_keys':[list(k) for k in unique]}
    stable,row,hg,ag=next(iter(unique.values()));actual='home' if hg>ag else 'away' if hg<ag else 'draw'
    source_record_id='|'.join(str(x) for x in stable)
    receipt={'schema_version':'V6.5.1-market-first-result-receipt-r1','match_id':event.get('match_id'),'competition_id':'KOR_KLeague1','kickoff_at':identity.get('kickoff_at'),'home_team':identity.get('home_team'),'away_team':identity.get('away_team'),'home_goals_90':hg,'away_goals_90':ag,'actual_result':actual,'settlement_scope':'90_minutes_including_stoppage','source':{'name':'K LEAGUE official attendance/result API','url':urls[0] if urls else KLEAGUE_API,'observed_at':now.isoformat(),'source_record_id':source_record_id},'source_metadata':{'local_match_date':date_token,'home_team_official':row.get('homeTeamName'),'away_team_official':row.get('awayTeamName'),'meet_name':row.get('meetName'),'round_id':row.get('roundId'),'field_name':row.get('fieldNameFull'),'score_fields':'homeGoal/awayGoal','fallback_reason':'ESPN identity not found','identity_rule':'exact local date + strict mapped home/away + unique official row'},'prediction_event_hash':event.get('event_hash')}
    return receipt,'resolved_kleague_official_fallback',{**audit_base,'score':[hg,ag],'official_row':{k:row.get(k) for k in ('gameDate','homeTeamName','awayTeamName','homeGoal','awayGoal','meetName','roundId','fieldNameFull')},'source_record_id':source_record_id}


def resolve_one(event:dict[str,Any],now:datetime,cache:dict)->tuple[dict[str,Any]|None,str,dict[str,Any]]:
    identity=(event.get('payload') or {}).get('fixture_identity') or {};cid=str(identity.get('competition_id') or '')
    if cid not in common.DOMAINS:return None,'domain_unmapped',{}
    kickoff=parse_iso_datetime(str(identity.get('kickoff_at') or ''),'kickoff_at');matches=[];pages=[]
    for date_token,payload,url in common.fetch_scoreboards(cid,kickoff,cache):
        pages.append({'date_token':date_token,'url':url})
        for raw_event in payload.get('events') or []:
            if not isinstance(raw_event,dict):continue
            try:event_kickoff=parse_iso_datetime(str(raw_event.get('date') or ''),'espn_event_date')
            except Exception:continue
            if abs(event_kickoff-kickoff)>common.KICKOFF_TOLERANCE:continue
            comps=raw_event.get('competitions') or []
            if not isinstance(comps,list) or not comps or not isinstance(comps[0],dict):continue
            comp=comps[0];competitors=comp.get('competitors') or []
            if not isinstance(competitors,list):continue
            home=next((r for r in competitors if isinstance(r,dict) and r.get('homeAway')=='home'),None);away=next((r for r in competitors if isinstance(r,dict) and r.get('homeAway')=='away'),None)
            if not isinstance(home,dict) or not isinstance(away,dict):continue
            if common.team_matches(cid,home,str(identity.get('home_team') or '')) and common.team_matches(cid,away,str(identity.get('away_team') or '')):
                stable=(str(raw_event.get('id') or ''),event_kickoff.isoformat());matches.append((stable,raw_event,comp,event_kickoff,url,date_token))
    unique={item[0]:item for item in matches}
    if not unique:
        if cid=='KOR_KLeague1':
            receipt,status,audit=_resolve_kleague_official(event,now,kickoff,cache)
            audit={'espn_pages':pages,**audit}
            return receipt,status,audit
        return None,'identity_not_found',{'pages':pages}
    if len(unique)>1:return None,'identity_ambiguous',{'candidate_count':len(unique),'event_ids':[k[0] for k in unique],'pages':pages}
    _,raw_event,comp,event_kickoff,url,date_token=next(iter(unique.values()));score=common.regulation_score(raw_event,comp)
    if score is None:return None,'not_final_or_90m_score_unavailable',{'event_id':raw_event.get('id'),'url':url,'page_date':date_token}
    hg,ag,method=score;actual='home' if hg>ag else 'away' if hg<ag else 'draw'
    receipt={'schema_version':'V6.5.1-market-first-result-receipt-r1','match_id':event.get('match_id'),'competition_id':cid,'kickoff_at':identity.get('kickoff_at'),'home_team':identity.get('home_team'),'away_team':identity.get('away_team'),'home_goals_90':hg,'away_goals_90':ag,'actual_result':actual,'settlement_scope':'90_minutes_including_stoppage','source':{'name':'ESPN public soccer scoreboard API','url':url,'observed_at':now.isoformat(),'source_record_id':str(raw_event.get('id') or '') or None},'source_metadata':{'scoreboard_date_token':date_token,'event_kickoff_at':event_kickoff.isoformat(),'kickoff_difference_seconds':abs((event_kickoff-kickoff).total_seconds()),'regulation_score_extraction':method},'prediction_event_hash':event.get('event_hash')}
    return receipt,'resolved',{'event_id':raw_event.get('id'),'score':[hg,ag],'url':url,'page_date':date_token,'method':method}


def main()->int:
    now=now_utc();freeze=market.ensure_freeze(now);ledger=market.load_ledger();before=market.audit_chain(ledger)
    if before.get('status')!='PASS':raise PlatformError(f'pre-existing V6.5.1 ledger invalid: {before}')
    envelope=load_result_envelope();existing={str(r.get('match_id') or ''):r for r in envelope['results'] if isinstance(r,dict) and r.get('match_id')}
    preds=market.prediction_events(ledger);settled=market.settlement_events(ledger);cache={};stats=Counter();audits=[];new_receipts=[]
    for mid,event in sorted(preds.items()):
        if mid in settled:stats['already_settled']+=1;continue
        identity=event['payload']['fixture_identity'];kickoff=parse_iso_datetime(identity['kickoff_at'],'kickoff_at')
        if now<kickoff+MIN_RESULT_AGE:stats['not_old_enough']+=1;continue
        stats['eligible_for_resolution']+=1
        if mid in existing:
            receipt=existing[mid];stats['already_in_result_inbox']+=1;status='resolved_from_existing_receipt';audit={'result_receipt_sha256':sha256_json(receipt)}
        else:
            receipt,status,audit=resolve_one(event,now,cache);stats[status]+=1
            if receipt is not None:new_receipts.append(receipt);existing[mid]=receipt
        audits.append({'match_id':mid,'competition_id':identity.get('competition_id'),'home_team':identity.get('home_team'),'away_team':identity.get('away_team'),'kickoff_at':identity.get('kickoff_at'),'status':status,'audit':audit})
        if receipt is None:continue
        if str(receipt.get('prediction_event_hash') or '')!=str(event.get('event_hash') or ''):
            stats['prediction_hash_mismatch']+=1;continue
        result={'home_goals_90':int(receipt['home_goals_90']),'away_goals_90':int(receipt['away_goals_90']),'actual_result':str(receipt['actual_result']),'source_record_id':str((receipt.get('source') or {}).get('source_record_id') or '') or None,'settlement_scope':'90_minutes_including_stoppage'}
        market.append_event(ledger,'RESULT_SETTLED',mid,now.isoformat(),{'prediction_event_hash':event['event_hash'],'result':result,'result_source':receipt['source'],'result_receipt_sha256':sha256_json(receipt)})
        settled[mid]=ledger['events'][-1];stats['new_results_settled']+=1
    if new_receipts:
        envelope['results'].extend(new_receipts);envelope['results'].sort(key=lambda r:(str(r.get('competition_id') or ''),str(r.get('kickoff_at') or ''),str(r.get('match_id') or '')))
    atomic_write_json(RESULTS,envelope);after=market.audit_chain(ledger)
    if after.get('status')!='PASS':raise PlatformError(f'V6.5.1 ledger invalid after official settlement append: {after}')
    atomic_write_json(market.LEDGER,ledger);evaluation=market.evaluate(freeze,ledger,now,{},dict(sorted(stats.items())));atomic_write_json(market.OUT,evaluation)
    eligible=int(stats.get('eligible_for_resolution',0));resolved=int(stats.get('new_results_settled',0))+int(stats.get('already_in_result_inbox',0));status='PASS' if eligible==resolved else 'WARN_UNRESOLVED_ELIGIBLE_RESULTS'
    payload={'schema_version':'V6.46.5-market-first-result-resolver-status-r2','generated_at_utc':now.isoformat(),'status':status,'ledger_before':before,'ledger_after':after,'prediction_count':len(preds),'settled_count':len(market.settlement_events(ledger)),'open_prediction_count':len(preds)-len(market.settlement_events(ledger)),'result_inbox_count':len(envelope['results']),'stats':dict(sorted(stats.items())),'audits':audits,'governance':{'existing_market_predictions_only':True,'prediction_generation':False,'processed_training_repository_used_for_settlement':False,'espn_public_scoreboard_primary':True,'kleague_official_fallback_for_kor_kleague1':True,'kleague_fallback_source':KLEAGUE_API,'third_party_score_fallback':False,'scoreboard_calendar_search':'UTC_KICKOFF_DAY_MINUS1_0_PLUS1','unique_team_and_kickoff_identity_required_for_espn':True,'kleague_identity_rule':'Asia/Seoul local date + strict mapped home/away + unique official row','extra_time_or_penalty_final_never_used_as_90m_without_regulation_linescores':True,'result_receipt_hash_frozen_into_settlement':True,'formal_weight_change':False,'runtime_probability_change':False,'current_rule_change':False,'automatic_promotion':False}}
    STATUS.parent.mkdir(parents=True,exist_ok=True);atomic_write_json(STATUS,payload);print(json.dumps(payload,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
