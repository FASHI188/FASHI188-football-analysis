#!/usr/bin/env python3
"""V6.6.4 repair overlay for incomplete (<18 named players) weekly rosters.

Repair order is source-preserving: retry both supported ESPN roster surfaces and select the more
complete single ESPN payload; only if the best same-provider roster is still sub-18 may
TheSportsDB be used as a tier-3 fallback. Player lists are never merged across providers or
endpoints. Existing injury/transaction/depth evidence is kept.
"""
from __future__ import annotations
import json,re,time,unicodedata,urllib.parse,urllib.request
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1];EVIDENCE=ROOT/'evidence'/'team_configuration_weekly';OUT=ROOT/'manifests'/'v6_team_roster_repair_v664_status.json'
TSD='https://www.thesportsdb.com/api/v1/json/123';ESPN='https://site.api.espn.com/apis/site/v2/sports/soccer';UA='football-v6.6.4-roster-repair/3.0';MIN_PLAYERS=18;REQUEST_INTERVAL_SECONDS=2.05;_last_tsd=0.0
def now()->datetime:return datetime.now(timezone.utc).replace(microsecond=0)
def slug(value:str)->str:return re.sub(r'[^a-z0-9]+','',unicodedata.normalize('NFKD',value).encode('ascii','ignore').decode().lower())
def get_json(url:str,rate_limit_tsd:bool=False)->dict[str,Any]:
 global _last_tsd
 if rate_limit_tsd:
  elapsed=time.monotonic()-_last_tsd
  if elapsed<REQUEST_INTERVAL_SECONDS:time.sleep(REQUEST_INTERVAL_SECONDS-elapsed)
 req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'application/json'})
 with urllib.request.urlopen(req,timeout=30) as response:payload=json.loads(response.read().decode('utf-8',errors='replace'))
 if rate_limit_tsd:_last_tsd=time.monotonic()
 return payload if isinstance(payload,dict) else {}
def latest_aggregate()->tuple[Path,dict[str,Any]]|None:
 c=[]
 for p in EVIDENCE.glob('weekly_aggregate__*.json') if EVIDENCE.exists() else []:
  try:r=json.loads(p.read_text(encoding='utf-8'));t=datetime.fromisoformat(str(r.get('observed_at_utc') or '').replace('Z','+00:00'));c.append((t,p,r))
  except Exception:pass
 if not c:return None
 c.sort(key=lambda x:(x[0],str(x[1])));return c[-1][1],c[-1][2]
def normalize_espn_players(payload:dict[str,Any])->list[dict[str,Any]]:
 """Parse one ESPN payload without combining separate endpoint responses."""
 candidate_containers=[payload]
 team=payload.get('team')
 if isinstance(team,dict):candidate_containers.append(team)
 roster=payload.get('roster')
 if isinstance(roster,dict):candidate_containers.append(roster)
 if isinstance(team,dict) and isinstance(team.get('roster'),dict):candidate_containers.append(team['roster'])
 best=[]
 for container in candidate_containers:
  athletes=container.get('athletes') or container.get('players') or []
  out=[];seen=set()
  for group in athletes if isinstance(athletes,list) else []:
   items=group.get('items') if isinstance(group,dict) else None
   if not isinstance(items,list) and isinstance(group,dict) and isinstance(group.get('athletes'),list):items=group.get('athletes')
   seq=items if isinstance(items,list) else [group]
   for player in seq:
    if isinstance(player,dict) and isinstance(player.get('athlete'),dict):player=player['athlete']
    if not isinstance(player,dict):continue
    name=player.get('displayName') or player.get('fullName') or player.get('name')
    if not name:continue
    key=str(player.get('id') or player.get('uid') or name)
    if key in seen:continue
    seen.add(key);pos=player.get('position');positions=[]
    if isinstance(pos,dict):positions=[x for x in (pos.get('displayName'),pos.get('name'),pos.get('abbreviation')) if x]
    elif pos:positions=[str(pos)]
    out.append({'player_id':str(player.get('id') or player.get('uid') or '') or None,'player_name':name,'positions':positions,'age':player.get('age'),'shirt_number':player.get('jersey') or player.get('jerseyNumber'),'squad_status':(player.get('status') or {}).get('name') if isinstance(player.get('status'),dict) else player.get('status'),'roster_source':'ESPN same-provider roster repair'})
  if len(out)>len(best):best=out
 return best
