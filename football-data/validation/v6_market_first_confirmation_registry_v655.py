#!/usr/bin/env python3
"""V6.5.5 independently confirmed market-first research champion registry.

Locks the V6.5.0 threshold after V6.5.4 passed a fully disjoint, outcome-blind historical
confirmation panel. This registry explicitly forbids further threshold retuning on either
historical panel. Promotion to formal execution still requires V6.5.1 pristine future evidence.
CURRENT remains unchanged.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
MAN=ROOT/'manifests'
OUT=MAN/'v6_market_first_confirmation_registry_v655_status.json'
FILES={
    'development_rule':'v6_market_first_selector_v650_status.json',
    'fresh_confirmation':'v6_market_first_fresh_confirmation_v654_status.json',
    'market_timing':'v6_opening_vs_closing_market_v653_status.json',
    'forward_freeze':'v6_market_first_forward_freeze_v651.json',
    'forward_evaluation':'v6_market_first_forward_evaluation_v651_status.json',
    'architecture_registry':'v6_market_first_architecture_registry_v652_status.json',
}

def load(name):
    p=MAN/name
    if not p.exists():raise RuntimeError(f'missing {name}')
    return json.loads(p.read_text(encoding='utf-8'))
def sha(name):return hashlib.sha256((MAN/name).read_bytes()).hexdigest()

def main():
    x={k:load(v) for k,v in FILES.items()};dev=x['development_rule'];fresh=x['fresh_confirmation'];timing=x['market_timing'];freeze=x['forward_freeze'];feval=x['forward_evaluation']
    threshold=float(dev['arms']['A_market']['selected_rule']['threshold'])
    checks={
        'development_threshold_is_035':abs(threshold-.35)<1e-12,
        'fresh_panel_pass':fresh.get('status')=='PASS',
        'fresh_no_overlap':fresh['pre_registered_design']['overlap_allowed'] is False,
        'fresh_parameters_not_tunable':fresh['pre_registered_design']['confirmation_parameters_tunable'] is False,
        'fresh_newer_raw65':bool(fresh.get('primary_raw65_met')),
        'fresh_newer_wilson65':bool(fresh.get('primary_wilson65_met')),
        'fresh_all_15_competitions':int(fresh['fresh_newer_750_primary']['competitions_represented'])==15,
        'opening_market_beats_v6':float(timing['opening_gain_pp_vs_v6_newer'])>0.0,
        'opening_selector_raw65':float(timing['opening_selector_newer']['accuracy'])>=.65,
        'forward_freeze_matches_threshold':abs(float(freeze['rule']['selective_threshold'])-threshold)<1e-12,
        'forward_no_backfill':freeze['rule']['historical_backfill'] is False,
        'forward_integrity_pass':feval.get('status')=='PASS',
    }
    status='PASS' if all(checks.values()) else 'FAIL_EVIDENCE_CONSISTENCY'
    payload={
        'schema_version':'V6.5.5-market-first-confirmed-research-champion-r1',
        'generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        'status':status,
        'evidence_checks':checks,
        'locked_research_champion':{
            'probability_source':'synchronized de-vigged 1X2 market when verifiable',
            'selective_rule':{'pick':'market_top1','draws_excluded':True,'confidence_minimum':threshold},
            'development_newer':dev['primary_newer_test'],
            'fresh_disjoint_newer_confirmation':fresh['fresh_newer_750_primary'],
            'fresh_disjoint_combined':fresh['combined_1500'],
            'early_market_confirmation':timing['opening_selector_newer'],
            'historical_status':'INDEPENDENT_CONFIRMATION_PASS',
        },
        'frozen_next_stage':{
            'epoch':'V6.5.1',
            'freeze_timestamp_utc':freeze['freeze_timestamp_utc'],
            'required_valid_settled':freeze['forward_gates']['minimum_valid_settled'],
            'required_selected':freeze['forward_gates']['minimum_selected'],
            'required_competitions':freeze['forward_gates']['minimum_competitions'],
            'required_raw_accuracy':freeze['forward_gates']['raw_accuracy_minimum'],
            'required_wilson90_lower':freeze['forward_gates']['wilson90_lower_minimum'],
            'historical_backfill':False,
        },
        'no_more_historical_tuning':{
            'threshold_locked':True,
            'development_panel_may_not_retune':True,
            'fresh_confirmation_panel_may_not_retune':True,
            'opening_vs_closing_panel_may_not_retune':True,
            'new_threshold_requires_new_named_epoch_and_new_preregistered_confirmation':True,
        },
        'source_receipts':{k:{'path':v,'sha256':sha(v)} for k,v in FILES.items()},
        'governance':{
            'historical_confirmation_is_not_formal_promotion':True,
            'pristine_future_required':True,
            'automatic_promotion':False,
            'manual_review_required':True,
            'current_rule_change':False,
            'formal_weight_change':False,
            'runtime_probability_change':False,
            'current_remains_v5':True,
        },
    }
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(payload,ensure_ascii=False,indent=2));return 0 if status=='PASS' else 1
if __name__=='__main__':raise SystemExit(main())
