from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
C=ROOT/'governance/football3/v3_c3_sealed_historical_oos_contract_v1.json'
A=ROOT/'governance/football3/v3_c3_historical_oos_coverage_audit_v1.json'
def load(p): return json.loads(p.read_text(encoding="utf-8"))
def test_contract():
 c=load(C)
 assert c['classification']=='SEALED_HISTORICAL_OOS_CONFIRMATION'
 assert c['paused_future_confirmation']['status']==['PAUSED_FUTURE_ENROLLMENT','NOT_DELETED','NOT_REWRITTEN']
 assert c['paused_future_confirmation']['future_enrollment_allowed'] is False
 assert c['frozen_candidate']['head']=='8a0da8528b9074e095cab323f825fd53570b5db4'
 assert c['frozen_candidate']['beta']==0.878653613734059
 assert c['frozen_candidate']['required_n']==8454
 assert c['decision']['terminal']=='STOP_DATA_COVERAGE'
 assert c['decision']['labels_opened']==0
 assert c['inactive']=={'status':'NOT_AVAILABLE','weight':0,'matrix_delta':0,'data_ready':False}
 assert all(c['forbidden_changes'].values())
def test_audit():
 a=load(A)
 assert a['audit_mode']=='ZERO_LABEL_METADATA_ONLY'
 assert a['result_values_read']==0 and a['goal_values_read']==0 and a['xg_values_read_for_target_selection']==0
 assert a['required_n']==8454 and a['usable_n']==0 and a['gap_n']==8454
 assert a['decision']=='STOP_DATA_COVERAGE' and a['labels_opened']==0
 assert a['existing_sources'][0]['big5_2014_2023_n']==18084
 assert a['existing_sources'][0]['big5_2024_partial_n']==59
 assert a['explicit_exclusions']['reuse_allowed'] is False