def espn_players(snapshot:dict[str,Any])->tuple[list[dict[str,Any]],str|None,dict[str,Any]]:
 ids=snapshot.get('provider_ids') or {};league=str(ids.get('espn_league_slug') or '');tid=str(ids.get('espn_team_id') or '')
 if not league or not tid:return [],None,{'reason':'missing ESPN league/team id'}
 urls={'roster':f'{ESPN}/{league}/teams/{tid}/roster','detail_enable_roster':f'{ESPN}/{league}/teams/{tid}?enable=roster'}
 attempts={};candidates=[]
 for label,url in urls.items():
  try:
   players=normalize_espn_players(get_json(url));attempts[label]={'players':len(players),'url':url,'ok':True};candidates.append((len(players),label,url,players))
  except Exception as exc:attempts[label]={'players':0,'url':url,'ok':False,'error':f'{type(exc).__name__}: {exc}'}
 if not candidates:return [],None,attempts
 candidates.sort(key=lambda row:(row[0],row[1]),reverse=True);_count,label,url,players=candidates[0];attempts['selected_endpoint']=label
 return players,url,attempts
def search_team(name:str)->dict[str,Any]|None:
 payload=get_json(f'{TSD}/searchteams.php?t={urllib.parse.quote_plus(name)}',True);rows=payload.get('teams') or [];target=slug(name)
 for row in rows:
  names=[row.get('strTeam'),row.get('strTeamAlternate'),row.get('strTeamShort')]
  if any(target==slug(str(v)) for v in names if v):return row
 return rows[0] if rows else None
def tsd_players(team_id:str)->tuple[list[dict[str,Any]],str]:
 url=f'{TSD}/lookup_all_players.php?id={urllib.parse.quote_plus(team_id)}';payload=get_json(url,True);dedup={}
 for p in payload.get('player') or []:
  name=p.get('strPlayer')
  if not name:continue
  row={'player_id':str(p.get('idPlayer') or '') or None,'player_name':name,'positions':[p.get('strPosition')] if p.get('strPosition') else [],'age':None,'shirt_number':p.get('strNumber') or None,'squad_status':'roster-listed','roster_source':'TheSportsDB roster fallback'};dedup.setdefault(slug(str(name)),row)
 return list(dedup.values()),url
def tsd_fallback(snapshot:dict[str,Any])->tuple[list[dict[str,Any]],dict[str,Any]|None,str|None]:
 ids=snapshot.get('provider_ids') or {};eid=str(ids.get('thesportsdb_team_id') or '');team={'idTeam':eid,'strTeam':snapshot.get('team_name')} if eid else search_team(str(snapshot.get('team_name') or ''))
 if not team or not team.get('idTeam'):return [],None,None
 players,url=tsd_players(str(team['idTeam']));return players,team,url
def make_overlay(snapshot:dict[str,Any],players:list[dict[str,Any]],generated:datetime,source_name:str,source_tier:str,source_url:str,reason:str,before:int,team:dict[str,Any]|None=None)->dict[str,Any]:
 repaired=json.loads(json.dumps(snapshot));repaired['schema_version']='V6.6.4-roster-repair-overlay-r3';repaired['observed_at_utc']=generated.isoformat();repaired['players']=players
 if team:repaired.setdefault('provider_ids',{})['thesportsdb_team_id']=team.get('idTeam')
 h=repaired.setdefault('source_health',{});h['primary_named_player_count_before_repair']=before;h['repair_named_player_count']=len(players);h['named_player_count']=len(players);h['roster_content_ok']=len(players)>=MIN_PLAYERS;h['roster_fallback_used']=source_tier!='tier_2';h['roster_repair_reason']=reason
 repaired.setdefault('sources',[]).append({'source_name':source_name,'source_tier':source_tier,'source_url':source_url,'source_observed_at_utc':generated.isoformat(),'source_role':'sub18_roster_repair','source_reached':True})
 g=repaired.setdefault('governance',{});g['sub18_repair_overlay']=True;g['primary_availability_transactions_depth_preserved']=True;g['formal_probability_use']=False;g['no_cross_source_player_list_merge']=True;g['no_cross_endpoint_player_list_merge']=True
 return repaired
