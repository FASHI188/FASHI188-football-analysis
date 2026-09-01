from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import statistics
import sys
from collections import Counter, defaultdict
from typing import Any

ROOT = pathlib.Path('.').resolve()
CTX = ROOT / 'football-data/research/context_translator_v1'
sys.path.insert(0, str(CTX))

import source_ingest as si
import candidate_b_diagnostic as cbd
import pit_roster_candidate_b_diagnostic as prbd
from candidate_c import (
    UNCERTAINTY_BY_GRADE,
    CandidateCContractError,
    ComponentEffect,
    c1_availability_replacement,
    c2_possible_xi,
    c3_confirmed_xi,
    c4_bench,
    candidate_contract,
    combine_effects,
    deduplicated_c1_plus_lineup,
    evidence_grade,
    matchup_log_mu,
    probability_mass_supported,
    zero_effect,
)
from player_strength import estimate_player_vectors

N_POOL = 272
SAMPLE_N = 100
ACTIVATION_THRESHOLD = 30
MODELS = ('baseline', 'old_l1_l2', 'candidate_b', 'candidate_c1', 'candidate_c1_c2', 'candidate_c_full')
OUTCOMES = ('home', 'draw', 'away')
GRADE_ORDER = ('CONFIRMED_LINEUP_PIT', 'POSSIBLE_XI_PIT', 'TEAM_NEWS_AVAILABILITY_PIT', 'NO_USABLE_ROSTER_EVIDENCE')


