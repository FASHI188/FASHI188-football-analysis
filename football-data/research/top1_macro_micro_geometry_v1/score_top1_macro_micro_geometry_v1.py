#!/usr/bin/env python3
import argparse, hashlib, json, math, pathlib
from collections import Counter, defaultdict

class ScoreError(RuntimeError):
    pass

def read_json(path):
    return json.loads(pathlib.Path(path).read_text(encoding='utf-8'))

def read_jsonl(path):
    with pathlib.Path(path).open('r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                yield json.loads(line)

def write_json(path, obj):
    pathlib.Path(path).write_text(json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

def write_jsonl(path, rows):
    with pathlib.Path(path).open('w', encoding='utf-8') as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(',', ':')) + '\n')

def outcome_idx(hg, ag):
    return 0 if hg > ag else 1 if hg == ag else 2

def integrate(m):
    h = d = a = 0.0
    for i, row in enumerate(m):
        for j, v in enumerate(row):
            v = float(v)
            if i > j:
                h += v
            elif i == j:
                d += v
            else:
                a += v
    s = h + d + a
    if not math.isfinite(s) or abs(s - 1.0) > 1e-8:
        raise ScoreError(f'matrix mass {s}')
    return [h / s, d / s, a / s]

def region_peaks(m):
    out = [0.0, 0.0, 0.0]
    for i, row in enumerate(m):
        for j, v in enumerate(row):
            k = 0 if i > j else 1 if i == j else 2
            out[k] = max(out[k], float(v))
    if any(v <= 0.0 for v in out):
        raise ScoreError(f'nonpositive region peak {out}')
    return out

def baseline_top1(p):
    return max(range(3), key=lambda k: (float(p[k]), -k))

def geometry_decision(p, m):
    p = [float(x) for x in p]
    pi = integrate(m)
    if max(abs(a - b) for a, b in zip(p, pi)) > 1e-12:
        raise ScoreError('1x2/matrix integration mismatch')
    b = baseline_top1(p)
    if max(p) > 0.5:
        return b, 'DOMINANT_MAJORITY', region_peaks(m), [None, None, None]
    peaks = region_peaks(m)
    g = [math.sqrt(p[k] * peaks[k]) for k in range(3)]
    c = max(range(3), key=lambda k: (g[k], p[k], -k))
    return c, 'BALANCED_GEOMETRY', peaks, g

def preflight(preds, contract):
    c = Counter()
    by_season = defaultdict(Counter)
    decisions = {}
    for fid in sorted(preds):
        r = preds[fid]
        p = [float(x) for x in r['v3_1_1_1x2']]
        b = baseline_top1(p)
        cand, regime, peaks, evidence = geometry_decision(p, r['v3_1_1_matrix'])
        sy = str(int(r['season']))
        c['fixture_count'] += 1
        c['dominant_majority_fixture_count'] += int(regime == 'DOMINANT_MAJORITY')
        c['balanced_no_majority_fixture_count'] += int(regime == 'BALANCED_GEOMETRY')
        c['candidate_decision_change_count'] += int(b != cand)
        c['candidate_draw_top1_count'] += int(cand == 1)
        c['candidate_home_away_cross_switch_count'] += int({b, cand} == {0, 2})
        by_season[sy]['fixtures'] += 1
        by_season[sy]['dominant'] += int(regime == 'DOMINANT_MAJORITY')
        by_season[sy]['decision_changes'] += int(b != cand)
        by_season[sy]['candidate_draw_top1'] += int(cand == 1)
        decisions[fid] = {
            'baseline_top1': b,
            'candidate_top1': cand,
            'regime': regime,
            'region_peaks': peaks,
            'geometry_evidence': evidence,
        }
    frozen = contract['label_free_preflight_frozen_before_score']
    expected = {
        'fixture_count': int(frozen['fixture_count']),
        'dominant_majority_fixture_count': int(frozen['dominant_majority_fixture_count']),
        'balanced_no_majority_fixture_count': int(frozen['balanced_no_majority_fixture_count']),
        'candidate_decision_change_count': int(frozen['candidate_decision_change_count']),
        'candidate_draw_top1_count': int(frozen['candidate_draw_top1_count']),
        'candidate_home_away_cross_switch_count': int(frozen['candidate_home_away_cross_switch_count']),
    }
    got = {k: int(c[k]) for k in expected}
    if got != expected:
        raise ScoreError(f'label-free preflight drift: got={got} expected={expected}')
    for sy, exp in frozen['by_season'].items():
        got_sy = {k: int(by_season[sy][k]) for k in ('fixtures','dominant','decision_changes','candidate_draw_top1')}
        exp_sy = {k: int(exp[k]) for k in got_sy}
        if got_sy != exp_sy:
            raise ScoreError(f'preflight season drift {sy}: {got_sy} != {exp_sy}')
    return decisions, {'global': got, 'by_season': {k: dict(v) for k, v in sorted(by_season.items())}}

