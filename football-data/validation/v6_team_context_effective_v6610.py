#!/usr/bin/env python3
"""V6.6.10 consolidate effective team-context coverage without double counting.

Strict current-roster evidence takes precedence over prior-season provisional continuity. The
three roster-context states are mutually exclusive: STRICT_CURRENT, PROVISIONAL_ONLY, NO_ROSTER_CONTEXT.
Manager and other feature counts remain separately reported. This is an audit/registry view only.
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
 team=load(TEAM);cur=load(CURRENT);prov=load(PROV);total=int(team.get('latest_team_snapshots') or cur.get('team_baseline_count') or 0);base_strict=int((team.get('feature_eligibility') or {}).get('roster') or 0)
 additions=set()
 for row in cur.get('matched_overlays') or []:
  if row.get('strict_roster_addition'):additions.add(key(row.get('competition_id'),row.get('resolved_team_name')))
 provisional=set()
 for row in prov.get('attempts') or []:
  if row.get('status')=='PROVISIONAL_CONTINUITY_AVAILABLE':provisional.add(key(row.get('competition_id'),row.get('team_name')))
 provisional_only=provisional-additions;effective_strict=base_strict+len(additions);none=max(0,total-effective_strict-len(provisional_only));elig=team.get('feature_eligibility') or {}
 payload={'schema_version':'V6.6.10-effective-team-context-status-r1','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),'status':'PASS','team_count':total,'roster_context_states':{'STRICT_CURRENT':effective_strict,'PROVISIONAL_ONLY':len(provisional_only),'NO_ROSTER_CONTEXT':none},'strict_breakdown':{'base_strict_current':base_strict,'validated_current_roster_overlay_additions':len(additions),'effective_strict_current':effective_strict},'provisional_breakdown':{'raw_provisional_continuity':len(provisional),'overlap_removed_due_to_strict_current_overlay':len(provisional & additions),'effective_provisional_only':len(provisional_only)},'other_feature_eligibility':{'availability':elig.get('availability'),'transactions':elig.get('transactions'),'depth_chart':elig.get('depth_chart'),'manager':elig.get('manager'),'manager_change':elig.get('manager_change'),'full_context':elig.get('full_context')},'verified_manager_records':team.get('verified_manager_records'),'resolved_manager_records':team.get('resolved_manager_records'),'governance':{'mutually_exclusive_roster_context_states':True,'strict_current_precedence':True,'provisional_never_counts_as_strict':True,'formal_probability_change':False,'formal_weight_change':False}}
 OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(payload,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
