#!/usr/bin/env python3
"""V6.6.5 diagnose current and prior-season ESPN roster surfaces for sub-18 teams.

Current roster eligibility remains strict >=18 and PIT-current. Prior-season results are diagnostic
only: they may support a separately labelled PROVISIONAL_ROSTER continuity baseline when combined
with current transactions, but can never masquerade as a current registered roster.
"""
from __future__ import annotations
import json,re,urllib.request
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]; EVIDENCE=ROOT/'evidence'/'team_configuration_weekly'; OUT=ROOT/'manifests'/'v6_espn_roster_source_diagnostic_v665_status.json'
UA='football-v6.6.5-roster-diagnostic/2.0'; SITE='https://site.api.espn.com/apis/site/v2/sports/soccer'; CORE='https://sports.core.api.espn.com/v2/sports/soccer/leagues'
def get(url:str)->tuple[Any,str|None]:
 try:
  req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'application/json'})
  with urllib.request.urlopen(req,timeout=25) as response:return json.loads(response.read().decode('utf-8',errors='replace')),None
 except Exception as exc:return {},f'{type(exc).__name__}: {exc}'
def latest_aggregate()->dict[str,Any]:
 c=[]
 for p in EVIDENCE.glob('weekly_aggregate__*.json') if EVIDENCE.exists() else []:
  try:r=json.loads(p.read_text(encoding='utf-8')); t=datetime.fromisoformat(str(r.get('observed_at_utc') or '').replace('Z','+00:00')); c.append((t,p,r))
  except Exception:pass
 if not c:raise RuntimeError('no weekly aggregate')
 c.sort(key=lambda x:(x[0],str(x[1]))); return c[-1][2]
def site_names(payload:Any)->list[str]:
 if not isinstance(payload,dict):return []
 athletes=payload.get('athletes') or payload.get('players') or []; out=[]
 for group in athletes if isinstance(athletes,list) else []:
  items=group.get('items') if isinstance(group,dict) else None; seq=items if isinstance(items,list) else [group]
  for player in seq:
   if isinstance(player,dict):
    name=player.get('displayName') or player.get('fullName') or player.get('name')
    if name:out.append(str(name))
 return sorted(set(out))
def enabled_names(payload:Any)->list[str]:
 if not isinstance(payload,dict):return []
 roots=[]
 for container in (payload,payload.get('team') if isinstance(payload.get('team'),dict) else {}):
  for key in ('athletes','roster','players'):
   if key in container:roots.append(container[key])
 out=[]; seen=set()
 def walk(node:Any,depth:int=0):
  if depth>8:return
  if isinstance(node,list):
   for item in node:walk(item,depth+1)
   return
  if not isinstance(node,dict):return
  name=node.get('displayName') or node.get('fullName'); pid=node.get('id') or node.get('uid'); signal=any(k in node for k in ('position','jersey','jerseyNumber','age','headshot'))
  if name and signal:
   token=str(pid or name)
   if token not in seen:seen.add(token);out.append(str(name))
  for key,value in node.items():
   if key not in {'team','league','sport'} and isinstance(value,(dict,list)):walk(value,depth+1)
 for root in roots:walk(root)
 return sorted(set(out))
def season_year(raw:str)->int:
 m=re.search(r'(20\d{2})',str(raw or '')); return int(m.group(1)) if m else 2026
def core_count(payload:Any)->tuple[int|None,int|None]:
 if not isinstance(payload,dict):return None,None
 try:c=int(payload.get('count')) if payload.get('count') is not None else None
 except Exception:c=None
 items=len(payload.get('items') or []) if isinstance(payload.get('items'),list) else None
 return c,items
def main()->int:
 aggregate=latest_aggregate(); snaps=[x for x in aggregate.get('snapshots') or [] if isinstance(x,dict)]; deficient=[x for x in snaps if len(x.get('players') or [])<18 and (x.get('provider_ids') or {}).get('espn_team_id')]; rows=[]
 for snap in deficient:
  ids=snap.get('provider_ids') or {}; league=str(ids.get('espn_league_slug') or ''); tid=str(ids.get('espn_team_id') or ''); year=season_year(str(snap.get('season') or '')); prev=year-1
  urls={'roster':f'{SITE}/{league}/teams/{tid}/roster','enabled_team':f'{SITE}/{league}/teams/{tid}?enable=roster','core_athletes':f'{CORE}/{league}/seasons/{year}/teams/{tid}/athletes?limit=100','previous_site_roster':f'{SITE}/{league}/teams/{tid}/roster?season={prev}','previous_core_athletes':f'{CORE}/{league}/seasons/{prev}/teams/{tid}/athletes?limit=100'}
  payloads={}; errors={}
  for key,url in urls.items():
   payload,error=get(url);payloads[key]=payload
   if error:errors[key]=error
  roster=site_names(payloads['roster']); enabled=enabled_names(payloads['enabled_team']); cc,ci=core_count(payloads['core_athletes']); prev_roster=site_names(payloads['previous_site_roster']); pcc,pci=core_count(payloads['previous_core_athletes'])
  if isinstance(cc,int) and cc>=18:diag='CORE_HAS_FULLER_ROSTER'
  elif len(enabled)>=18:diag='ENABLED_TEAM_HAS_FULLER_ROSTER'
  elif len(roster)>=18:diag='SITE_ROSTER_HAS_FULLER_ROSTER'
  else:diag='ALL_CURRENT_ESPN_SURFACES_SUB18_OR_UNAVAILABLE'
  provisional=(len(prev_roster)>=18 or (isinstance(pcc,int) and pcc>=18))
  rows.append({'competition_id':snap.get('competition_id'),'team_name':snap.get('team_name'),'season':snap.get('season'),'espn_team_id':tid,'league_slug':league,'stored_player_count':len(snap.get('players') or []),'site_roster_parsed_count':len(roster),'enabled_team_parsed_count':len(enabled),'core_athletes_count':cc,'core_items_returned':ci,'previous_season':str(prev),'previous_site_roster_parsed_count':len(prev_roster),'previous_core_athletes_count':pcc,'previous_core_items_returned':pci,'prior_season_continuity_available':provisional,'surface_urls':urls,'errors':errors,'diagnosis':diag})
 counts={}; teams={}; prior=0
 for row in rows:
  d=row['diagnosis'];counts[d]=counts.get(d,0)+1;teams.setdefault(d,[]).append(f"{row['competition_id']}|{row['team_name']}");prior+=int(row['prior_season_continuity_available'])
 payload={'schema_version':'V6.6.5-espn-roster-source-diagnostic-r2','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','deficient_espn_team_count':len(deficient),'diagnosis_counts':dict(sorted(counts.items())),'teams_by_diagnosis':teams,'prior_season_continuity_available_count':prior,'rows':rows,'governance':{'read_only':True,'formal_probability_change':False,'no_roster_rewrite':True,'prior_season_roster_is_provisional_only':True,'current_registered_roster_gate_remains_ge18':True}}
 OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(payload,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
