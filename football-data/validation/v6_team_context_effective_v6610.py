#!/usr/bin/env python3
"""V6.6.12 consolidate mutually exclusive effective team-context coverage.

Roster-context precedence is:
  STRICT_CURRENT > ACTIVE_MATCH_POOL > PROVISIONAL_ONLY > NO_ROSTER_CONTEXT.

STRICT_CURRENT is a current first-team/registered squad and is the only state that satisfies the
strict roster gate. ACTIVE_MATCH_POOL is current official recent-match squad continuity (currently
used for K League) and never counts as strict. PROVISIONAL_ONLY is previous-season continuity.
All four states are mutually exclusive and must conserve the project team count.
"""
from __future__ import annotations
import json,re,unicodedata
from datetime import datetime,timezone,timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];M=ROOT/'manifests';OUT=M/'v6_team_context_effective_v6610_status.json'
TEAM=M/'v6_team_configuration_weekly_v660_status.json';CURRENT=M/'v6_current_roster_overlay_v669_status.json';PROV=M/'v6_team_provisional_roster_v667_status.json';ACTIVE=ROOT/'evidence'/'team_active_match_pool_weekly'
def load(p:Path):return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
def norm(v:str)->str:
 t=unicodedata.normalize('NFKD',str(v)).encode('ascii','ignore').decode().lower();return ' '.join(re.findall(r'[a-z0-9]+',t))
def key(cid,team):return str(cid),norm(str(team))
def active_keys(now:datetime)->set[tuple[str,str]]:
 latest={}
 for p in ACTIVE.glob('*.json') if ACTIVE.exists() else []:
  try:x=load(p);rows=x.get('records') if isinstance(x,dict) and isinstance(x.get('records'),list) else []
  except Exception:continue
  for r in rows:
   if not isinstance(r,dict) or r.get('status')!='ACTIVE_MATCH_POOL_AVAILABLE' or int(r.get('active_player_count') or 0)<18:continue
   try:obs=datetime.fromisoformat(str(r.get('observed_at_utc') or '').replace('Z','+00:00'));latest_match=datetime.fromisoformat(str(r.get('latest_match_utc') or '').replace('Z','+00:00'))
   except Exception:continue
   if obs.tzinfo is None or latest_match.tzinfo is None or now-obs>timedelta(days=8) or now-latest_match>timedelta(days=45):continue
   k=key(r.get('competition_id'),r.get('team_name'));prev=latest.get(k)
   if prev is None or obs>prev:latest[k]=obs
 return set(latest)
def main()->int:
 now=datetime.now(timezone.utc).replace(microsecond=0);team=load(TEAM);cur=load(CURRENT);prov=load(PROV);latest=[r for r in team.get('latest') or [] if isinstance(r,dict)];total=int(team.get('latest_team_snapshots') or cur.get('team_baseline_count') or len(latest) or 0)
 base_strict_keys={key(r.get('competition_id'),r.get('team_name')) for r in latest if r.get('roster_research_eligible') is True};base_strict=int((team.get('feature_eligibility') or {}).get('roster') or len(base_strict_keys))
 if latest and base_strict!=len(base_strict_keys):raise SystemExit(f'base strict count/key mismatch: receipt={base_strict} keys={len(base_strict_keys)}')
 additions=set()
 for row in cur.get('matched_overlays') or []:
  if row.get('strict_roster_addition'):additions.add(key(row.get('competition_id'),row.get('resolved_team_name')))
 strict_keys=base_strict_keys|additions
 active=active_keys(now);active_only=active-strict_keys
 provisional=set()
 for row in prov.get('attempts') or []:
  if row.get('status')=='PROVISIONAL_CONTINUITY_AVAILABLE':provisional.add(key(row.get('competition_id'),row.get('team_name')))
 provisional_after_strict=provisional-strict_keys;provisional_only=provisional_after_strict-active_only
 effective_strict=len(strict_keys) if latest else base_strict+len(additions);none=max(0,total-effective_strict-len(active_only)-len(provisional_only));elig=team.get('feature_eligibility') or {};conservation=effective_strict+len(active_only)+len(provisional_only)+none==total
 payload={'schema_version':'V6.6.12-effective-team-context-status-r3','generated_at_utc':now.isoformat(),'status':'PASS' if conservation else 'FAIL_CONTEXT_STATE_CONSERVATION','team_count':total,'roster_context_states':{'STRICT_CURRENT':effective_strict,'ACTIVE_MATCH_POOL':len(active_only),'PROVISIONAL_ONLY':len(provisional_only),'NO_ROSTER_CONTEXT':none},'strict_breakdown':{'base_strict_current':base_strict,'validated_current_roster_overlay_additions':len(additions-base_strict_keys),'effective_strict_current':effective_strict},'active_match_pool_breakdown':{'raw_active_match_pool':len(active),'overlap_removed_due_to_strict_current':len(active&strict_keys),'effective_active_match_pool_only':len(active_only)},'provisional_breakdown':{'raw_provisional_continuity':len(provisional),'overlap_removed_due_to_strict_current':len(provisional&strict_keys),'overlap_removed_due_to_active_match_pool':len(provisional_after_strict&active_only),'effective_provisional_only':len(provisional_only)},'other_feature_eligibility':{'availability':elig.get('availability'),'transactions':elig.get('transactions'),'depth_chart':elig.get('depth_chart'),'manager':elig.get('manager'),'manager_change':elig.get('manager_change'),'full_context':elig.get('full_context')},'verified_manager_records':team.get('verified_manager_records'),'resolved_manager_records':team.get('resolved_manager_records'),'consistency_check':{'state_count_conservation':conservation,'base_strict_key_count':len(base_strict_keys),'strict_key_count':len(strict_keys),'active_key_count':len(active),'provisional_key_count':len(provisional)},'governance':{'mutually_exclusive_roster_context_states':True,'precedence':['STRICT_CURRENT','ACTIVE_MATCH_POOL','PROVISIONAL_ONLY','NO_ROSTER_CONTEXT'],'strict_current_only_satisfies_strict_roster_gate':True,'active_match_pool_is_current_but_not_registered_roster':True,'provisional_never_counts_as_strict':True,'formal_probability_change':False,'formal_weight_change':False}}
 OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(payload,ensure_ascii=False,indent=2));return 0 if conservation else 2
if __name__=='__main__':raise SystemExit(main())
