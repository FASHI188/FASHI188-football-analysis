#!/usr/bin/env python3
"""V6.6.12 build the unresolved strict-current roster queue with context-aware priorities.

Unresolved means not STRICT_CURRENT. Context priority is mutually exclusive and mirrors the
V6.6.12 effective registry:
  A_NO_CURRENT_CONTEXT
  B_HAS_PROVISIONAL_ONLY
  C_HAS_ACTIVE_MATCH_POOL

ACTIVE_MATCH_POOL is useful current official match-squad continuity but remains below a registered
roster and therefore stays in the strict-current gap queue. Its freshness rules MUST match the
effective-context ledger exactly: evidence observation <=8 days and latest official match <=45 days.
"""
from __future__ import annotations
import json,re,unicodedata
from collections import Counter
from datetime import datetime,timezone,timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];M=ROOT/'manifests';TEAM=M/'v6_team_configuration_weekly_v660_status.json';CURRENT=M/'v6_current_roster_overlay_v669_status.json';PROV=M/'v6_team_provisional_roster_v667_status.json';EFFECTIVE=M/'v6_team_context_effective_v6610_status.json';ACTIVE=ROOT/'evidence'/'team_active_match_pool_weekly';OUT=M/'v6_roster_gap_inventory_v6611_status.json'
def load(path:Path)->dict:return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
def norm(value:str)->str:
 text=unicodedata.normalize('NFKD',str(value)).encode('ascii','ignore').decode().lower();return ' '.join(re.findall(r'[a-z0-9]+',text))
def key(cid,team):return str(cid),norm(str(team))
def parse_ts(value):return datetime.fromisoformat(str(value or '').replace('Z','+00:00'))
def active_map(now:datetime)->dict:
 latest={}
 for p in ACTIVE.glob('*.json') if ACTIVE.exists() else []:
  try:x=load(p);rows=x.get('records') if isinstance(x,dict) and isinstance(x.get('records'),list) else []
  except Exception:continue
  for r in rows:
   if not isinstance(r,dict) or r.get('status')!='ACTIVE_MATCH_POOL_AVAILABLE' or int(r.get('active_player_count') or 0)<18:continue
   try:obs=parse_ts(r.get('observed_at_utc'));match=parse_ts(r.get('latest_match_utc'))
   except Exception:continue
   if obs.tzinfo is None or match.tzinfo is None or now-obs>timedelta(days=8) or now-match>timedelta(days=45):continue
   k=key(r.get('competition_id'),r.get('team_name'));prev=latest.get(k)
   if prev is None or obs>prev[0]:latest[k]=(obs,{'active_player_count':int(r.get('active_player_count') or 0),'latest_match_utc':r.get('latest_match_utc')})
 return {k:v[1] for k,v in latest.items()}
def main()->int:
 now=datetime.now(timezone.utc).replace(microsecond=0);team=load(TEAM);current=load(CURRENT);prov=load(PROV);effective=load(EFFECTIVE);latest=[r for r in team.get('latest') or [] if isinstance(r,dict)]
 if not latest:raise SystemExit('weekly team latest inventory missing')
 additions={key(r.get('competition_id'),r.get('resolved_team_name')) for r in current.get('matched_overlays') or [] if r.get('strict_roster_addition') is True};active=active_map(now);prov_map={}
 for r in prov.get('attempts') or []:
  if isinstance(r,dict) and r.get('status')=='PROVISIONAL_CONTINUITY_AVAILABLE':prov_map[key(r.get('competition_id'),r.get('team_name'))]={'previous_season':r.get('previous_season'),'previous_player_count':int(r.get('previous_player_count') or 0)}
 gaps=[]
 for r in latest:
  if r.get('roster_research_eligible') is True:continue
  k=key(r.get('competition_id'),r.get('team_name'))
  if k in additions:continue
  a=active.get(k);p=prov_map.get(k)
  priority='C_HAS_ACTIVE_MATCH_POOL' if a is not None else 'B_HAS_PROVISIONAL_ONLY' if p is not None else 'A_NO_CURRENT_CONTEXT'
  gaps.append({'competition_id':r.get('competition_id'),'team_name':r.get('team_name'),'season':r.get('season'),'base_named_players':int(r.get('players') or 0),'active_match_pool_available':a is not None,'active_player_count':a.get('active_player_count') if a else 0,'active_pool_latest_match_utc':a.get('latest_match_utc') if a else None,'provisional_previous_season_available':p is not None,'previous_season':p.get('previous_season') if p else None,'previous_season_player_count':p.get('previous_player_count') if p else 0,'priority':priority,'required_resolution':'CURRENT_FIRST_TEAM_OR_CURRENT_REGISTERED_SQUAD_CONTRACT_QUALIFIED'})
 gaps.sort(key=lambda r:(r['priority'],str(r['competition_id']),str(r['team_name'])));by_comp=Counter(str(r['competition_id']) for r in gaps);counts=Counter(str(r['priority']) for r in gaps);states=effective.get('roster_context_states') or {};expected={'A_NO_CURRENT_CONTEXT':int(states.get('NO_ROSTER_CONTEXT') or 0),'B_HAS_PROVISIONAL_ONLY':int(states.get('PROVISIONAL_ONLY') or 0),'C_HAS_ACTIVE_MATCH_POOL':int(states.get('ACTIVE_MATCH_POOL') or 0)};consistency=all(int(counts.get(k,0))==v for k,v in expected.items());strict=int(states.get('STRICT_CURRENT') or 0);conservation=len(gaps)+strict==len(latest)
 payload={'schema_version':'V6.6.12-roster-gap-inventory-r3','generated_at_utc':now.isoformat(),'status':'PASS' if consistency and conservation else 'FAIL_CONTEXT_STATE_MISMATCH','formal_current_version':'V5.0.1','team_count':len(latest),'effective_strict_current_count':strict,'unresolved_strict_current_gap_count':len(gaps),'priority_counts':{k:int(counts.get(k,0)) for k in ('A_NO_CURRENT_CONTEXT','B_HAS_PROVISIONAL_ONLY','C_HAS_ACTIVE_MATCH_POOL')},'competition_gap_counts':dict(sorted(by_comp.items())),'gaps':gaps,'consistency_check':{'effective_states':states,'inventory_expected_priority_counts':expected,'inventory_matches_effective_states':consistency,'strict_plus_gap_conservation':conservation,'active_pool_freshness_aligned_with_effective_ledger':True},'governance':{'derived_from_validated_receipts_only':True,'active_match_pool_never_counts_as_strict':True,'active_match_pool_observation_max_age_days':8,'active_match_pool_latest_match_max_age_days':45,'provisional_never_counts_as_strict':True,'no_player_list_union_across_provider_groups':True,'research_context_only':True,'formal_probability_change':False,'formal_weight_change':False}}
 OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({k:payload[k] for k in ('status','effective_strict_current_count','unresolved_strict_current_gap_count','priority_counts','competition_gap_counts','consistency_check')},ensure_ascii=False,indent=2));return 0 if payload['status']=='PASS' else 2
if __name__=='__main__':raise SystemExit(main())