def block(rows):
    n = len(rows)
    base_correct = sum(r['baseline_top1'] == r['y'] for r in rows)
    cand_correct = sum(r['candidate_top1'] == r['y'] for r in rows)
    changes = [r for r in rows if r['baseline_top1'] != r['candidate_top1']]
    draw_rows = [r for r in rows if r['candidate_top1'] == 1]
    dominant = [r for r in rows if r['regime'] == 'DOMINANT_MAJORITY']
    balanced = [r for r in rows if r['regime'] == 'BALANCED_GEOMETRY']
    return {
        'n': n,
        'baseline_top1_correct': base_correct,
        'candidate_top1_correct': cand_correct,
        'baseline_top1_accuracy': base_correct / n if n else None,
        'candidate_top1_accuracy': cand_correct / n if n else None,
        'top1_net_correct': cand_correct - base_correct,
        'decision_change_count': len(changes),
        'decision_change_baseline_correct': sum(r['baseline_top1'] == r['y'] for r in changes),
        'decision_change_candidate_correct': sum(r['candidate_top1'] == r['y'] for r in changes),
        'candidate_draw_top1_count': len(draw_rows),
        'candidate_draw_top1_correct': sum(r['y'] == 1 for r in draw_rows),
        'candidate_draw_top1_accuracy': (sum(r['y'] == 1 for r in draw_rows) / len(draw_rows)) if draw_rows else None,
        'dominant_regime_n': len(dominant),
        'dominant_regime_baseline_correct': sum(r['baseline_top1'] == r['y'] for r in dominant),
        'dominant_regime_candidate_correct': sum(r['candidate_top1'] == r['y'] for r in dominant),
        'balanced_regime_n': len(balanced),
        'balanced_regime_baseline_correct': sum(r['baseline_top1'] == r['y'] for r in balanced),
        'balanced_regime_candidate_correct': sum(r['candidate_top1'] == r['y'] for r in balanced),
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--contract', required=True)
    ap.add_argument('--stress-dir', required=True)
    ap.add_argument('--history-dir', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    contract = read_json(a.contract)
    if contract['status'] != 'FROZEN_BEFORE_OUTCOME_SCORING':
        raise ScoreError('contract not frozen')
    auth = contract['authorization']
    forbidden = ('training_allowed','tuning_allowed','parameter_search_allowed','candidate_selection_allowed','threshold_search_allowed','formal_weight_change_allowed','CURRENT_change_allowed','production_pointer_change_allowed','formal_enablement_change_allowed','promotion_allowed')
    if any(bool(auth[k]) for k in forbidden):
        raise ScoreError('authorization drift')
    cand = contract['single_frozen_candidate']
    if cand['id'] != 'TOP1-MACRO-MICRO-GEOMETRY-MAJORITY-PROTECTED-V1' or cand['parameter_grid'] != {}:
        raise ScoreError('candidate identity drift')

    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stress = pathlib.Path(a.stress_dir)
    hist = pathlib.Path(a.history_dir)
    allowed_leagues = set(contract['development_scope']['leagues'])
    allowed_seasons = set(int(x) for x in contract['development_scope']['season_start_years'])

    preds = {}
    for r in read_jsonl(stress / 'evidence' / 'predictions_label_free.jsonl'):
        if r['league'] in allowed_leagues and int(r['season']) in allowed_seasons:
            preds[r['fixture_id']] = r
    if len(preds) != int(contract['development_scope']['fixture_count_exact']):
        raise ScoreError(f'prediction count {len(preds)}')

    decisions, pf = preflight(preds, contract)
    write_json(out / 'label_free_preflight.json', pf)

    fixtures = {r['fixture_id']: r for r in read_jsonl(hist / 'data' / 'fixtures.jsonl') if r['fixture_id'] in preds}
    labels = {r['fixture_id']: r for r in read_jsonl(hist / 'data' / 'label_vault.jsonl') if r['fixture_id'] in preds}
    if set(fixtures) != set(preds) or set(labels) != set(preds):
        raise ScoreError(f'identity mismatch preds={len(preds)} fixtures={len(fixtures)} labels={len(labels)}')

    rows = []
    label_free_out = []
    for fid in sorted(preds):
        pr, fr, lab, de = preds[fid], fixtures[fid], labels[fid], decisions[fid]
        if pr['league'] != fr['league'] or int(pr['season']) != int(fr['season']):
            raise ScoreError(f'fixture metadata mismatch {fid}')
        hg, ag = int(lab['home_goals']), int(lab['away_goals'])
        y = outcome_idx(hg, ag)
        row = {
            'fixture_id': fid,
            'league': pr['league'],
            'season': int(pr['season']),
            'y': y,
            **de,
        }
        rows.append(row)
        label_free_out.append({
            'fixture_id': fid,
            'league': pr['league'],
            'season': int(pr['season']),
            'baseline_1x2': pr['v3_1_1_1x2'],
            'baseline_top1': de['baseline_top1'],
            'candidate_top1': de['candidate_top1'],
            'regime': de['regime'],
            'region_peaks': de['region_peaks'],
            'geometry_evidence': de['geometry_evidence'],
            'probability_vector_changed': False,
            'joint_matrix_changed': False,
        })

    pooled = block(rows)
    by_season = []
    for sy in sorted(allowed_seasons):
        by_season.append({'season': sy, **block([r for r in rows if r['season'] == sy])})
    by_league = []
    for lg in sorted(allowed_leagues):
        by_league.append({'league': lg, **block([r for r in rows if r['league'] == lg])})
    by_league_season = []
    for lg in sorted(allowed_leagues):
        for sy in sorted(allowed_seasons):
            cohort = [r for r in rows if r['league'] == lg and r['season'] == sy]
            by_league_season.append({'league': lg, 'season': sy, **block(cohort)})

    g = contract['development_gates']
    gates = {
        'fixture_count_exact': pooled['n'] == int(g['fixture_count_exact']),
        'candidate_decision_change_count_exact': pooled['decision_change_count'] == int(g['candidate_decision_change_count_exact']),
        'candidate_draw_top1_count_exact': pooled['candidate_draw_top1_count'] == int(g['candidate_draw_top1_count_exact']),
        'candidate_home_away_cross_switch_count_exact': pf['global']['candidate_home_away_cross_switch_count'] == int(g['candidate_home_away_cross_switch_count_exact']),
        'probabilities_and_matrix_unchanged': bool(g['probabilities_and_matrix_unchanged']),
        'pooled_top1_correct_count_gain': pooled['top1_net_correct'] >= int(g['pooled_top1_correct_count_gain_min']),
        'both_seasons_top1_nondegrade': all(x['top1_net_correct'] >= 0 for x in by_season),
        'all_five_leagues_top1_nondegrade': all(x['top1_net_correct'] >= 0 for x in by_league),
        'candidate_draw_top1_correct_count': pooled['candidate_draw_top1_correct'] >= int(g['candidate_draw_top1_correct_count_min']),
        'dominant_regime_decisions_identity': pooled['dominant_regime_baseline_correct'] == pooled['dominant_regime_candidate_correct'] and bool(g['dominant_regime_decisions_identity']),
    }
    status = contract['terminal']['pass'] if all(gates.values()) else contract['terminal']['fail']
    result = {
        'schema_version': 'football3-top1-macro-micro-geometry-dev-score-result-v1',
        'status': status,
        'classification': contract['classification'],
        'fresh_confirmation': False,
        'promotion_allowed': False,
        'training_performed': False,
        'tuning_performed': False,
        'parameter_search_performed': False,
        'candidate_selection_performed': False,
        'threshold_search_performed': False,
        'formal_weight_changed': False,
        'CURRENT_changed': False,
        'production_pointer_changed': False,
        'formal_enablement_changed': False,
        'probability_vector_changed': False,
        'joint_matrix_changed': False,
        'pooled': pooled,
        'season_blocks': by_season,
        'league_blocks': by_league,
        'league_season_blocks': by_league_season,
        'label_free_preflight': pf,
        'gates': gates,
        'contract_sha256': hashlib.sha256(pathlib.Path(a.contract).read_bytes()).hexdigest(),
    }
    write_json(out / 'development_score_result.json', result)
    write_json(out / 'season_blocks.json', by_season)
    write_json(out / 'league_blocks.json', by_league)
    write_json(out / 'league_season_blocks.json', by_league_season)
    write_jsonl(out / 'candidate_decisions_label_free.jsonl', label_free_out)
    (out / 'artifact_slug.txt').write_text(f"{status}__n_{pooled['n']}__top1net_{pooled['top1_net_correct']}__draw_{pooled['candidate_draw_top1_count']}\n", encoding='utf-8')
    print(json.dumps({
        'status': status,
        'n': pooled['n'],
        'baseline_top1_accuracy': pooled['baseline_top1_accuracy'],
        'candidate_top1_accuracy': pooled['candidate_top1_accuracy'],
        'top1_net_correct': pooled['top1_net_correct'],
        'decision_changes': pooled['decision_change_count'],
        'candidate_draw_top1': pooled['candidate_draw_top1_count'],
        'candidate_draw_correct': pooled['candidate_draw_top1_correct'],
        'candidate_draw_accuracy': pooled['candidate_draw_top1_accuracy'],
        'gates': gates,
    }, sort_keys=True))

if __name__ == '__main__':
    main()
