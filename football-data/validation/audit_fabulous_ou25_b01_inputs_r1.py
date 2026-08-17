#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import statistics
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pyarrow.parquet as pq
from rapidfuzz.fuzz import ratio
from unidecode import unidecode

SRC = Path('/tmp/fab/match_odds.csv')
FIXTURES = Path('/tmp/fab/fixtures.parquet')
TEAMS = Path('/tmp/fab/teams.parquet')
OUTDIR = Path('football-data/research/anonymous_data_reserve_r1/fabulous_ou25_b01_input_audit_r1')
OUTDIR.mkdir(parents=True, exist_ok=True)

SRC_SHA = 'c0e8854302159e1a8c529463f33280b728909c5e0ba95262515a7a144a43aa2a'
FIX_SHA = '7ba90661dbed29eb940daf5ea385c7d76d5751d16be86bd9063293a982abc7b7'
TEAM_SHA = '5529282b37ad51437142dd7c6d32fb60bbbdd56953dc432e3b9247dca17a2fa5'
EXPECTED_B01_SHA = 'fcba07147d230357925d3ee41027dfae18a27654960641c509ead5626b057baf'
SOURCE_REVISION = '211feb35f9dcd270bd7a1b27b39a8b1f45f239aa'
SEED = 'FABULOUS-OU25-PIT-RESERVE-20260817-R1'
CUTOFFS_H = [24, 12, 6, 1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dt(x):
    if x is None:
        return None
    if isinstance(x, datetime):
        return x.replace(tzinfo=timezone.utc) if x.tzinfo is None else x.astimezone(timezone.utc)
    s = str(x).strip().replace('Z', '+00:00')
    try:
        z = datetime.fromisoformat(s)
    except Exception:
        try:
            z = datetime.strptime(s, '%Y-%m-%d %H:%M:%S.%f')
        except Exception:
            try:
                z = datetime.strptime(s, '%Y-%m-%d %H:%M:%S')
            except Exception:
                return None
    return z.replace(tzinfo=timezone.utc) if z.tzinfo is None else z.astimezone(timezone.utc)


def norm(s):
    s = unidecode(str(s or '')).lower()
    s = re.sub(r'\b(fc|cf|afc|ac|ssc|calcio|club|football|futbol|deportivo|sporting)\b', ' ', s)
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    return ' '.join(s.split())


alias = {
    'lipsia': 'rb leipzig', 'colonia': 'koln', 'stoccarda': 'vfb stuttgart', 'friburgo': 'sc freiburg',
    'eintracht francofort': 'eintracht frankfurt', 'monchengladbach': 'borussia monchengladbach',
    'bayern': 'bayern munich', 'dortmund': 'borussia dortmund', 'leverkusen': 'bayer leverkusen',
    'union berlino': 'union berlin', 'paris saint germain': 'paris saint germain', 'milan': 'ac milan',
    'inter': 'inter milan', 'roma': 'as roma', 'napoli': 'ssc napoli', 'verona': 'hellas verona',
    'atletico madrid': 'atletico madrid', 'barcellona': 'barcelona', 'real sociedad': 'real sociedad',
    'athletic bilbao': 'athletic club', 'betis': 'real betis', 'siviglia': 'sevilla',
    'girona': 'girona', 'alaves': 'alaves', 'cadice': 'cadiz', 'maiorka': 'mallorca',
    'psg': 'paris saint germain',
}
alias = {norm(k): norm(v) for k, v in alias.items()}


def canon(s):
    n = norm(s)
    return alias.get(n, n)


def valid_price(x):
    try:
        v = float(x)
        return math.isfinite(v) and v > 1.0
    except Exception:
        return False


def de_vig_ou(over, under):
    io, iu = 1.0 / float(over), 1.0 / float(under)
    return io / (io + iu)


def de_vig_hda(h, d, a):
    inv = [1.0 / float(h), 1.0 / float(d), 1.0 / float(a)]
    s = sum(inv)
    return [x / s for x in inv]


def stat(values):
    values = sorted(float(x) for x in values)
    if not values:
        return None
    return {
        'min': values[0],
        'p10': values[int(0.10 * (len(values) - 1))],
        'median': statistics.median(values),
        'p90': values[int(0.90 * (len(values) - 1))],
        'max': values[-1],
    }


assert sha256(SRC) == SRC_SHA
assert sha256(FIXTURES) == FIX_SHA
assert sha256(TEAMS) == TEAM_SHA

# Rebuild the original reserve using only pre-match market identity/timestamps.
groups = {}
with SRC.open('r', encoding='utf-8-sig', newline='') as f:
    r = csv.DictReader(f)
    forbidden = {'score', 'result', 'winner', 'home_goals', 'away_goals', 'fthg', 'ftag', 'settlement'}
    assert not (set(x.lower() for x in (r.fieldnames or [])) & forbidden)
    for row in r:
        h = row.get('home_team', '').strip()
        a = row.get('away_team', '').strip()
        c = row.get('competition', '').strip()
        ts = dt(row.get('U/O 2.5 timestamp'))
        if not (h and a and c and ts and valid_price(row.get('odds_under_2.5')) and valid_price(row.get('odds_over_2.5'))):
            continue
        key = (h, a, c)
        g = groups.setdefault(key, {'home': h, 'away': a, 'competition': c, 'timestamps': set(), 'rows': 0})
        g['timestamps'].add(ts)
        g['rows'] += 1
for g in groups.values():
    ss = sorted(g['timestamps'])
    g['min_ts'] = ss[0]
    g['max_ts'] = ss[-1]
    g['n_ts'] = len(ss)

# Team dictionary and fixture identity/time only. No goals/results are read.
tpf = pq.ParquetFile(TEAMS)
tcols = tpf.schema.names
idc = 'id' if 'id' in tcols else ('team_id' if 'team_id' in tcols else None)
namec = 'name' if 'name' in tcols else ('team_name' if 'team_name' in tcols else None)
assert idc and namec
tt = pq.read_table(TEAMS, columns=[idc, namec])
team_name = {int(i): str(n) for i, n in zip(tt[idc].to_pylist(), tt[namec].to_pylist()) if i is not None and n is not None}
fixture_fields = ['id', 'date_utc', 'is_played', 'league_id', 'home_team_id', 'away_team_id']
ft = pq.read_table(FIXTURES, columns=fixture_fields)
fixtures = []
for fid, kick, played, lid, hid, aid in zip(*(ft[c].to_pylist() for c in fixture_fields)):
    if not played or fid is None or hid is None or aid is None:
        continue
    k = dt(kick)
    if k is None or not (datetime(2023, 7, 1, tzinfo=timezone.utc) <= k <= datetime(2024, 6, 30, 23, 59, 59, tzinfo=timezone.utc)):
        continue
    hn, an = team_name.get(int(hid)), team_name.get(int(aid))
    if not hn or not an:
        continue
    fixtures.append({'id': int(fid), 'kickoff': k, 'home': hn, 'away': an, 'home_n': canon(hn), 'away_n': canon(an), 'league_id': lid})

matched, ambiguous, unmatched = [], [], []
for g in groups.values():
    if g['n_ts'] < 2:
        continue
    sh, sa, mx = canon(g['home']), canon(g['away']), g['max_ts']
    cand = []
    for fx in fixtures:
        lead = (fx['kickoff'] - mx).total_seconds() / 3600.0
        if lead < 0.25 or lead > 336:
            continue
        hs, aas = ratio(sh, fx['home_n']), ratio(sa, fx['away_n'])
        if hs < 68 or aas < 68:
            continue
        pair = (hs + aas) / 2.0
        score = pair - min(20, lead / 48.0)
        cand.append((score, pair, lead, fx))
    cand.sort(key=lambda x: x[0], reverse=True)
    if not cand:
        unmatched.append(g)
        continue
    best = cand[0]
    if best[1] < 78:
        unmatched.append(g)
        continue
    if len(cand) > 1 and best[0] - cand[1][0] < 8:
        ambiguous.append(g)
        continue
    fx = best[3]
    first_lead = (fx['kickoff'] - g['min_ts']).total_seconds() / 3600.0
    last_lead = (fx['kickoff'] - g['max_ts']).total_seconds() / 3600.0
    if last_lead < 0.25:
        continue
    matched.append({
        'fixture_id': fx['id'], 'source_home': g['home'], 'source_away': g['away'],
        'fixture_home': fx['home'], 'fixture_away': fx['away'], 'competition': g['competition'],
        'kickoff_utc': fx['kickoff'].isoformat(), 'first_quote_hours_before': first_lead,
        'last_quote_hours_before': last_lead, 'distinct_ou25_timestamps': g['n_ts'],
        'source_rows': g['rows'], 'pair_similarity': best[1],
    })

byfid = defaultdict(list)
for m in matched:
    byfid[m['fixture_id']].append(m)
dup_fids = {fid for fid, rows in byfid.items() if len(rows) > 1}
matched = [m for m in matched if m['fixture_id'] not in dup_fids]
reserve = [m for m in matched if m['distinct_ou25_timestamps'] >= 2 and m['last_quote_hours_before'] >= 1.0]
reserve = sorted(reserve, key=lambda m: hashlib.sha256(f"{SEED}|{m['fixture_id']}".encode()).hexdigest())
b01 = reserve[:400]
assert len(reserve) == 1559
assert len(b01) == 400

payload = {
    'schema_version': 'FAB-OU25-PIT-BATCH-R1', 'batch_id': 'FAB-OU25-PIT-B01', 'status': 'SEALED_UNOPENED',
    'batch_size': 400, 'source_revision': SOURCE_REVISION, 'source_csv_sha256': SRC_SHA,
    'selection_uses_outcomes': False, 'outcome_values_dereferenced': 0,
    'pit_gate': 'unique zero-label team-pair/kickoff join; >=2 distinct OU2.5 timestamps; last timestamp >=1h before kickoff',
    'matches': b01,
}
raw_manifest = (json.dumps(payload, ensure_ascii=False, indent=2) + '\n').encode()
reconstructed_b01_sha = hashlib.sha256(raw_manifest).hexdigest()
assert reconstructed_b01_sha == EXPECTED_B01_SHA, (reconstructed_b01_sha, EXPECTED_B01_SHA)

# Collect only market values/timestamps for the already sealed B01 identities.
selected = {(m['source_home'], m['source_away'], m['competition']): m for m in b01}
quotes = {k: {'ou': {}, 'hda': {}} for k in selected}
with SRC.open('r', encoding='utf-8-sig', newline='') as f:
    r = csv.DictReader(f)
    for row in r:
        key = (row.get('home_team', '').strip(), row.get('away_team', '').strip(), row.get('competition', '').strip())
        if key not in selected:
            continue
        uts = dt(row.get('U/O 2.5 timestamp'))
        if uts and valid_price(row.get('odds_over_2.5')) and valid_price(row.get('odds_under_2.5')):
            quotes[key]['ou'][uts] = (float(row['odds_over_2.5']), float(row['odds_under_2.5']))
        hts = dt(row.get('1X2 timestamp'))
        if hts and all(valid_price(row.get(c)) for c in ('odds_1', 'odds_X', 'odds_2')):
            quotes[key]['hda'][hts] = (float(row['odds_1']), float(row['odds_X']), float(row['odds_2']))

packets = []
coverage = {}
for cutoff_h in CUTOFFS_H:
    complete = 0
    ou_leads, hda_leads, market_ts_gap = [], [], []
    for key, m in selected.items():
        kickoff = dt(m['kickoff_utc'])
        cutoff = kickoff - timedelta(hours=cutoff_h)
        ou_eligible = [(t, v) for t, v in quotes[key]['ou'].items() if t <= cutoff]
        hda_eligible = [(t, v) for t, v in quotes[key]['hda'].items() if t <= cutoff]
        if not ou_eligible or not hda_eligible:
            continue
        ou_t, (over, under) = max(ou_eligible, key=lambda x: x[0])
        hda_t, (oh, od, oa) = max(hda_eligible, key=lambda x: x[0])
        pou = de_vig_ou(over, under)
        ph, pd, pa = de_vig_hda(oh, od, oa)
        ou_lead = (kickoff - ou_t).total_seconds() / 3600.0
        hda_lead = (kickoff - hda_t).total_seconds() / 3600.0
        complete += 1
        ou_leads.append(ou_lead)
        hda_leads.append(hda_lead)
        market_ts_gap.append(abs((ou_t - hda_t).total_seconds()) / 3600.0)
        packets.append({
            'fixture_id': m['fixture_id'], 'batch_id': 'FAB-OU25-PIT-B01', 'cutoff_hours': cutoff_h,
            'kickoff_utc': m['kickoff_utc'], 'competition': m['competition'],
            'ou_timestamp_utc': ou_t.isoformat(), 'hda_timestamp_utc': hda_t.isoformat(),
            'ou_lead_hours': ou_lead, 'hda_lead_hours': hda_lead,
            'p_over25_devig': pou, 'p_home_devig': ph, 'p_draw_devig': pd, 'p_away_devig': pa,
        })
    coverage[str(cutoff_h)] = {
        'complete_both_markets': complete,
        'coverage_rate': complete / 400.0,
        'ou_selected_lead_hours': stat(ou_leads),
        'hda_selected_lead_hours': stat(hda_leads),
        'abs_ou_vs_hda_timestamp_gap_hours': stat(market_ts_gap),
    }

packet_path = OUTDIR / 'B01_pre_match_inputs_all_cutoffs.json'
packet_payload = {
    'schema_version': 'FAB-OU25-B01-PRE-MATCH-INPUTS-R1',
    'batch_id': 'FAB-OU25-PIT-B01',
    'reconstructed_batch_manifest_sha256': reconstructed_b01_sha,
    'cutoffs_hours_before_kickoff': CUTOFFS_H,
    'selection_uses_outcomes': False,
    'outcome_values_dereferenced': 0,
    'fields': ['fixture_id', 'kickoff_utc', 'competition', 'market timestamps', 'de-vigged 1X2 probabilities', 'de-vigged OU2.5 over probability'],
    'rows': packets,
}
packet_path.write_text(json.dumps(packet_payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

summary = {
    'schema_version': 'FAB-OU25-B01-INPUT-AUDIT-R1',
    'status': 'ZERO_LABEL_COMPLETE',
    'source_sha256': SRC_SHA,
    'fixtures_sha256': FIX_SHA,
    'teams_sha256': TEAM_SHA,
    'reserve_reconstructed_n': len(reserve),
    'b01_n': len(b01),
    'b01_manifest_sha256_expected': EXPECTED_B01_SHA,
    'b01_manifest_sha256_reconstructed': reconstructed_b01_sha,
    'b01_hash_match': reconstructed_b01_sha == EXPECTED_B01_SHA,
    'coverage': coverage,
    'pre_match_packet_sha256': sha256(packet_path),
    'target_labels_accessed': 0,
    'target_outcome_columns_read': [],
    'fixture_fields_read': fixture_fields,
    'model_fits': 0,
    'scoring': 0,
    'threshold_tuning': 0,
    'effect_test_authorized': False,
}
(OUTDIR / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False, indent=2))
