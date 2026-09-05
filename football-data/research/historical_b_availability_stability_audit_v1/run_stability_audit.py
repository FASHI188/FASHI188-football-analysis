#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, math, pathlib

EPS = 1e-15

class AuditError(RuntimeError):
    pass

def read_json(path):
    return json.loads(pathlib.Path(path).read_text(encoding='utf-8'))

def read_jsonl(path):
    with pathlib.Path(path).open('r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                yield json.loads(line)

def write_json(path, obj):
    pathlib.Path(path).write_text(json.dumps(obj, sort_keys=True, indent=2, allow_nan=False) + '\n', encoding='utf-8')

def sha_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()

def outcome_idx(label):
    hg = int(label['home_goals']); ag = int(label['away_goals'])
    return 0 if hg > ag else 1 if hg == ag else 2

def metrics(rows, labels):
    if not rows:
        raise AuditError('empty cohort')
    ll_b = ll_c = br_b = br_c = rps_b = rps_c = 0.0
    hit_b = hit_c = 0
    for r in rows:
        y = outcome_idx(labels[r['fixture_id']])
        bp = [float(x) for x in r['baseline_b_p']]
        cp = [float(x) for x in r['candidate_p']]
        if abs(sum(bp) - 1.0) > 1e-9 or abs(sum(cp) - 1.0) > 1e-9:
            raise AuditError('probability normalization drift')
        ll_b -= math.log(max(EPS, bp[y])); ll_c -= math.log(max(EPS, cp[y]))
        br_b += sum((bp[i] - (1.0 if i == y else 0.0)) ** 2 for i in range(3))
        br_c += sum((cp[i] - (1.0 if i == y else 0.0)) ** 2 for i in range(3))
        o1 = 1.0 if y == 0 else 0.0
        o2 = 1.0 if y in (0, 1) else 0.0
        rps_b += 0.5 * ((bp[0] - o1) ** 2 + (bp[0] + bp[1] - o2) ** 2)
        rps_c += 0.5 * ((cp[0] - o1) ** 2 + (cp[0] + cp[1] - o2) ** 2)
        hit_b += int(max(range(3), key=lambda i: bp[i]) == y)
        hit_c += int(max(range(3), key=lambda i: cp[i]) == y)
    n = len(rows)
    base = {'n': n, 'logloss': ll_b / n, 'brier': br_b / n, 'rps': rps_b / n, 'top1_correct': hit_b, 'top1_accuracy': hit_b / n}
    cand = {'n': n, 'logloss': ll_c / n, 'brier': br_c / n, 'rps': rps_c / n, 'top1_correct': hit_c, 'top1_accuracy': hit_c / n}
    delta = {
        'one_x_two_logloss_gain': base['logloss'] - cand['logloss'],
        'one_x_two_brier_delta': cand['brier'] - base['brier'],
        'one_x_two_rps_delta': cand['rps'] - base['rps'],
        'top1_net_correct': hit_c - hit_b,
        'top1_delta_pp': (cand['top1_accuracy'] - base['top1_accuracy']) * 100.0,
    }
    return {'n': n, 'baseline_b': base, 'candidate_b_plus_availability': cand, 'deltas': delta}

def strength_regime(r, c):
    p = sorted([float(x) for x in r['baseline_b_p']], reverse=True)
    gap = p[0] - p[1]
    if gap < float(c['deterministic_partitions']['baseline_strength_regime']['parity_gap_lt']):
        return 'parity'
    if p[0] >= float(c['deterministic_partitions']['baseline_strength_regime']['strong_top_probability_gte']):
        return 'strong'
    return 'middle'

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--contract', required=True)
    ap.add_argument('--parent-dir', required=True)
    ap.add_argument('--history-dir', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    c = read_json(a.contract)
    if c['status'] != 'FROZEN_BEFORE_FINE_GRAINED_STABILITY_SCORING':
        raise AuditError('contract status drift')
    auth = c['authorization']
    forbidden = ('training_allowed','tuning_allowed','parameter_search_allowed','threshold_search_allowed','candidate_selection_allowed','prediction_modification_allowed','formal_weight_change_allowed','CURRENT_change_allowed','production_pointer_change_allowed','formal_enablement_change_allowed','promotion_allowed')
    if not auth['outcome_scoring_allowed'] or any(bool(auth[k]) for k in forbidden):
        raise AuditError('authorization drift')

    parent = pathlib.Path(a.parent_dir)
    history = pathlib.Path(a.history_dir)
    pred_path = parent / c['frozen_inputs']['parent_sealed_result']['prediction_file']
    result_path = parent / c['frozen_inputs']['parent_sealed_result']['result_file']
    label_path = history / c['frozen_inputs']['completed_history_labels']['label_file']
    if sha_file(pred_path) != c['frozen_inputs']['parent_sealed_result']['prediction_sha256']:
        raise AuditError('prediction SHA drift')
    if sha_file(result_path) != c['frozen_inputs']['parent_sealed_result']['result_sha256']:
        raise AuditError('parent result SHA drift')

    parent_result = read_json(result_path)
    preds = list(read_jsonl(pred_path))
    labels_all = {str(r['fixture_id']): r for r in read_jsonl(label_path)}
    primary = [r for r in preds if bool(r.get('primary_exact_kickoff_identity'))]
    if len(preds) != 760 or len(primary) != int(c['scope']['primary_exact_kickoff_n']):
        raise AuditError(f'prediction count drift {len(preds)} / {len(primary)}')
    labels = {r['fixture_id']: labels_all[r['fixture_id']] for r in primary if r['fixture_id'] in labels_all}
    if len(labels) != len(primary):
        raise AuditError(f'label join drift {len(labels)} != {len(primary)}')
    active_n = sum(bool(r.get('b_active')) for r in primary)
    if active_n != int(c['scope']['b_active_n']):
        raise AuditError(f'B active drift {active_n}')

    pooled = metrics(primary, labels)
    expected = c['frozen_parent_reproduction']['expected']
    tol = float(c['frozen_parent_reproduction']['abs_tolerance'])
    reproduced = {
        'one_x_two_logloss_gain': abs(pooled['deltas']['one_x_two_logloss_gain'] - float(expected['one_x_two_logloss_gain'])) <= tol,
        'one_x_two_brier_delta': abs(pooled['deltas']['one_x_two_brier_delta'] - float(expected['one_x_two_brier_delta'])) <= tol,
        'one_x_two_rps_delta': abs(pooled['deltas']['one_x_two_rps_delta'] - float(expected['one_x_two_rps_delta'])) <= tol,
        'top1_net_correct': pooled['deltas']['top1_net_correct'] == int(expected['top1_net_correct']),
    }

    temporal = []
    expected_counts = c['deterministic_partitions']['chronological_half_season']['expected_counts']
    for season in (2024, 2025):
        rr = sorted([r for r in primary if int(r['season']) == season], key=lambda x: (str(x['kickoff']), str(x['fixture_id'])))
        cut = len(rr) // 2
        parts = ((f'{season}_H1', rr[:cut]), (f'{season}_H2', rr[cut:]))
        for name, block in parts:
            e = metrics(block, labels)
            e['block'] = name
            e['expected_n'] = int(expected_counts[name])
            e['count_exact'] = len(block) == int(expected_counts[name])
            temporal.append(e)

    active = metrics([r for r in primary if bool(r.get('b_active'))], labels)
    fallback = metrics([r for r in primary if not bool(r.get('b_active'))], labels)

    regimes = []
    for name in ('parity','strong','middle'):
        rr = [r for r in primary if strength_regime(r, c) == name]
        e = metrics(rr, labels)
        e['regime'] = name
        e['gate_eligible'] = len(rr) >= int(c['deterministic_partitions']['baseline_strength_regime']['gate_min_n'])
        regimes.append(e)

    g = c['stability_gates']
    ll_non = sum(e['deltas']['one_x_two_logloss_gain'] >= -1e-15 for e in temporal)
    br_non = sum(e['deltas']['one_x_two_brier_delta'] <= 1e-15 for e in temporal)
    rps_non = sum(e['deltas']['one_x_two_rps_delta'] <= 1e-15 for e in temporal)
    top_non = sum(e['deltas']['top1_net_correct'] >= 0 for e in temporal)
    worst_ll_deg = max(max(0.0, -e['deltas']['one_x_two_logloss_gain']) for e in temporal)
    worst_br_deg = max(max(0.0, e['deltas']['one_x_two_brier_delta']) for e in temporal)
    worst_rps_deg = max(max(0.0, e['deltas']['one_x_two_rps_delta']) for e in temporal)
    eligible_regimes = [e for e in regimes if e['gate_eligible']]
    regime_worst_ll = max(max(0.0, -e['deltas']['one_x_two_logloss_gain']) for e in eligible_regimes)
    regime_worst_br = max(max(0.0, e['deltas']['one_x_two_brier_delta']) for e in eligible_regimes)
    regime_worst_rps = max(max(0.0, e['deltas']['one_x_two_rps_delta']) for e in eligible_regimes)
    regime_worst_top = min(e['deltas']['top1_delta_pp'] for e in eligible_regimes)

    gates = {
        'parent_metric_reproduction': all(reproduced.values()),
        'all_four_temporal_block_counts_exact': all(e['count_exact'] for e in temporal),
        'temporal_logloss_nondegrade_min_blocks': ll_non >= int(g['temporal_logloss_nondegrade_min_blocks']),
        'temporal_brier_nondegrade_min_blocks': br_non >= int(g['temporal_brier_nondegrade_min_blocks']),
        'temporal_rps_nondegrade_min_blocks': rps_non >= int(g['temporal_rps_nondegrade_min_blocks']),
        'temporal_top1_nondegrade_min_blocks': top_non >= int(g['temporal_top1_nondegrade_min_blocks']),
        'temporal_worst_logloss_degradation': worst_ll_deg <= float(g['temporal_worst_logloss_degradation_max']) + 1e-15,
        'temporal_worst_brier_degradation': worst_br_deg <= float(g['temporal_worst_brier_degradation_max']) + 1e-15,
        'temporal_worst_rps_degradation': worst_rps_deg <= float(g['temporal_worst_rps_degradation_max']) + 1e-15,
        'b_active_logloss_gain': active['deltas']['one_x_two_logloss_gain'] >= float(g['b_active_logloss_gain_min']) - 1e-15,
        'b_active_brier_delta': active['deltas']['one_x_two_brier_delta'] <= float(g['b_active_brier_delta_max']) + 1e-15,
        'b_active_rps_delta': active['deltas']['one_x_two_rps_delta'] <= float(g['b_active_rps_delta_max']) + 1e-15,
        'strength_regime_worst_logloss_degradation': regime_worst_ll <= float(g['strength_regime_worst_logloss_degradation_max']) + 1e-15,
        'strength_regime_worst_brier_degradation': regime_worst_br <= float(g['strength_regime_worst_brier_degradation_max']) + 1e-15,
        'strength_regime_worst_rps_degradation': regime_worst_rps <= float(g['strength_regime_worst_rps_degradation_max']) + 1e-15,
        'strength_regime_worst_top1_delta_pp': regime_worst_top >= float(g['strength_regime_worst_top1_delta_pp_min']) - 1e-15,
    }
    status = c['terminal']['pass'] if all(gates.values()) else c['terminal']['fail']
    result = {
        'schema_version': 'football3-modern-b-fplcache-availability-stability-audit-result-v1',
        'status': status,
        'classification': c['classification'],
        'research_only': True,
        'fresh_confirmation': False,
        'promotion_allowed': False,
        'training_performed': False,
        'tuning_performed': False,
        'parameter_search_performed': False,
        'threshold_search_performed': False,
        'prediction_modification_performed': False,
        'historical_confirmation_2023_read': False,
        'prospective_1335_touched': False,
        'formal_v2_changed': False,
        'frozen_v3_1_1_changed': False,
        'stage6_b_frozen_candidate_changed': False,
        'CURRENT_changed': False,
        'production_pointer_changed': False,
        'formal_enablement_changed': False,
        'parent_prediction_sha256': sha_file(pred_path),
        'parent_result_sha256': sha_file(result_path),
        'parent_reproduction_checks': reproduced,
        'pooled_reproduction': pooled,
        'temporal_blocks': temporal,
        'temporal_summary': {
            'logloss_nondegrade_n': ll_non,
            'brier_nondegrade_n': br_non,
            'rps_nondegrade_n': rps_non,
            'top1_nondegrade_n': top_non,
            'worst_logloss_degradation': worst_ll_deg,
            'worst_brier_degradation': worst_br_deg,
            'worst_rps_degradation': worst_rps_deg,
        },
        'b_state': {'active': active, 'fallback': fallback},
        'strength_regimes': regimes,
        'strength_summary': {
            'eligible_regime_n': len(eligible_regimes),
            'worst_logloss_degradation': regime_worst_ll,
            'worst_brier_degradation': regime_worst_br,
            'worst_rps_degradation': regime_worst_rps,
            'worst_top1_delta_pp': regime_worst_top,
        },
        'gates': gates,
        'failed_gates': [k for k,v in gates.items() if not v],
        'contract_sha256': sha_file(a.contract),
    }
    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    write_json(out / 'stability_audit_result.json', result)
    write_json(out / 'temporal_blocks.json', temporal)
    write_json(out / 'strength_regimes.json', regimes)
    (out / 'terminal.txt').write_text(status + '\n', encoding='utf-8')
    print(json.dumps({'status':status,'failed_gates':result['failed_gates'],'temporal_summary':result['temporal_summary'],'active_deltas':active['deltas'],'fallback_deltas':fallback['deltas'],'strength':[{ 'regime':e['regime'],'n':e['n'],'deltas':e['deltas']} for e in regimes]},sort_keys=True))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
