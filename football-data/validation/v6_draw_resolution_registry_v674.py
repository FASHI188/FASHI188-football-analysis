#!/usr/bin/env python3
"""V6.7.4 evidence-locked draw-resolution registry.

This registry prevents repeated retuning of already-rejected draw hacks. It records the current
evidence-based decision: keep market draw probability as the research probability champion;
do not force draw Top-1; use draw as a calibrated risk probability; only genuinely orthogonal
pre-match context may challenge it prospectively.
"""
from __future__ import annotations
import hashlib,json
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
M=ROOT/'manifests'
OUT=M/'v6_draw_resolution_registry_v674_status.json'
SOURCES={
 'multimarket':'v6_multimarket_draw_side_v643_status.json',
 'override':'v6_draw_residual_override_v670_status.json',
 'dynamic_propensity':'v6_dynamic_draw_propensity_v671_status.json',
 'structural_zero':'v6_zero_modified_skellam_draw_v672_status.json',
 'calibration':'v6_draw_probability_calibration_v673_status.json',
 'team_config':'v6_team_configuration_fetch_v660_status.json',
 'market_champion':'v6_market_first_confirmation_registry_v655_status.json',
}
def load(name):return json.loads((M/name).read_text(encoding='utf-8'))
def sha(name):return hashlib.sha256((M/name).read_bytes()).hexdigest()
def main():
    x={k:load(v) for k,v in SOURCES.items()}
    checks={
      'multimarket_rejected':not x['multimarket'].get('research_gate_passed',False),
      'override_rejected':not x['override'].get('research_gate_passed',False),
      'dynamic_propensity_rejected':not x['dynamic_propensity'].get('research_gate_passed',False),
      'structural_zero_rejected':not x['structural_zero'].get('research_gate_passed',False),
      'draw_calibration_no_safe_increment':x['calibration'].get('selected_candidate') is None and not x['calibration'].get('research_gate_passed',False),
      'market_champion_confirmed':x['market_champion'].get('status')=='PASS',
      'team_context_available_for_forward':int(x['team_config'].get('snapshots_written',0))>0,
    }
    h=x['calibration']['baseline_holdout']; ece=float(h['calibration']['ece'])
    actual_draw_count=sum(int(round(float(b['observed'])*int(b['count']))) for b in h['calibration']['bins'])
    status='PASS' if all(checks.values()) else 'FAIL'
    out={
      'schema_version':'V6.7.4-draw-resolution-registry-r2',
      'generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
      'status':status,
      'evidence_checks':checks,
      'diagnosis':{
        'primary_problem':'Top-1 argmax rarely selects draw; held-out draw probability itself is already reasonably calibrated',
        'heldout_draw_probability_ece':ece,
        'heldout_market_draw_top1_count':h['draw_prediction_count'],
        'heldout_actual_draw_count':actual_draw_count,
        'heldout_match_count':h['count'],
        'forced_draw_overrides_supported':False,
      },
      'research_policy':{
        'draw_probability_champion':'de-vigged synchronized 1X2 market draw probability',
        'top1_decision':'plain argmax for all-match probability reporting; do not artificially boost draw',
        'selective_execution':'existing V6.5.5 champion continues to exclude draws',
        'draw_risk_output':'report calibrated draw probability/risk separately for non-selected or ambiguous matches',
        'new_draw_challenger_requirement':'must use orthogonal pre-match PIT context and improve OOS probability scores or paired decisions; no threshold retuning on used panels',
        'allowed_prospective_context':['injuries','suspensions','player_availability','expected_lineup_continuity','manager_change','task_state','two_leg_state'],
      },
      'prospective_context_epoch':{
        'registration':'FORWARD_RESEARCH_ONLY',
        'team_configuration_snapshots':x['team_config'].get('snapshots_written'),
        'domains_with_snapshots':x['team_config'].get('domains_with_snapshots'),
        'missing_domains':[d for d,v in x['team_config'].get('domains',{}).items() if not v.get('snapshots_written')],
        'historical_backfill_allowed':False,
      },
      'rejected_routes':['direct multi-market draw rewrite','generic draw override classifier','time-decayed team draw propensity','market-anchored zero-mass Skellam challenger','one-dimensional draw recalibration'],
      'source_receipts':{k:{'path':v,'sha256':sha(v)} for k,v in SOURCES.items()},
      'governance':{'research_only':True,'current_rule_change':False,'formal_weight_change':False,'runtime_probability_change':False}
    }
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2));return 0 if status=='PASS' else 1
if __name__=='__main__':raise SystemExit(main())
