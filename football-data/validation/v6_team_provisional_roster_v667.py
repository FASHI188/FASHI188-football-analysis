#!/usr/bin/env python3
"""V6.6.7 prior-season provisional roster continuity evidence.

For current team snapshots that fail the strict >=18 current-roster gate, query the same ESPN team
for the immediately preceding season. When that prior roster has >=18 named players, store it in a
SEPARATE evidence layer together with the current PIT transactions/availability already observed.

This does NOT satisfy the strict current-roster gate and must never be called a current registered
squad. It exists only to prevent a total loss of continuity context during preseason roster-feed
transitions. Any downstream model must treat it as higher-uncertainty research context.
"""
from __future__ import annotations
import json,re,urllib.request
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1];TEAM=ROOT/'evidence'/'team_configuration_weekly';OUTROOT=ROOT/'evidence'/'team_provisional_roster_weekly';STATUS=ROOT/'manifests'/'v6_team_provisional_roster_v667_status.json'
SITE='https://site.api.espn.com/apis/site/v2/sports/soccer';UA='football-v6.6.7-provisional-roster/1.0';MIN=18
def now():return datetime.now(timezone.utc).replace(microsecond=0)
def get(url:str)->dict[str,Any]:
 req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'application/json'})
 with urllib.request.urlopen(req,timeout=30) as r:return json.loads(r.read().decode('utf-8',errors='replace'))
def latest_aggregate()->tuple[Path,dict[str,Any]]:
 rows=[]
 for p in TEAM.glob('weekly_aggregate__*.json') if TEAM.exists() else []:
  try:x=json.loads(p.read_text(encoding='utf-8'));t=datetime.fromisoformat(str(x.get('observed_at_utc') or '').replace('Z','+00:00'));rows.append((t,p,x))
  except Exception:pass
 if not rows:raise RuntimeError('no weekly aggregate')
 rows.sort(key=lambda x:(x[0],str(x[1])));return rows[-1][1],rows[-1][2]
def prev_year(raw:str)->int:
 m=re.search(r'(20\d{2})',str(raw or ''));return (int(m.group(1))-1) if m else 2025
def parse_players(payload:dict[str,Any])->list[dict[str,Any]]:
 athletes=payload.get('athletes') or payload.get('players') or [];out=[];seen=set()
 for group in athletes if isinstance(athletes,list) else []:
  items=group.get('items') if isinstance(group,dict) else None;seq=items if isinstance(items,list) else [group]
  for p in seq:
   if not isinstance(p,dict):continue
   name=p.get('displayName') or p.get('fullName') or p.get('name')
   if not name:continue
   key=str(p.get('id') or p.get('uid') or name)
   if key in seen:continue
   seen.add(key);pos=p.get('position');positions=[]
   if isinstance(pos,dict):positions=[v for v in (pos.get('displayName'),pos.get('abbreviation')) if v]
   elif pos:positions=[str(pos)]
   out.append({'player_id':str(p.get('id') or p.get('uid') or '') or None,'player_name':name,'positions':positions,'age':p.get('age'),'shirt_number':p.get('jersey') or p.get('jerseyNumber'),'squad_status':(p.get('status') or {}).get('name') if isinstance(p.get('status'),dict) else p.get('status')})
 return out
def main()->int:
 source_path,agg=latest_aggregate();generated=now();snaps=[x for x in agg.get('snapshots') or [] if isinstance(x,dict)];records=[];attempts=[]
 for s in snaps:
  current=len(s.get('players') or [])
  if current>=MIN:continue
  ids=s.get('provider_ids') or {};league=str(ids.get('espn_league_slug') or '');tid=str(ids.get('espn_team_id') or '')
  if not league or not tid:continue
  py=prev_year(str(s.get('season') or ''));url=f'{SITE}/{league}/teams/{tid}/roster?season={py}';a={'competition_id':s.get('competition_id'),'team_name':s.get('team_name'),'current_player_count':current,'previous_season':str(py),'status':'UNAVAILABLE'}
  try:
   players=parse_players(get(url));a['previous_player_count']=len(players)
   if len(players)>=MIN:
    records.append({'schema_version':'V6.6.7-provisional-roster-continuity-r1','observed_at_utc':generated.isoformat(),'competition_id':s.get('competition_id'),'team_name':s.get('team_name'),'target_season':s.get('season'),'strict_current_roster_eligible':False,'status':'PROVISIONAL_PREVIOUS_SEASON_CONTINUITY','previous_season':str(py),'previous_season_players':players,'current_pit_context':{'current_named_player_count':current,'availability':s.get('availability') or [],'transactions':s.get('transactions') or [],'depth_chart':s.get('depth_chart') or []},'sources':[{'source_name':'ESPN public site API','source_tier':'tier_2','source_url':url,'source_observed_at_utc':generated.isoformat(),'source_role':'previous_season_roster_continuity'}],'uncertainty':{'roster_is_not_current_registered_squad':True,'transfer_window_drift_possible':True,'downstream_weight_requires_forward_validation':True},'governance':{'research_context_only':True,'cannot_satisfy_strict_current_roster_gate':True,'formal_probability_use':False}});a['status']='PROVISIONAL_CONTINUITY_AVAILABLE'
  except Exception as exc:a['error']=f'{type(exc).__name__}: {exc}'
  attempts.append(a)
 OUTROOT.mkdir(parents=True,exist_ok=True);outpath=None
 if records:
  outpath=OUTROOT/f"weekly_provisional_rosters__{generated.strftime('%Y%m%dT%H%M%SZ')}.json";outpath.write_text(json.dumps({'schema_version':'V6.6.7-provisional-roster-weekly-aggregate-r1','observed_at_utc':generated.isoformat(),'source_current_aggregate':str(source_path.relative_to(ROOT)),'record_count':len(records),'records':records,'governance':{'separate_from_strict_current_roster':True,'formal_probability_use':False}},ensure_ascii=False,indent=2),encoding='utf-8')
 payload={'schema_version':'V6.6.7-provisional-roster-status-r1','generated_at_utc':generated.isoformat(),'status':'PASS','strict_current_sub18_count':sum(1 for s in snaps if len(s.get('players') or [])<MIN),'espn_previous_season_attempts':len(attempts),'provisional_continuity_count':len(records),'evidence_path':str(outpath.relative_to(ROOT)) if outpath else None,'attempts':attempts,'governance':{'strict_current_roster_gate_unchanged':True,'provisional_is_separate_research_feature':True,'formal_probability_change':False}}
 STATUS.parent.mkdir(parents=True,exist_ok=True);STATUS.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(payload,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
