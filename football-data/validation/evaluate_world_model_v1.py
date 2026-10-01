#!/usr/bin/env python3
"""World Model V1 frozen retrospective evaluator. Research-only; formal_weight=0."""
from __future__ import annotations
import argparse,hashlib,json,math,sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
from typing import Any
import numpy as np
HELPER=Path(__file__).resolve().parents[1]/"research"/"world_model_v1"
sys.path.insert(0,str(HELPER))
from wmv1_source import *
from wmv1_features import *
from wmv1_model import *
def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    matches, matches_sha = load_matches()
    if len(matches) < MIN_USABLE_MATCHES:
        return {'status': 'STOP_DATA_COVERAGE', 'reason': f'only {len(matches)} match metadata rows'}
    lineups, lineup_failures = load_all_lineups(matches)
    lineup_rate = len(lineups) / len(matches)
    if lineup_rate < MIN_LINEUP_SUCCESS_RATE:
        return {'status': 'STOP_DATA_COVERAGE', 'reason': 'lineup coverage below frozen threshold before event target access', 'coverage': {'matches': len(matches), 'lineup_success_rate': lineup_rate, 'lineup_failures': lineup_failures}}
    event_ok, event_missing = event_head_coverage(matches)
    event_rate = event_ok / len(matches)
    if event_rate < MIN_EVENT_SUCCESS_RATE:
        return {'status': 'STOP_DATA_COVERAGE', 'reason': 'event HEAD coverage below frozen threshold before event target access', 'coverage': {'matches': len(matches), 'event_head_success_rate': event_rate, 'event_missing': event_missing}}
    by_date: dict[str, list[MatchMeta]] = defaultdict(list)
    for m in matches:
        if m.match_id in lineups:
            by_date[m.match_date].append(m)
    dates = sorted(by_date)
    cumulative = 0
    target_burn = math.ceil(len(matches) * 0.5)
    test_start_idx = None
    for i, d in enumerate(dates):
        if cumulative >= target_burn:
            test_start_idx = i
            break
        cumulative += len(by_date[d])
    if test_start_idx is None or test_start_idx >= len(dates):
        return {'status': 'STOP_DATA_COVERAGE', 'reason': 'could not create frozen 50/50 chronological split'}
    test_dates = dates[test_start_idx:]
    test_match_count = sum((len(by_date[d]) for d in test_dates))
    if test_match_count < MIN_TEST_MATCHES:
        return {'status': 'STOP_DATA_COVERAGE', 'reason': f'test matches {test_match_count} below {MIN_TEST_MATCHES}'}
    counts = {d: len(by_date[d]) for d in test_dates}
    fold_map = make_folds(test_dates, counts)
    fold_counts = {f: sum((counts[d] for d in test_dates if fold_map[d] == f)) for f in range(3)}
    if min(fold_counts.values()) < MIN_FOLD_MATCHES:
        return {'status': 'STOP_DATA_COVERAGE', 'reason': f'fold counts below frozen minimum: {fold_counts}'}
    history: list[MatchSummary] = []
    training_rows: list[tuple[np.ndarray, float, int]] = []
    player_attack: dict[tuple[str, int], float] = {}
    player_defense: dict[tuple[str, int], float] = {}
    player_apps: dict[tuple[str, int], int] = {}
    last_starters: dict[str, tuple[int, ...]] = {}
    last_roles: dict[str, tuple[int, int, int, int]] = {}
    results: list[dict[str, Any]] = []
    event_failures: list[dict[str, Any]] = []
    event_shas: dict[int, str] = {}
    lineup_shas: dict[int, str] = {mid: lu.sha256 for mid, lu in lineups.items()}
    for date in dates:
        day = sorted(by_date[date], key=lambda m: (m.kick_off, m.match_id))
        contexts: dict[int, PredictionContext] = {}
        model = fit_hazard(training_rows) if date in test_dates else None
        for meta in day:
            contexts[meta.match_id] = make_context(meta, lineups[meta.match_id], history, player_attack, player_defense, player_apps, last_starters, last_roles)
        pending_predictions: dict[int, dict[str, Any]] = {}
        if date in test_dates:
            if model is None:
                return {'status': 'STOP_DATA_COVERAGE', 'reason': f'hazard training rows {len(training_rows)} below {MIN_TRAIN_ROWS} at test date {date}'}
            for meta in day:
                ctx = contexts[meta.match_id]
                pending_predictions[meta.match_id] = {'baseline': baseline_outputs(ctx.lh, ctx.la), 'candidate': simulate_candidate(ctx, model), 'model_rows': model.rows, 'model_iterations': model.iterations}
        summaries: dict[int, MatchSummary] = {}
        with ThreadPoolExecutor(max_workers=min(12, max(1, len(day)))) as pool:
            futures = {pool.submit(summarize_events, meta, lineups[meta.match_id]): meta for meta in day}
            for future in as_completed(futures):
                meta = futures[future]
                try:
                    summary = future.result()
                    summaries[meta.match_id] = summary
                    event_shas[meta.match_id] = summary.event_sha256
                except Exception as exc:
                    event_failures.append({'match_id': meta.match_id, 'date': date, 'error': f'{type(exc).__name__}: {exc}'})
        if len(summaries) != len(day):
            return {'status': 'STOP_DATA_COVERAGE', 'reason': 'event download/parse failure after frozen HEAD coverage; no partial scientific verdict', 'event_failures': event_failures}
        if date in test_dates:
            for meta in day:
                summary = summaries[meta.match_id]
                pred = pending_predictions[meta.match_id]
                hg = int(sum(summary.home.goal_bins_for))
                ag = int(sum(summary.away.goal_bins_for))
                hb, ab = (min(hg, 7), min(ag, 7))
                yi = outcome_index(hg, ag)
                total = hg + ag
                b = pred['baseline']
                c = pred['candidate']
                b_score = -math.log(max(float(b['matrix'][hb, ab]), PROB_FLOOR))
                c_score = -math.log(max(float(c['matrix'][hb, ab]), PROB_FLOOR))
                b_hda_ll = -math.log(max(float(b['hda'][yi]), PROB_FLOOR))
                c_hda_ll = -math.log(max(float(c['hda'][yi]), PROB_FLOOR))
                is_draw = hg == ag
                results.append({'match_id': meta.match_id, 'match_date': date, 'fold': int(fold_map[date]), 'home': meta.home, 'away': meta.away, 'home_goals': hg, 'away_goals': ag, 'score_baseline': b_score, 'score_candidate': c_score, 'score_delta': c_score - b_score, 'hda_ll_baseline': b_hda_ll, 'hda_ll_candidate': c_hda_ll, 'hda_brier_baseline': multiclass_brier(b['hda'], yi), 'hda_brier_candidate': multiclass_brier(c['hda'], yi), 'total_rps_baseline': total_rps(b['total'], total), 'total_rps_candidate': total_rps(c['total'], total), 'draw_ll_baseline': draw_logloss(float(b['hda'][1]), is_draw), 'draw_ll_candidate': draw_logloss(float(c['hda'][1]), is_draw), 'draw_p_baseline': float(b['hda'][1]), 'draw_p_candidate': float(c['hda'][1]), 'is_draw': is_draw, 'top1_baseline': int(np.argmax(b['hda'])), 'top1_candidate': int(np.argmax(c['hda'])), 'model_rows': int(pred['model_rows']), 'model_iterations': int(pred['model_iterations'])})
        for meta in day:
            summary = summaries[meta.match_id]
            add_training_rows(contexts[meta.match_id], summary, training_rows)
        for meta in day:
            summary = summaries[meta.match_id]
            history.append(summary)
            update_player_state(summary, player_attack, player_defense, player_apps, last_starters, last_roles)
    if len(results) < MIN_TEST_MATCHES:
        return {'status': 'STOP_DATA_COVERAGE', 'reason': f'only {len(results)} evaluated test matches'}

    def mean_key(key: str) -> float:
        return float(np.mean([r[key] for r in results]))
    metrics = {'baseline': {'exact_score_logscore': mean_key('score_baseline'), 'hda_logloss': mean_key('hda_ll_baseline'), 'hda_brier': mean_key('hda_brier_baseline'), 'total_rps': mean_key('total_rps_baseline'), 'draw_binary_logloss': mean_key('draw_ll_baseline')}, 'candidate': {'exact_score_logscore': mean_key('score_candidate'), 'hda_logloss': mean_key('hda_ll_candidate'), 'hda_brier': mean_key('hda_brier_candidate'), 'total_rps': mean_key('total_rps_candidate'), 'draw_binary_logloss': mean_key('draw_ll_candidate')}}
    deltas = {k: metrics['candidate'][k] - metrics['baseline'][k] for k in metrics['baseline']}
    bootstrap = bootstrap_primary(results)
    fold_metrics = {}
    fold_wins = 0
    for f in range(3):
        rr = [r for r in results if r['fold'] == f]
        delta = float(np.mean([r['score_delta'] for r in rr]))
        fold_metrics[str(f)] = {'n': len(rr), 'exact_score_logscore_delta': delta}
        if delta < 0.0:
            fold_wins += 1
    base_draw_calls = sum((1 for r in results if r['top1_baseline'] == 1))
    cand_draw_calls = sum((1 for r in results if r['top1_candidate'] == 1))
    base_draw_hits = sum((1 for r in results if r['top1_baseline'] == 1 and r['is_draw']))
    cand_draw_hits = sum((1 for r in results if r['top1_candidate'] == 1 and r['is_draw']))
    checks = {'primary_mean_delta_le_neg_0_005': deltas['exact_score_logscore'] <= -0.005, 'bootstrap_p95_lt_zero': bootstrap['p95'] < 0.0, 'fold_primary_wins_at_least_2': fold_wins >= 2, 'hda_logloss_not_materially_worse': deltas['hda_logloss'] <= 0.002, 'total_rps_not_materially_worse': deltas['total_rps'] <= 0.002}
    passed = all(checks.values())
    status = 'RESEARCH_SIGNAL_PASS_RETROSPECTIVE_ONLY' if passed else 'FAIL_RESEARCH_ONLY'
    sample_sha = hashlib.sha256('\n'.join((str(r['match_id']) for r in results)).encode('utf-8')).hexdigest()
    event_digest = hashlib.sha256('\n'.join((f'{k}:{event_shas[k]}' for k in sorted(event_shas))).encode('utf-8')).hexdigest()
    lineup_digest = hashlib.sha256('\n'.join((f'{k}:{lineup_shas[k]}' for k in sorted(lineup_shas))).encode('utf-8')).hexdigest()
    return {'status': status, 'scientific_component_pass': False, 'research_metric_gate_pass': passed, 'boundary': {'formal_weight': 0, 'b05_opened': False, 'new_protected_labels_opened': 0, 'market_data_used': False, 'formal_model_mutation': False, 'formal_data_mutation': False, 'formal_config_mutation': False, 'current_mutation': False, 'main_mutation': False, 'automatic_promotion': False, 'pit_status': 'RETROSPECTIVE_OPEN_DATA_NOT_PROVEN_HISTORICAL_PIT'}, 'coverage': {'metadata_matches': len(matches), 'expected_matches': EXPECTED_MATCHES, 'lineup_success_rate': lineup_rate, 'event_head_success_rate': event_rate, 'event_failures': event_failures}, 'split': {'burn_in_matches': cumulative, 'test_matches': len(results), 'test_start_date': test_dates[0], 'test_sample_sha256': sample_sha, 'fold_counts': {str(k): int(v) for k, v in fold_counts.items()}, 'same_day_update_forbidden': True}, 'metrics': {**metrics, 'deltas_candidate_minus_baseline': deltas}, 'bootstrap': bootstrap, 'fold_metrics': fold_metrics, 'development_gate': {'checks': checks, 'passed': passed}, 'draw_diagnostic': {'baseline_top1_draw_calls': base_draw_calls, 'baseline_top1_draw_hits': base_draw_hits, 'candidate_top1_draw_calls': cand_draw_calls, 'candidate_top1_draw_hits': cand_draw_hits}, 'draw_calibration': {'baseline': calibration_bins(results, 'draw_p_baseline'), 'candidate': calibration_bins(results, 'draw_p_candidate')}, 'training': {'final_segment_rows': len(training_rows), 'feature_count': len(results) and len(make_context(matches[-1], lineups[matches[-1].match_id], history, player_attack, player_defense, player_apps, last_starters, last_roles).static_home) + 5}, 'source_hashes': {'matches_sha256': matches_sha, 'lineup_ledger_sha256': lineup_digest, 'event_ledger_sha256': event_digest}}

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps({'status': result.get('status'), 'research_metric_gate_pass': result.get('research_metric_gate_pass'), 'test_matches': (result.get('split') or {}).get('test_matches'), 'deltas': (result.get('metrics') or {}).get('deltas_candidate_minus_baseline'), 'bootstrap': result.get('bootstrap'), 'development_gate': result.get('development_gate'), 'draw_diagnostic': result.get('draw_diagnostic')}, ensure_ascii=False, indent=2))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
