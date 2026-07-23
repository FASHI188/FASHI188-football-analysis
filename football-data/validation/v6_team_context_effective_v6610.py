#!/usr/bin/env python3
"""V6.6.11 consolidate mutually exclusive effective team-context coverage.

Strict current-roster evidence takes precedence over prior-season provisional continuity. The
strict set includes both the latest weekly baseline teams that currently pass the strict roster
gate (including any same-provider V6.6.4 repair overlays selected as latest) and validated V6.6.9
CURRENT_FIRST_TEAM/CURRENT_REGISTERED_SQUAD additions. Provisional continuity is then subtractively
resolved against the entire strict set, not only against current-roster overlay additions.
"""
from __future__ import annotations
import json,re,unicodedata
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];M=ROOT/'manifests';OUT=M/'v6_team_context_effective_v6610_status.json'
TEAM=M/'v6_team_configuration_weekly_v660_status.json';CURRENT=M/'v6_current_roster_overlay_v669_status.json';PROV=M/'v6_team_provisional_roster_v667_status.json'
def load(p:Path):return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
def norm(v:str)->str:
 t=unicodedata.normalize('NFKD',str(v)).encode('ascii','ignore').decode().lower();return ' '.join(re.findall(r'[a-z0-9]+',t))
def key(cid,team):return str(cid),norm(str(team))
def main()->int:
 team=load(TEAM);cur=load(CURRENT);prov=load(PROV);latest=[r for r in team.get('latest') or [] if isinstance(r,dict)];total=int(team.get('latest_team_snapshots') or cur.get('team_baseline_count') or len(latest) or 0)
 base_strict_keys={key(r.get('competition_id'),r.get('team_name')) for r in latest if r.get('roster_research_eligible') is True};base_strict=int((team.get('feature_eligibility') or {}).get('roster') or len(base_strict_keys))
 if latest and base_strict!=len(base_strict_keys):raise SystemExit(f'base strict count/key mismatch: receipt={base_strict} keys={len(base_strict_keys)}')
 additions=set()
 for row in cur.get('matched_overlays') or []:
  if row.get('strict_roster_addition'):additions.add(key(row.get('competition_id'),row.get('resolved_team_name')))
 strict_keys=base_strict_keys|additions
 provisional=set()
 for row in prov.get('attempts') or []:
  if row.get('status')=='PROVISIONAL_CONTINUITY_AVAILABLE':provisional.add(key(row.get('competition_id'),row.get('team_name')))
 provisional_overlap_base=provisional&base_strict_keys;provisional_overlap_overlay=provisional&additions;provisional_overlap_all=provisional&strict_keys;provisional_only=provisional-strict_keys
 effective_strict=len(strict_keys) if latest else base_strict+len(additions);none=max(0,total-effective_strict-len(provisional_only));elig=team.get('feature_eligibility') or {};conservation=effective_strict+len(provisional_only)+none==total
 payload={'schema_version':'V6.6.11-effective-team-context-status-r2','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS' if conservation else 'FAIL_CONTEXT_STATE_CONSERVATION','team_count':total,'roster_context_states':{'STRICT_CURRENT':effective_strict,'PROVISIONAL_ONLY':len(provisional_only),'NO_ROSTER_CONTEXT':none},'strict_breakdown':{'base_strict_current':base_strict,'validated_current_roster_overlay_additions':len(additions-base_strict_keys),'effective_strict_current':effective_strict},'provisional_breakdown':{'raw_provisional_continuity':len(provisional),'overlap_removed_due_to_base_strict_current':len(provisional_overlap_base),'overlap_removed_due_to_current_roster_overlay':len(provisional_overlap_overlay),'unique_overlap_removed_due_to_any_strict_current':len(provisional_overlap_all),'effective_provisional_only':len(provisional_only)},'other_feature_eligibility':{'availability':elig.get('availability'),'transactions':elig.get('transactions'),'depth_chart':elig.get('depth_chart'),'manager':elig.get('manager'),'manager_change':elig.get('manager_change'),'full_context':elig.get('full_context')},'verified_manager_records':team.get('verified_manager_records'),'resolved_manager_records':team.get('resolved_manager_records'),'consistency_check':{'state_probability_conservation':conservation,'base_strict_key_count':len(base_strict_keys),'strict_key_count':len(strict_keys),'provisional_key_count':len(provisional)},'governance':{'mutually_exclusive_roster_context_states':True,'strict_current_precedence':True,'all_strict_current_sources_remove_provisional_overlap':True,'provisional_never_counts_as_strict':True,'formal_probability_change':False,'formal_weight_change':False}}
 OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(payload,ensure_ascii=False,indent=2));return 0 if conservation else 2
if __name__=='__main__':raise SystemExit(main())