def main()->int:
 latest=latest_aggregate();generated=now()
 if latest is None:
  payload={'schema_version':'V6.6.4-roster-repair-status-r3','generated_at_utc':generated.isoformat(),'status':'NO_WEEKLY_AGGREGATE','repaired':0};OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(payload,ensure_ascii=False,indent=2));return 0
 source_path,aggregate=latest;snapshots=[x for x in aggregate.get('snapshots') or [] if isinstance(x,dict)];deficient=[x for x in snapshots if len(x.get('players') or [])<MIN_PLAYERS];overlays=[];attempts=[]
 for snapshot in deficient:
  before=len(snapshot.get('players') or []);record={'competition_id':snapshot.get('competition_id'),'team_name':snapshot.get('team_name'),'before_players':before,'status':'NOT_REPAIRED'}
  try:
   ep,eu,emeta=espn_players(snapshot);record['espn_best_players']=len(ep);record['espn_attempts']=emeta
   if len(ep)>before and len(ep)>=MIN_PLAYERS:
    selected=str(emeta.get('selected_endpoint') or 'unknown');overlays.append(make_overlay(snapshot,ep,generated,'ESPN public site API','tier_2',eu or '',f'ESPN_SAME_PROVIDER_{selected.upper()}_RECOVERED',before));record['status']='REPAIRED_TO_STRICT_ROSTER_ESPN_SAME_PROVIDER';attempts.append(record);continue
   tp,team,tu=tsd_fallback(snapshot);record['fallback_players']=len(tp)
   best_count=max(before,len(ep))
   if len(tp)>best_count:
    overlays.append(make_overlay(snapshot,tp,generated,'TheSportsDB free API','tier_3_fallback',tu or '', 'ESPN_SAME_PROVIDER_RETRIES_STILL_SUB18',before,team));record['status']='REPAIRED_TO_STRICT_ROSTER_TSD' if len(tp)>=MIN_PLAYERS else 'IMPROVED_BUT_STILL_SUB18'
   elif len(ep)>before:
    selected=str(emeta.get('selected_endpoint') or 'unknown');overlays.append(make_overlay(snapshot,ep,generated,'ESPN public site API','tier_2',eu or '',f'ESPN_SAME_PROVIDER_{selected.upper()}_IMPROVED_BUT_SUB18',before));record['status']='ESPN_SAME_PROVIDER_IMPROVED_BUT_STILL_SUB18'
   else:record['status']='NO_MORE_COMPLETE_SOURCE'
  except Exception as exc:record['status']='REPAIR_ERROR';record['error']=f'{type(exc).__name__}: {exc}'
  attempts.append(record)
 overlay_path=None
 if overlays:
  stamp=generated.strftime('%Y%m%dT%H%M%SZ');overlay_path=EVIDENCE/f'weekly_roster_repair__{stamp}.json';overlay={'schema_version':'V6.6.4-weekly-roster-repair-aggregate-r3','observed_at_utc':generated.isoformat(),'source_weekly_aggregate':str(source_path.relative_to(ROOT)),'snapshot_count':len(overlays),'snapshots':overlays,'governance':{'append_only_overlay':True,'formal_probability_use':False,'same_provider_multi_endpoint_selection_precedes_tier3':True,'no_cross_source_player_list_merge':True,'no_cross_endpoint_player_list_merge':True}};overlay_path.write_text(json.dumps(overlay,ensure_ascii=False,indent=2),encoding='utf-8')
 strict=sum(1 for x in attempts if str(x['status']).startswith('REPAIRED_TO_STRICT_ROSTER'));errors=sum(1 for x in attempts if x['status']=='REPAIR_ERROR')
 payload={'schema_version':'V6.6.4-roster-repair-status-r3','generated_at_utc':generated.isoformat(),'status':'PASS' if not errors else 'WARN','source_weekly_aggregate':str(source_path.relative_to(ROOT)),'total_team_snapshots':len(snapshots),'sub18_before_repair':len(deficient),'repair_attempt_count':len(attempts),'strict_repairs_created':strict,'strict_espn_repairs':sum(1 for x in attempts if x['status']=='REPAIRED_TO_STRICT_ROSTER_ESPN_SAME_PROVIDER'),'strict_espn_retries':sum(1 for x in attempts if x['status']=='REPAIRED_TO_STRICT_ROSTER_ESPN_SAME_PROVIDER'),'strict_tsd_repairs':sum(1 for x in attempts if x['status']=='REPAIRED_TO_STRICT_ROSTER_TSD'),'overlays_created':len(overlays),'overlay_path':str(overlay_path.relative_to(ROOT)) if overlay_path else None,'attempts':attempts,'governance':{'research_context_only':True,'no_formal_probability_change':True,'same_provider_multi_endpoint_selection_first':True,'no_cross_source_player_list_merge':True,'no_cross_endpoint_player_list_merge':True}}
 OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(payload,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())