def sha(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def canon(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def readjl(path: pathlib.Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()]


def dump(path: pathlib.Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf-8')


def same_prediction(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return all(abs(float(a[k]) - float(b[k])) <= 1e-12 for k in ('p_home', 'p_draw', 'p_away')) and canon(a['score_matrix']) == canon(b['score_matrix'])


def merged_usage(hist_usage: dict[str, list[dict[str, Any]]], roster_usage: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    out = defaultdict(list)
    for src in (hist_usage, roster_usage):
        for k, vals in src.items():
            out[str(k)].extend(vals)
    for k in out:
        out[k].sort(key=lambda x: (str(x.get('known_at', '')), str(x.get('match_id', ''))))
    return out


def effect_prediction(base_matrix: list[list[float]], effect: ComponentEffect, lock: dict[str, Any], eng: Any) -> dict[str, Any]:
    if not effect.active:
        return cbd.pred(base_matrix, eng)
    dh, da = matchup_log_mu(effect)
    bh, ba = cbd.matrix_mean(base_matrix)
    feat = {
        'mu_home': max(1e-8, bh * math.exp(dh)),
        'mu_away': max(1e-8, ba * math.exp(da)),
        'home_evidence': 10.0,
        'away_evidence': 10.0,
    }
    m = eng.joint_matrix(
        lock['joint_family'], feat,
        dispersion_home=float(lock.get('dispersion_home', 50.0)),
        dispersion_away=float(lock.get('dispersion_away', 50.0)),
        dependence=float(lock['dependence']),
        max_goals=int(lock['max_goals']),
    )
    return cbd.pred(m, eng)


def _packet_possible_ids(packet: dict[str, Any]) -> set[str]:
    out = set()
    for side in ('home', 'away'):
        for row in ((packet.get('predicted_lineups') or {}).get(side) or []):
            if row.get('player_id'):
                out.add(str(row['player_id']))
    return out


def _suspension_ids(packet: dict[str, Any]) -> set[str]:
    return {str(x['player_id']) for x in packet.get('status_records') or [] if x.get('status_type') == 'SUSPENSION' and x.get('player_id')}


def _component_uncertainty(effect: ComponentEffect) -> float:
    return max(float(effect.home.uncertainty), float(effect.away.uncertainty))


def prediction_phase(v2: pathlib.Path, candidate_b: pathlib.Path, pit: pathlib.Path, source: pathlib.Path, out: pathlib.Path) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    if (v2 / 'dataset/evaluation_label_vault.jsonl').exists():
        raise RuntimeError('evaluation labels physically present during Candidate C prediction')

    bm = json.load(open(candidate_b / 'artifact_manifest.json'))
    pm = json.load(open(pit / 'artifact_manifest.json'))
    if bm['n'] != N_POOL or bm['formal_weight'] != 0 or bm['formal_promotion_eligible']:
        raise RuntimeError('Candidate B artifact governance mismatch')
    if pm['inventory_n'] != SAMPLE_N or pm['head'] != 'b3929c62583a8f245ee50d36cbe1c4c32b897ff1' or pm['status'] != 'INSUFFICIENT_PIT_ROSTER_DATA':
        raise RuntimeError('frozen PIT roster artifact identity mismatch')

    bfull = readjl(candidate_b / 'candidate_b_predictions.jsonl')
    if len(bfull) != N_POOL:
        raise RuntimeError('Candidate B 272 pool mismatch')
    mechanical_ids = sorted(str(x['fixture_id']) for x in bfull)[:SAMPLE_N]
    bmap = {str(x['fixture_id']): x for x in bfull}

    prior_rows = readjl(pit / 'pit_candidate_b_predictions.jsonl')
    packets = readjl(pit / 'pit_roster_packets.jsonl')
    if [str(x['fixture_id']) for x in prior_rows] != mechanical_ids or [str(x['fixture_id']) for x in packets] != mechanical_ids:
        raise RuntimeError('Candidate C did not receive exact frozen mechanical first-100 inventory')
    pmap = {str(x['fixture_id']): x for x in packets}
    oldmap = {str(x['fixture_id']): x for x in prior_rows}
    for p in packets:
        prbd.validate_packet(p)

    for fid in mechanical_ids:
        if not same_prediction(oldmap[fid]['candidate_b_original'], bmap[fid]['candidate_b1_b2']):
            raise RuntimeError(f'Candidate B comparator drift for {fid}')

    _, mapped = si._map_inventory(v2, source, out)
    ev = readjl(v2 / 'dataset/evaluation_features.jsonl')
    eids = {str(x['fixture_id']) for x in ev}
    gp = [(r, s) for r, s in mapped if str(r['fixture_id']) in eids and str(r['competition_id']) == 'GER1' and si._season(str(r['season'])) == '2023/24']
    if not gp:
        raise RuntimeError('no GER1 StatsBomb mapping for Candidate C')

    tm = cbd.team_map(gp)
    hist = cbd.History(gp)
    eng = cbd.engine()
    lock = json.load(open(v2 / 'locks/v2_lock.json'))
    roster_usage: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rows: list[dict[str, Any]] = []
    comp_reasons: dict[str, Counter[str]] = {k: Counter() for k in ('C1', 'C2', 'C3', 'C4')}
    evidence_counts = Counter()
    ability_attempt_n = ability_available_n = 0
    conflict_n = 0

    ordered_ids = sorted(mechanical_ids, key=lambda fid: (str(oldmap[fid]['cutoff']), fid))
    for fid in ordered_ids:
        o = oldmap[fid]
        packet = pmap[fid]
        cutoff = str(o['cutoff'])
        hist.release_before(cutoff)
        grade = evidence_grade(packet, cutoff)
        evidence_counts[grade] += 1
        base = o['baseline']
        ht = tm.get(str(o['home_team_id']))
        at = tm.get(str(o['away_team_id']))
        vectors: dict[str, Any] = {}
        usage = merged_usage(hist.usage, roster_usage)
        if ht and at:
            pe = [e for e in hist.events if str(e['team_id']) in {str(ht), str(at)}]
            vectors = estimate_player_vectors(pe, hist.segments, as_of=cutoff) if pe else {}

        ids_for_coverage = _packet_possible_ids(packet) | {str(x['player_id']) for x in packet.get('status_records') or [] if x.get('player_id')}
        ability_attempt_n += len(ids_for_coverage)
        if ht and at:
            ability_available_n += sum(pid in vectors and str(vectors[pid].team_id) in {str(ht), str(at)} for pid in ids_for_coverage)

        ev_unc = UNCERTAINTY_BY_GRADE[grade]
        if not packet.get('pit_legal') or not ht or not at or not vectors:
            c1 = zero_effect('C1', 'NO_USABLE_PIT_OR_TEAM_CAPABILITY', uncertainty=ev_unc)
            c2 = zero_effect('C2', 'NO_USABLE_PIT_OR_TEAM_CAPABILITY', uncertainty=ev_unc)
            c3 = zero_effect('C3', 'NO_USABLE_PIT_OR_TEAM_CAPABILITY', uncertainty=ev_unc)
            c4 = zero_effect('C4', 'NO_USABLE_PIT_OR_TEAM_CAPABILITY', uncertainty=ev_unc)
        else:
            c1 = c1_availability_replacement(vectors=vectors, home_team_id=str(ht), away_team_id=str(at), status_records=packet.get('status_records') or [], evidence_uncertainty=ev_unc)
            c2 = c2_possible_xi(vectors=vectors, usage=usage, home_team_id=str(ht), away_team_id=str(at), predicted_lineups=packet.get('predicted_lineups') or {}, cutoff=cutoff) if grade == 'POSSIBLE_XI_PIT' else zero_effect('C2', 'EVIDENCE_GRADE_NOT_POSSIBLE_XI', uncertainty=ev_unc)
            c3 = c3_confirmed_xi(vectors=vectors, usage=usage, home_team_id=str(ht), away_team_id=str(at), confirmed_lineups=packet.get('confirmed_lineups'), cutoff=cutoff) if grade == 'CONFIRMED_LINEUP_PIT' else zero_effect('C3', 'EVIDENCE_GRADE_NOT_CONFIRMED_XI', uncertainty=ev_unc)
            c4 = c4_bench(vectors=vectors, home_team_id=str(ht), away_team_id=str(at), bench=packet.get('bench'), evidence_uncertainty=ev_unc)

            conflict = _suspension_ids(packet) & _packet_possible_ids(packet)
            if conflict and c2.active:
                conflict_n += 1
                c1 = zero_effect('C1', 'SOURCE_CONFLICT_SUSPENDED_PLAYER_IN_POSSIBLE_XI', uncertainty=min(2.0, ev_unc + 0.75))
                c2 = zero_effect('C2', 'SOURCE_CONFLICT_SUSPENDED_PLAYER_IN_POSSIBLE_XI', uncertainty=min(2.0, ev_unc + 0.75))

        for e in (c1, c2, c3, c4):
            comp_reasons[e.component][e.reason] += 1

        lineup = c3 if c3.active else c2
        c12, dedupe12 = deduplicated_c1_plus_lineup(c1, c2, grade=grade)
        if lineup.active:
            full_inputs = [lineup] + ([c4] if c4.active else [])
            full = combine_effects(full_inputs, grade=grade)
            dedupe_full = {'c1_absorbed_by_lineup_residual': bool(c1.active), 'lineup_component': lineup.component}
        else:
            full_inputs = ([c1] if c1.active else []) + ([c4] if c4.active else [])
            full = combine_effects(full_inputs, grade=grade)
            dedupe_full = {'c1_absorbed_by_lineup_residual': False, 'lineup_component': None}

        p_c1 = effect_prediction(base['score_matrix'], c1, lock, eng)
        p_c12 = effect_prediction(base['score_matrix'], c12, lock, eng)
        p_full = effect_prediction(base['score_matrix'], full, lock, eng)
        mass_supported = probability_mass_supported(packet)

        rows.append({
            'fixture_id': fid,
            'competition_id': o['competition_id'],
            'season': o['season'],
            'cutoff': cutoff,
            'home_team_id': o['home_team_id'],
            'away_team_id': o['away_team_id'],
            'shared_cold_start_bucket': bmap[fid].get('shared_cold_start_bucket'),
            'research_status': 'RESEARCH_ONLY_POST_VIEW_DIAGNOSTIC',
            'evidence_grade': grade,
            'baseline': base,
            'old_l1_l2': o['old_l1_l2'],
            'candidate_b': o['candidate_b_original'],
            'candidate_c1': p_c1,
            'candidate_c1_c2': p_c12,
            'candidate_c_full': p_full,
            'components': {'C1': c1.to_dict(), 'C2': c2.to_dict(), 'C3': c3.to_dict(), 'C4': c4.to_dict()},
            'c1_c2_effect': c12.to_dict(),
            'full_effect': full.to_dict(),
            'deduplication': {'c1_c2': dedupe12, 'full': dedupe_full},
            'probability_mass_supported': mass_supported,
            'probability_mass_redistribution_active': False,
            'uncertainty': _component_uncertainty(full) if full.active else ev_unc,
            'roster_packet_sha256': packet.get('packet_sha256'),
        })

        if packet.get('pit_legal') and grade == 'POSSIBLE_XI_PIT' and ht and at:
            known = str(packet['source']['available_at'])
            for tid, side in ((str(ht), 'home'), (str(at), 'away')):
                xs = (packet.get('predicted_lineups') or {}).get(side) or []
                if len(xs) == 11 and all(x.get('player_id') for x in xs):
                    roster_usage[tid].append({'players': [{'player_id': str(x['player_id']), 'started': True, 'appeared': None, 'minutes': None, 'role': 'UNK', 'known_at': known, 'reference_route': 'PREMATCH_POSSIBLE_XI'} for x in xs], 'known_at': known, 'match_id': f'candidate_c_pit:{fid}'})

    rmap = {r['fixture_id']: r for r in rows}
    rows = [rmap[fid] for fid in mechanical_ids]
    if len(rows) != SAMPLE_N:
        raise RuntimeError('Candidate C prediction row count mismatch')
    pp = out / 'candidate_c_predictions.jsonl'
    pp.write_text(''.join(json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n' for r in rows), encoding='utf-8')

    active = {k: sum(bool(r['components'][k]['active']) for r in rows) for k in ('C1', 'C2', 'C3', 'C4')}
    any_active = sum(any(bool(r['components'][k]['active']) for k in ('C1', 'C2', 'C3', 'C4')) for r in rows)
    max_active = max(active.values()) if active else 0
    feasible = max_active >= ACTIVATION_THRESHOLD
    moves = {m: statistics.fmean(cbd.meanmove(r[m], r['baseline']) for r in rows) for m in ('old_l1_l2', 'candidate_b', 'candidate_c1', 'candidate_c1_c2', 'candidate_c_full')}

    comparable = opposite = cancellation = 0
    cancellation_fractions: list[float] = []
    for r in rows:
        if not (r['components']['C1']['active'] and r['components']['C2']['active']):
            continue
        v1 = cbd.move(r['candidate_c1'], r['baseline'])
        v2 = cbd.move(r['candidate_c1_c2'], r['candidate_c1'])
        vf = cbd.move(r['candidate_c1_c2'], r['baseline'])
        if cbd.vnorm(v1) > 1e-14 and cbd.vnorm(v2) > 1e-14:
            comparable += 1
            if sum(a * b for a, b in zip(v1, v2)) < 0:
                opposite += 1
                if cbd.vnorm(vf) < cbd.vnorm(v1):
                    cancellation += 1
                    cancellation_fractions.append(1.0 - cbd.vnorm(vf) / cbd.vnorm(v1))

    grade_unc = {}
    for g in GRADE_ORDER:
        vals = [float(r['uncertainty']) for r in rows if r['evidence_grade'] == g]
        grade_unc[g] = {'n': len(vals), 'mean_uncertainty': None if not vals else statistics.fmean(vals)}
    present = [grade_unc[g]['mean_uncertainty'] for g in GRADE_ORDER if grade_unc[g]['n'] > 0]
    monotonic = all(float(present[i]) <= float(present[i + 1]) + 1e-12 for i in range(len(present) - 1)) if len(present) >= 2 else True

    pit_audit = json.load(open(pit / 'pit_roster_inventory_audit.json'))
    pre = {
        'schema_version': 'football3-context-translator-candidate-c-pre-score-v1',
        'status': 'RESEARCH_ONLY_POST_VIEW_DIAGNOSTIC',
        'formal_promotion_eligible': False,
        'formal_weight': 0,
        'labels_read_in_prediction_phase': False,
        'new_fixture_labels_read_n': 0,
        'inventory_n': SAMPLE_N,
        'mechanical_fixture_ids': mechanical_ids,
        'mechanical_fixture_set_sha256': canon(mechanical_ids),
        'frozen_pit_artifact_head': pm['head'],
        'frozen_pit_packet_sha256': pm['payload_sha256']['roster_packets'],
        'candidate_contract': candidate_contract(),
        'evidence_grade_counts': {g: int(evidence_counts.get(g, 0)) for g in GRADE_ORDER},
        'component_activation_n': active,
        'any_component_active_n': any_active,
        'overall_activation_rate': any_active / SAMPLE_N,
        'fallback_rate': (SAMPLE_N - any_active) / SAMPLE_N,
        'probability_mass_supported_n': sum(bool(r['probability_mass_supported']) for r in rows),
        'probability_mass_redistribution_active_n': 0,
        'activation_threshold_n': ACTIVATION_THRESHOLD,
        'max_component_activation_n': max_active,
        'feasible_for_post_view_scoring': feasible,
        'component_reasons': {k: dict(sorted(v.items())) for k, v in comp_reasons.items()},
        'player_identity_attempt_n': pit_audit['player_identity_attempt_n'],
        'player_identity_matched_n': pit_audit['player_identity_matched_n'],
        'player_identity_match_rate': pit_audit['player_identity_match_rate'],
        'historical_capability_lookup_attempt_n': ability_attempt_n,
        'historical_capability_available_n': ability_available_n,
        'historical_capability_coverage_rate': None if not ability_attempt_n else ability_available_n / ability_attempt_n,
        'mean_probability_move_vs_baseline': moves,
        'direction_and_cancellation': {
            'c1_c2_comparable_n': comparable,
            'opposite_direction_n': opposite,
            'opposite_direction_rate': None if not comparable else opposite / comparable,
            'post_dedupe_cancellation_n': cancellation,
            'mean_cancellation_fraction_when_present': None if not cancellation_fractions else statistics.fmean(cancellation_fractions),
            'double_encoding_guard': 'C1 absorbed whenever active C2/C3 already encodes current XI replacement state',
        },
        'uncertainty_by_evidence_grade': grade_unc,
        'uncertainty_monotonic_with_missing_evidence': monotonic,
        'source_conflict_n': conflict_n,
        'prediction_payload_sha256': sha(pp),
        'global_fixture_consumption_registry_extended': False,
    }
    dump(out / 'candidate_c_contract.json', candidate_contract())
    dump(out / 'candidate_c_pre_score.json', pre)
    gate_status = 'CANDIDATE_C_POST_VIEW_DIAGNOSTIC_PENDING_SCORE' if feasible else 'INSUFFICIENT_REALISTIC_PIT_ROSTER_COVERAGE'
    dump(out / 'candidate_c_gate.json', {
        'schema_version': 'football3-context-translator-candidate-c-gate-v1',
        'pipeline_integrity': 'PASS',
        'status': gate_status,
        'inventory_n': SAMPLE_N,
        'component_activation_n': active,
        'max_component_activation_n': max_active,
        'activation_threshold_n': ACTIVATION_THRESHOLD,
        'labels_read_in_prediction_phase': False,
        'new_fixture_labels_read_n': 0,
        'formal_promotion_eligible': False,
        'formal_weight': 0,
        'prediction_payload_sha256': pre['prediction_payload_sha256'],
    })
    return pre


def metric(rows: list[dict[str, Any]], labels: dict[str, dict[str, Any]], model: str) -> dict[str, Any]:
    return cbd.metrics([(r, labels[r['fixture_id']]) for r in rows], model)


def _subgroup(rows: list[dict[str, Any]], labels: dict[str, dict[str, Any]], fn) -> dict[str, Any]:
    rr = [r for r in rows if fn(r, labels[r['fixture_id']])]
    return {'n': len(rr), 'models': {m: metric(rr, labels, m) for m in MODELS}}


def score_phase(candidate_b: pathlib.Path, label_vault: pathlib.Path, out: pathlib.Path) -> dict[str, Any]:
    pre = json.load(open(out / 'candidate_c_pre_score.json'))
    if not pre['feasible_for_post_view_scoring'] or pre['max_component_activation_n'] < ACTIVATION_THRESHOLD:
        raise RuntimeError('Candidate C score invoked below activation threshold')
    pp = out / 'candidate_c_predictions.jsonl'
    if sha(pp) != pre['prediction_payload_sha256'] or pre['labels_read_in_prediction_phase']:
        raise RuntimeError('Candidate C prediction freeze mismatch before scoring')
    rows = readjl(pp)
    allowed272 = {str(x['fixture_id']) for x in readjl(candidate_b / 'candidate_b_predictions.jsonl')}
    if len(allowed272) != N_POOL:
        raise RuntimeError('Candidate B allowed POST_VIEW whitelist not 272')
    sample = {r['fixture_id'] for r in rows}
    if len(sample) != SAMPLE_N or not sample <= allowed272:
        raise RuntimeError('Candidate C attempted fixture outside existing GER1-272 POST_VIEW whitelist')
    labels272 = cbd.allowed_labels(label_vault, allowed272)
    labs = {k: v for k, v in labels272.items() if k in sample}
    if set(labs) != sample:
        raise RuntimeError('Candidate C sample label subset incomplete')
    for r in rows:
        if si._dt(r['cutoff'], 'cutoff') != si._dt(labs[r['fixture_id']]['cutoff'], 'label_cutoff'):
            raise RuntimeError('Candidate C cutoff/label identity mismatch')

    overall = {m: metric(rows, labs, m) for m in MODELS}
    b = overall['baseline']
    delta = {m: {k: overall[m][k] - b[k] for k in ('logloss', 'brier', 'rps', 'top1')} for m in MODELS if m != 'baseline'}
    groups: dict[str, Any] = {}
    groups['actual_draw'] = _subgroup(rows, labs, lambda r, l: cbd.outcome(l) == 'draw')
    groups['weak_team_win'] = _subgroup(rows, labs, lambda r, l: cbd.outcome(l) in {'home', 'away'} and cbd.outcome(l) == ('home' if float(r['baseline']['p_home']) < float(r['baseline']['p_away']) else 'away'))
    for y in OUTCOMES:
        groups['actual_' + y] = _subgroup(rows, labs, lambda r, l, y=y: cbd.outcome(l) == y)
    for bucket in ('established', 'sparse', 'zero'):
        groups['cold_start_' + bucket] = _subgroup(rows, labs, lambda r, l, b=bucket: str(r.get('shared_cold_start_bucket')) == b)
    for comp in ('C1', 'C2', 'C3', 'C4'):
        groups[comp.lower() + '_active'] = _subgroup(rows, labs, lambda r, l, c=comp: bool(r['components'][c]['active']))
    for grade in GRADE_ORDER:
        groups['evidence_' + grade.lower()] = _subgroup(rows, labs, lambda r, l, g=grade: r['evidence_grade'] == g)

    team_counts = Counter()
    for r in rows:
        team_counts[str(r['home_team_id'])] += 1
        team_counts[str(r['away_team_id'])] += 1
    major = [tid for tid, n in sorted(team_counts.items(), key=lambda x: (-x[1], x[0])) if n >= 8][:8]
    for tid in major:
        groups['team_' + tid] = _subgroup(rows, labs, lambda r, l, tid=tid: str(r['home_team_id']) == tid or str(r['away_team_id']) == tid)

    result = {
        'schema_version': 'football3-context-translator-candidate-c-post-view-diagnostic-v1',
        'status': 'CANDIDATE_C_POST_VIEW_DIAGNOSTIC_COMPLETE',
        'scientific_claim': 'RESEARCH_ONLY_POST_VIEW_DIAGNOSTIC_NOT_BLIND_NOT_CONFIRMATION_NOT_FORMAL_EVIDENCE',
        'research_only': True,
        'formal_promotion_eligible': False,
        'formal_weight': 0,
        'n': SAMPLE_N,
        'already_unsealed_ger1_272_whitelist_n': N_POOL,
        'labels_parsed_only_for_existing_ger1_272_whitelist': True,
        'new_fixture_labels_read_n': 0,
        'models': overall,
        'delta_vs_protected_v2': delta,
        'operations': {
            'evidence_grade_counts': pre['evidence_grade_counts'],
            'component_activation_n': pre['component_activation_n'],
            'overall_activation_rate': pre['overall_activation_rate'],
            'fallback_rate': pre['fallback_rate'],
            'player_identity_match_rate': pre['player_identity_match_rate'],
            'historical_capability_coverage_rate': pre['historical_capability_coverage_rate'],
            'mean_probability_move_vs_baseline': pre['mean_probability_move_vs_baseline'],
            'direction_and_cancellation': pre['direction_and_cancellation'],
            'uncertainty_by_evidence_grade': pre['uncertainty_by_evidence_grade'],
            'uncertainty_monotonic_with_missing_evidence': pre['uncertainty_monotonic_with_missing_evidence'],
            'probability_mass_supported_n': pre['probability_mass_supported_n'],
            'probability_mass_redistribution_active_n': pre['probability_mass_redistribution_active_n'],
        },
        'subgroups': groups,
        'major_team_group_rule': 'team appearance count >=8 in mechanical first-100, sorted count desc then team_id, max 8; never selected by result/model performance',
        'major_team_ids': major,
        'protected_v2_modified': False,
        'main_modified': False,
        'current_modified': False,
        'airtable_modified': False,
        'pr334_modified': False,
        'r5_modified': False,
        'global_fixture_consumption_registry_extended': False,
        'formal_enablement': False,
        'promotion_decision': 'NOT_REQUESTED_AND_NOT_PERMITTED',
    }
    sp = out / 'candidate_c_score.json'
    dump(sp, result)
    gate = json.load(open(out / 'candidate_c_gate.json'))
    gate.update({'status': 'CANDIDATE_C_POST_VIEW_DIAGNOSTIC_COMPLETE', 'score_payload_sha256': sha(sp), 'new_fixture_labels_read_n': 0})
    dump(out / 'candidate_c_gate.json', gate)
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest='cmd', required=True)
    p = sp.add_parser('predict')
    p.add_argument('--v2', type=pathlib.Path, required=True)
    p.add_argument('--candidate-b', type=pathlib.Path, required=True)
    p.add_argument('--pit', type=pathlib.Path, required=True)
    p.add_argument('--source', type=pathlib.Path, required=True)
    p.add_argument('--out', type=pathlib.Path, required=True)
    s = sp.add_parser('score')
    s.add_argument('--candidate-b', type=pathlib.Path, required=True)
    s.add_argument('--label-vault', type=pathlib.Path, required=True)
    s.add_argument('--out', type=pathlib.Path, required=True)
    a = ap.parse_args()
    obj = prediction_phase(a.v2, a.candidate_b, a.pit, a.source, a.out) if a.cmd == 'predict' else score_phase(a.candidate_b, a.label_vault, a.out)
    print(json.dumps(obj, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
