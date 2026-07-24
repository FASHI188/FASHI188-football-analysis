#!/usr/bin/env python3
"""V6.8.5.2 research-only audit: exact Kambi competition-name coverage of formal domains.

No predictions, requests, freezes, sidecars, or backfills are created.
"""
from __future__ import annotations
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'evidence'/'market_ladders_v680'/'kambi_full_time_ladders.json'
OUT=ROOT/'manifests'/'v6_kambi_formal_domain_coverage_v6852_status.json'
EPOCH=datetime.fromisoformat('2026-07-23T06:28:17+00:00')
COMP_MAP={
 'Premier League':'ENG_PremierLeague','Bundesliga':'GER_Bundesliga','Serie A':'ITA_SerieA','Ligue 1':'FRA_Ligue1','LaLiga':'ESP_LaLiga','La Liga':'ESP_LaLiga','Liga Portugal':'POR_PrimeiraLiga','Primeira Liga':'POR_PrimeiraLiga','Eredivisie':'NED_Eredivisie','Super League':'SUI_SuperLeague','Scottish Premiership':'SCO_Premiership','Premiership':'SCO_Premiership','Allsvenskan':'SWE_Allsvenskan','Eliteserien':'NOR_Eliteserien','J1 League':'JPN_J1','J League':'JPN_J1','J.League':'JPN_J1','K-League 1':'KOR_KLeague1','K League 1':'KOR_KLeague1','Brasileirao Serie A':'BRA_SerieA','Brasileirão Serie A':'BRA_SerieA','Liga Profesional Argentina':'ARG_Primera','Major League Soccer':'USA_MLS','MLS':'USA_MLS','Champions League':'UEFA_ChampionsLeague','UEFA Champions League':'UEFA_ChampionsLeague',
}

def pdt(v):
    if not v:return None
    try:
        d=datetime.fromisoformat(str(v).replace('Z','+00:00'))
        if d.tzinfo is None:d=d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except:return None

def main():
    payload=json.loads(SRC.read_text(encoding='utf-8'));bundles=payload.get('bundles') or []
    now=datetime.now(timezone.utc);source_counts=Counter();domain_counts=Counter();future_counts=Counter();unmapped=Counter();examples=[]
    for b in bundles:
        obs=pdt(b.get('observed_at_utc'));ko=pdt(b.get('kickoff_utc'));home=str(b.get('home_team_source') or '').strip();away=str(b.get('away_team_source') or '').strip();src=str(b.get('competition_source') or '').strip()
        if not (obs and ko and home and away and obs>=EPOCH and obs<ko):continue
        source_counts[src or '<blank>']+=1;domain=COMP_MAP.get(src)
        if domain:
            domain_counts[domain]+=1
            if ko>now:future_counts[domain]+=1
            if len(examples)<100:examples.append({'event_id':b.get('event_id'),'competition_source':src,'competition_id':domain,'home':home,'away':away,'observed_at_utc':obs.isoformat(),'kickoff_utc':ko.isoformat(),'future_as_of_now':ko>now,'total_lines':(b.get('diagnostics') or {}).get('distinct_total_line_count')})
        else:unmapped[src or '<blank>']+=1
    out={'schema_version':'V6.8.5.2-kambi-formal-domain-coverage-r1','generated_at_utc':now.replace(microsecond=0).isoformat(),'status':'PASS','formal_current_version':'V5.0.1','exact_competition_map':COMP_MAP,'post_epoch_prematch_source_counts':dict(source_counts.most_common()),'formal_domain_counts':dict(sorted(domain_counts.items())),'formal_domain_future_counts':dict(sorted(future_counts.items())),'formal_domain_total':sum(domain_counts.values()),'formal_domain_future_total':sum(future_counts.values()),'unmapped_source_counts':dict(unmapped.most_common()),'examples':examples,'governance':{'research_only':True,'exact_competition_name_only':True,'fuzzy_mapping':False,'creates_formal_request':False,'creates_freeze':False,'historical_backfill':False,'formal_probability_change':False,'formal_weight_change':False,'current_rule_change':False}}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
