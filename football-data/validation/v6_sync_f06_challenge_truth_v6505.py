#!/usr/bin/env python3
"""V6.50.5 supplement for the unique 42-item runtime-truth registry.

Runs AFTER the existing general V6.49.5 truth synchronizer. It refreshes only F06 research
challenge evidence/counts from accepted manifests, leaving statuses, probabilities, weights,
F05 evidence, CURRENT, and historical events untouched. Re-running is idempotent.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]; M=ROOT/'manifests'
TRUTH=M/'v6_42_issue_remediation_v6489_status.json'
FRESH=M/'v6_fresh_market_forward_challengers_v6492_status.json'
S98=M/'v6_fresh_matrix_structural_audit_v6498_status.json'
S99=M/'v6_joint_surface_coherence_v6499_status.json'
K00=M/'v6_kl_joint_projection_v6500_status.json'
T01=M/'v6_shrunk_direct_total_v6501_status.json'
K02=M/'v6_kl_shape_audit_v6502_status.json'
O03=M/'v6_ou_kl_direct_total_v6503_status.json'
S04=M/'v6_simplex_direct_total_v6504_status.json'
J05=M/'v6_ou_result_joint_matrix_v6505_status.json'
SCOPE=M/'v6_current_research_scope_v6494_status.json'

def load(p:Path)->dict[str,Any]:
    x=json.loads(p.read_text(encoding='utf-8'))
    if not isinstance(x,dict):raise RuntimeError(str(p))
    return x
def item(t:dict[str,Any],id_:str)->dict[str,Any]:
    for r in t.get('items') or []:
        if isinstance(r,dict) and r.get('id')==id_:return r
    raise RuntimeError(f'missing {id_}')
def require_pass(name:str,x:dict[str,Any]):
    if x.get('status')!='PASS':raise RuntimeError(f'{name} not PASS:{x.get("status")}')
    a=x.get('ledger_audit')
    if isinstance(a,dict) and a.get('status')!='PASS':raise RuntimeError(f'{name} ledger not PASS')
def summary(t):
    out={}
    for r in t.get('items') or []:
        if isinstance(r,dict):out[str(r.get('status'))]=out.get(str(r.get('status')),0)+1
    return out
def main()->int:
    t=load(TRUTH); fresh=load(FRESH); s98=load(S98); s99=load(S99); k00=load(K00); t01=load(T01); k02=load(K02); o03=load(O03); s04=load(S04); j05=load(J05); scope=load(SCOPE)
    for name,x in [('fresh',fresh),('v6498',s98),('v6499',s99),('v6500',k00),('v6501',t01),('v6502',k02),('v6503',o03),('v6504',s04),('v6505',j05),('scope',scope)]:require_pass(name,x)
    if scope.get('active_prediction_targets_in_order')!=['90m_1x2','direct_total_goals','exact_score_from_unified_matrix']:raise RuntimeError('active target order drift')
    if scope.get('retired_handicap_prediction_target') is not True:raise RuntimeError('handicap target reactivated')
    for name,x in [('fresh',fresh),('v6500',k00),('v6501',t01),('v6503',o03),('v6504',s04),('v6505',j05)]:
        g=x.get('governance') or {}
        if g.get('formal_weight')!=0:raise RuntimeError(f'{name} formal weight nonzero')
        if g.get('automatic_promotion') is True:raise RuntimeError(f'{name} auto promotion')
    if (o03.get('governance') or {}).get('independent_provider_consensus') is not False:raise RuntimeError('V6503 provider consensus drift')
    if (j05.get('governance') or {}).get('independent_provider_consensus') is not False:raise RuntimeError('V6505 provider consensus drift')

    me=int((fresh.get('matrix_ledger_audit') or {}).get('event_count') or 0); mse=int((fresh.get('matrix_evaluation') or {}).get('candidate',{}).get('count') or 0)
    d98=s98.get('total_goal_diagnostic') or {}; e98=s98.get('exact_score_diagnostic') or {}; c99=s99.get('candidate_vs_f05') or {}; d00=k00.get('projection_diagnostics') or {}; e00=k00.get('evaluation') or {}; c01=(t01.get('freeze') or {}).get('calibration') or {}; d01=t01.get('prospective_diagnostics') or {}; sh02=k02.get('score_shape') or {}; d03=o03.get('prospective_diagnostics') or {}; e03=o03.get('evaluation') or {}; c04=(s04.get('freeze') or {}).get('calibration') or {}; d04=s04.get('prospective_diagnostics') or {}; d05=j05.get('projection_diagnostics') or {}; e05=j05.get('evaluation') or {}
    row=item(t,'F06')
    row['evidence']=(
        f"Historical source only: V6.47.8 nominated total=comp|margin=full. Current V6.49.2 has {me} frozen matrices and {mse} settled evaluations. "
        f"V6.49.8 structural audit PASS: total mode=2 on {(d98.get('candidate_mode_counts') or {}).get('2',0)}/{s98.get('prediction_count')}; raw score Top-1 unique={e98.get('unique_top1_score_count')}, 1-1={e98.get('one_one_top1_count')}/{s98.get('prediction_count')}. "
        f"V6.49.9 F05/F06 coherence: Top-1 agreement={c99.get('top1_agree_rate')}, mean max probability delta={c99.get('mean_max_abs_probability_delta')}, max delta={c99.get('max_abs_probability_delta')}, raw joint-ready={s99.get('formal_joint_surface_ready_without_coordination')}. "
        f"V6.50.0 KL coherence: frozen={d00.get('count')}, settled={(e00.get('projected') or {}).get('count',0)}, all_converged={d00.get('all_converged')}; it preserves raw total P(T). "
        f"V6.50.1 time-ordered shrink chose w_strength={c01.get('chosen_weight')}, prospective={d01.get('count')}, total-mode changes={d01.get('mode_changed_count')}; proper-score diagnostic improved slightly but current mode collapse remained. "
        f"V6.50.2 KL score-shape: unique score Top-1={sh02.get('unique_top1_score_count')}, 1-1 rate={sh02.get('one_one_top1_rate')}. "
        f"V6.50.3 single-Kambi OU-KL total: frozen={d03.get('count')}, modes={d03.get('candidate_mode_counts')}, changed={d03.get('mode_changed_count')}/{d03.get('count')}, settled={(e03.get('candidate') or {}).get('count',0)}, independent_provider_consensus=false. "
        f"V6.50.4 simplex selected weights={c04.get('chosen_weights')} and produced {d04.get('mode_changed_count')} mode changes; its recurring workflow was retired because it duplicated V6.50.1. "
        f"V6.50.5 coherent F05-result + V6.50.3-total + F06-score-prior matrix: frozen={d05.get('count')}, all_converged={d05.get('all_converged')}, total modes={d05.get('candidate_total_mode_counts')}, score Top-1 unique={d05.get('candidate_unique_score_top1')}, 1-1 rate={d05.get('candidate_one_one_top1_rate')}, settled={(e05.get('candidate') or {}).get('count',0)}. "
        "F06 remains FORWARD_GATED: no replacement has >=100 true post-freeze settlements, and the OU-based paths also fail the independent-provider formal market-source gate."
    )
    t['schema_version']='V6.50.5-42-issue-runtime-truth-registry-r9';t['generated_at_utc']=datetime.now(timezone.utc).replace(microsecond=0).isoformat();t['formal_current_version']='V5.0.1';t['formal_probability_change']=False;t['formal_weight_change']=False
    acq=t.setdefault('current_forward_acquisition',{});acq.update({
        'v6503_ou_kl_prediction_count':d03.get('count'),'v6503_ou_kl_settled':(e03.get('candidate') or {}).get('count',0),'v6503_total_mode_counts':d03.get('candidate_mode_counts'),'v6503_total_mode_changed_count':d03.get('mode_changed_count'),'v6503_independent_provider_consensus':False,
        'v6504_simplex_status':'RETIRED_DUPLICATE_EXECUTION_EVIDENCE_RETAINED','v6504_chosen_weights':c04.get('chosen_weights'),'v6504_prediction_count':d04.get('count'),'v6504_mode_changed_count':d04.get('mode_changed_count'),
        'v6505_joint_prediction_count':d05.get('count'),'v6505_joint_settled':(e05.get('candidate') or {}).get('count',0),'v6505_all_converged':d05.get('all_converged'),'v6505_total_mode_counts':d05.get('candidate_total_mode_counts'),'v6505_score_top1_counts':d05.get('candidate_score_top1_counts'),'v6505_score_top1_unique':d05.get('candidate_unique_score_top1'),'v6505_one_one_top1_rate':d05.get('candidate_one_one_top1_rate'),'v6505_independent_provider_consensus':False,
    })
    rs=t.setdefault('runtime_sync_sources',{});rs.update({'ou_kl_total_v6503':o03.get('generated_at_utc'),'simplex_total_v6504':s04.get('generated_at_utc'),'ou_result_joint_v6505':j05.get('generated_at_utc'),'scope_guard':scope.get('generated_at_utc')})
    h=t.setdefault('hard_rules',{});h.update({'raw_f06_mode_collapse_must_remain_visible':True,'raw_f05_f06_coherence_gap_must_remain_visible':True,'v6503_single_provider_market_cannot_promote':True,'v6504_duplicate_execution_retired':True,'v6505_joint_matrix_cannot_promote_without_forward_quality_and_independent_provider_gate':True,'challenge_pass_is_not_formal_activation':True})
    s=summary(t)
    if s.get('PASS')!=36 or s.get('PARTIAL')!=4 or s.get('FORWARD_GATED')!=2:raise RuntimeError(f'status composition drift:{s}')
    t['summary']={k:s.get(k,0) for k in ['PASS','PARTIAL','FORWARD_GATED','EXTERNAL_DATA_GAP','TODO','BLOCKED']}
    TRUTH.write_text(json.dumps(t,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':'PASS','schema':t['schema_version'],'F06_status':row.get('status'),'v6503_count':d03.get('count'),'v6505_count':d05.get('count'),'v6505_score_unique':d05.get('candidate_unique_score_top1'),'v6505_1_1_rate':d05.get('candidate_one_one_top1_rate'),'summary':t['summary']},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
