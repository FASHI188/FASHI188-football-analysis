from __future__ import annotations
import importlib.util
import sys
from pathlib import Path

P=Path(__file__).with_name('public_source_audit.py')
spec=importlib.util.spec_from_file_location('audit',P); m=importlib.util.module_from_spec(spec); sys.modules['audit']=m; spec.loader.exec_module(m)

def rows(): return {r.source_id:r for r in m.static_rows(False)}

def test_locked_power_and_terminal():
    r=m.build_report(False)
    assert r['required_n']==6481
    assert r['deduplicated_eligible_big5_completed_fixture_n']==0
    assert r['gap_to_required_n']==6481
    assert r['terminal']=='NO_SUFFICIENT_PUBLIC_SHOT_EVENT_DATA'
    assert r['target_labels_opened'] is False and r['v3_predictor_run'] is False and r['confirmation_scorer_run'] is False

def test_statsbomb_consumed():
    r=rows()['statsbomb_open']
    assert (r.fixture_n,r.big5_fixture_n,r.overlap_consumed_n,r.eligible_n)==(2169,1853,1853,0)
    assert all(r.schema[k] for k in ('npxg','non_penalty_shots','xg_per_shot','openplay_setpiece','event_time'))

def test_wyscout_schema_and_consumption():
    r=rows()['wyscout_pappalardo']
    assert (r.fixture_n,r.big5_fixture_n,r.overlap_consumed_n,r.eligible_n)==(1826,1826,1826,0)
    assert r.schema['xg_per_shot'] is False and r.schema['npxg'] is False

def test_other_sources_default_deny():
    rr=rows()
    assert (rr['impect_open'].big5_fixture_n,rr['impect_open'].overlap_consumed_n,rr['impect_open'].eligible_n)==(306,306,0)
    assert (rr['idsse_dfl'].fixture_n,rr['idsse_dfl'].big5_fixture_n,rr['idsse_dfl'].overlap_consumed_n)==(7,2,2)
    assert rr['skillcorner_open'].big5_fixture_n==0 and rr['skillcorner_open'].eligible_n==0
    assert rr['understat_public'].admission=='DEFAULT_DENY_LICENSE_VERSION' and rr['understat_public'].eligible_n==0
    assert rr['fotmob_frozen_prior'].fixture_n==9310 and rr['fotmob_frozen_prior'].eligible_n==0

def test_capacity_proof():
    r=m.build_report(False)
    assert r['capacity_proof']['remaining_current_or_future_single_complete_big5_season_theoretical_ceiling']==1752
    assert r['capacity_proof']['ceiling_lt_required_n'] is True

def test_no_model_or_confirmation_execution_surface():
    text=P.read_text(encoding='utf-8')
    forbidden=['import historical_fusion_v3','run_prediction(','score_predictions(','open_label_vault(','market_odds','provider_secret']
    assert not [x for x in forbidden if x in text]
