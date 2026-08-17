from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import statistics
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo

HF = Path('/tmp/hf-football-odds-2023-24/match_odds.csv')
OUT = Path('football-data/manifests/hf_odds_pit_matchability_r1.json')
ROWS_OUT = Path('football-data/manifests/hf_odds_pit_fixed500_r1.csv')

COMP = {
    'PREMIER LEAGUE': ('ENG_PremierLeague', Path('football-data/processed/ENG_PremierLeague/2023-24.csv'), 'Europe/London'),
    'LA LIGA': ('ESP_LaLiga', Path('football-data/processed/ESP_LaLiga/2023-24.csv'), 'Europe/Madrid'),
    'LALIGA': ('ESP_LaLiga', Path('football-data/processed/ESP_LaLiga/2023-24.csv'), 'Europe/Madrid'),
    'SERIE A': ('ITA_SerieA', Path('football-data/processed/ITA_SerieA/2023-24.csv'), 'Europe/Rome'),
    'BUNDESLIGA': ('GER_Bundesliga', Path('football-data/processed/GER_Bundesliga/2023-24.csv'), 'Europe/Berlin'),
    'LIGUE 1': ('FRA_Ligue1', Path('football-data/processed/FRA_Ligue1/2023-24.csv'), 'Europe/Paris'),
}
TARGETS = [360, 90, 15, 5]
HF_ZONE = ZoneInfo('Europe/London')


def norm(s: str) -> str:
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode('ascii').lower()
    s = s.replace('&', ' and ')
    s = re.sub(r"[^a-z0-9]+", ' ', s)
    toks = [t for t in s.split() if t not in {'fc', 'cf', 'afc', 'ssc', 'calcio', 'club'}]
    aliases = {
        'manchester city': 'man city',
        'manchester united': 'man united',
        'nottingham forest': 'nottm forest',
        'wolverhampton wanderers': 'wolves',
        'wolverhampton': 'wolves',
        'tottenham hotspur': 'tottenham',
        'newcastle united': 'newcastle',
        'west ham united': 'west ham',
        'sheffield united': 'sheffield utd',
        'brighton hove albion': 'brighton',
        'paris saint germain': 'paris sg',
        'paris saint germain psg': 'paris sg',
        'borussia monchengladbach': 'mgladbach',
        'borussia m gladbach': 'mgladbach',
        'monchengladbach': 'mgladbach',
        'atletico madrid': 'ath madrid',
        'athletic bilbao': 'ath bilbao',
        'inter milan': 'inter',
        'internazionale': 'inter',
        'hellas verona': 'verona',
        'bayern munich': 'bayern munich',
        'bayern monaco': 'bayern munich',
        'koln': 'fc koln',
        'cologne': 'fc koln',
    }
    z = ' '.join(toks)
    return aliases.get(z, z)


def sim(a: str, b: str) -> float:
    na, nb = norm(a), norm(b)
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def parse_repo_dt(date_s: str, time_s: str, tz_name: str) -> datetime:
    d = datetime.strptime(date_s.strip(), '%d/%m/%Y')
    hhmm = datetime.strptime(time_s.strip(), '%H:%M').time()
    return datetime.combine(d.date(), hhmm, tzinfo=ZoneInfo(tz_name)).astimezone(timezone.utc)


def parse_hf_naive(s: str) -> datetime:
    return datetime.fromisoformat((s or '').strip())


def parse_hf_utc(s: str) -> datetime:
    return parse_hf_naive(s).replace(tzinfo=HF_ZONE).astimezone(timezone.utc)


def quantile(xs, q):
    if not xs:
        return None
    ys = sorted(xs)
    pos = (len(ys) - 1) * q
    lo = math.floor(pos); hi = math.ceil(pos)
    if lo == hi:
        return ys[lo]
    return ys[lo] * (hi - pos) + ys[hi] * (pos - lo)


fixtures_by_comp = defaultdict(list)
fixture_by_pair = {}
for hf_comp, (league_id, path, tz_name) in COMP.items():
    if not path.exists():
        continue
    with path.open('r', encoding='utf-8-sig', newline='', errors='replace') as f:
        for row in csv.DictReader(f):
            if not row.get('Date') or not row.get('Time'):
                continue
            try:
                ko = parse_repo_dt(row['Date'], row['Time'], tz_name)
            except Exception:
                continue
            rec = {
                'league_id': league_id,
                'repo_home': row['HomeTeam'],
                'repo_away': row['AwayTeam'],
                'kickoff_utc': ko,
                'fthg': int(float(row['FTHG'])),
                'ftag': int(float(row['FTAG'])),
            }
            fixtures_by_comp[league_id].append(rec)
            fixture_by_pair[(league_id, norm(row['HomeTeam']), norm(row['AwayTeam']))] = rec

# Pass 1: unique HF match identities.
hf_matches = {}
competition_rows = defaultdict(int)
with HF.open('r', encoding='utf-8-sig', newline='', errors='replace') as f:
    for row in csv.DictReader(f):
        hc = (row.get('competition') or '').strip().upper()
        competition_rows[hc] += 1
        if hc not in COMP:
            continue
        key = (hc, row.get('home_team','').strip(), row.get('away_team','').strip(), row.get('refreshed_at','').strip())
        hf_matches.setdefault(key, None)

matched = {}
unmatched = []
alignment_seconds = []
match_scores = []
for key in hf_matches:
    hc, hh, ha, refreshed = key
    league_id, _, _ = COMP[hc]
    # Fast exact-normalized pair.
    rec = fixture_by_pair.get((league_id, norm(hh), norm(ha)))
    best_score = 2.0 if rec else -1.0
    if rec is None:
        try:
            rd = parse_hf_naive(refreshed).date()
        except Exception:
            rd = None
        candidates = fixtures_by_comp[league_id]
        if rd:
            candidates = [x for x in candidates if abs((x['kickoff_utc'].astimezone(HF_ZONE).date() - rd).days) <= 1]
        scored = []
        for x in candidates:
            score = sim(hh, x['repo_home']) + sim(ha, x['repo_away'])
            scored.append((score, x))
        scored.sort(key=lambda z: z[0], reverse=True)
        if scored:
            best_score, rec = scored[0]
            second = scored[1][0] if len(scored) > 1 else -1
            if best_score < 1.35 or best_score - second < 0.08:
                rec = None
    if rec is None:
        unmatched.append({'competition': hc, 'home': hh, 'away': ha, 'refreshed_at': refreshed, 'best_score_sum': best_score})
        continue
    matched[key] = rec
    match_scores.append(best_score)
    try:
        ref_utc = parse_hf_utc(refreshed)
        alignment_seconds.append((ref_utc - rec['kickoff_utc']).total_seconds())
    except Exception:
        pass

# Pass 2: nearest valid OU quote at/before each cutoff.
snaps = {key: {m: None for m in TARGETS} for key in matched}
valid_ou_rows = 0
post_kickoff_ou_rows = 0
with HF.open('r', encoding='utf-8-sig', newline='', errors='replace') as f:
    for row in csv.DictReader(f):
        hc = (row.get('competition') or '').strip().upper()
        if hc not in COMP:
            continue
        key = (hc, row.get('home_team','').strip(), row.get('away_team','').strip(), row.get('refreshed_at','').strip())
        rec = matched.get(key)
        if rec is None:
            continue
        try:
            ts = parse_hf_utc(row.get('U/O 2.5 timestamp',''))
            over = float(row.get('odds_over_2.5',''))
            under = float(row.get('odds_under_2.5',''))
            if over <= 1 or under <= 1:
                continue
        except Exception:
            continue
        valid_ou_rows += 1
        if ts >= rec['kickoff_utc']:
            post_kickoff_ou_rows += 1
        for mins in TARGETS:
            cutoff = rec['kickoff_utc'] - timedelta(minutes=mins)
            if ts <= cutoff:
                cur = snaps[key][mins]
                if cur is None or ts > cur['ts']:
                    snaps[key][mins] = {'ts': ts, 'over': over, 'under': under, 'age_min': (cutoff-ts).total_seconds()/60.0}

coverage = {}
for mins in TARGETS:
    ages = [v[mins]['age_min'] for v in snaps.values() if v[mins] is not None]
    coverage[str(mins)] = {
        'n': len(ages),
        'rate_of_matched': len(ages)/len(matched) if matched else 0,
        'age_min_p50': quantile(ages, .5),
        'age_min_p90': quantile(ages, .9),
        'age_min_p95': quantile(ages, .95),
        'within_15m': sum(x <= 15 for x in ages),
        'within_30m': sum(x <= 30 for x in ages),
        'within_60m': sum(x <= 60 for x in ages),
    }

all4 = []
for key, rec in matched.items():
    ss = snaps[key]
    if not all(ss[m] is not None for m in TARGETS):
        continue
    distinct = len({ss[m]['ts'] for m in TARGETS})
    ident = f"{rec['league_id']}|{rec['kickoff_utc'].isoformat()}|{rec['repo_home']}|{rec['repo_away']}"
    all4.append((hashlib.sha256(ident.encode()).hexdigest(), key, rec, ss, distinct))
all4.sort(key=lambda x: x[0])

quality_counts = {
    'all4_any_age': len(all4),
    'all4_distinct_at_least_2': sum(x[4] >= 2 for x in all4),
    'all4_distinct_at_least_3': sum(x[4] >= 3 for x in all4),
    'all4_t5_age_le_15m': sum(x[3][5]['age_min'] <= 15 for x in all4),
    'all4_t5_age_le_30m': sum(x[3][5]['age_min'] <= 30 for x in all4),
    'all4_t5_age_le_60m': sum(x[3][5]['age_min'] <= 60 for x in all4),
    'all4_distinct_ge3_t5_age_le30m': sum(x[4] >= 3 and x[3][5]['age_min'] <= 30 for x in all4),
    'all4_distinct_ge3_t5_age_le60m': sum(x[4] >= 3 and x[3][5]['age_min'] <= 60 for x in all4),
}

# Deterministic 500: prefer >=3 distinct target snapshots and a T-5 quote no older than 60m.
eligible = [x for x in all4 if x[4] >= 3 and x[3][5]['age_min'] <= 60]
selected = eligible[:500] if len(eligible) >= 500 else []
OUT.parent.mkdir(parents=True, exist_ok=True)
if selected:
    with ROWS_OUT.open('w', encoding='utf-8', newline='') as f:
        cols = ['sha256_rank','league_id','kickoff_utc','home_team','away_team','fthg','ftag','total_goals','distinct_snapshot_times']
        for mins in TARGETS:
            cols += [f't{mins}_quote_utc', f't{mins}_over25', f't{mins}_under25', f't{mins}_age_min']
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for sha, key, rec, ss, distinct in selected:
            z = {
                'sha256_rank': sha,
                'league_id': rec['league_id'],
                'kickoff_utc': rec['kickoff_utc'].isoformat(),
                'home_team': rec['repo_home'], 'away_team': rec['repo_away'],
                'fthg': rec['fthg'], 'ftag': rec['ftag'], 'total_goals': rec['fthg']+rec['ftag'],
                'distinct_snapshot_times': distinct,
            }
            for mins in TARGETS:
                q = ss[mins]
                z[f't{mins}_quote_utc'] = q['ts'].isoformat()
                z[f't{mins}_over25'] = q['over']
                z[f't{mins}_under25'] = q['under']
                z[f't{mins}_age_min'] = round(q['age_min'], 3)
            w.writerow(z)

align_abs = [abs(x) for x in alignment_seconds]
summary = {
    'schema_version': 'hf-odds-pit-matchability-r1',
    'status': 'PASS_FIXED500_READY' if len(selected) == 500 else 'INSUFFICIENT_HIGH_QUALITY_FIXED500',
    'source_rows_approx': 1956225,
    'source_competition_rows': dict(sorted(competition_rows.items())),
    'hf_unique_top5_match_keys': len(hf_matches),
    'repo_fixture_counts': {k: len(v) for k,v in fixtures_by_comp.items()},
    'matched_match_keys': len(matched),
    'unmatched_match_keys': len(unmatched),
    'unmatched_examples': unmatched[:30],
    'name_match_score_sum_p05': quantile(match_scores, .05),
    'timestamp_clock_assumption': 'Europe/London',
    'refreshed_at_minus_repo_kickoff_seconds': {
        'n': len(alignment_seconds),
        'median': quantile(alignment_seconds, .5),
        'p05': quantile(alignment_seconds, .05),
        'p95': quantile(alignment_seconds, .95),
        'abs_p95': quantile(align_abs, .95),
    },
    'valid_ou_rows_on_matched_fixtures': valid_ou_rows,
    'ou_rows_at_or_after_repo_kickoff': post_kickoff_ou_rows,
    'cutoff_coverage': coverage,
    'quality_counts': quality_counts,
    'selected_fixed500_n': len(selected),
    'selected_rule': 'all four cutoffs available; >=3 distinct OU quote timestamps across T-360/T-90/T-15/T-5; T-5 selected quote age <=60 minutes; deterministic SHA256 ordering',
    'interpretation_guard': {
        'all_selected_quotes_must_be_at_or_before_their_cutoff': True,
        'post_kickoff_quotes_never_selected': True,
        'result_labels_not_used_for_selection': True,
        'formal_weight': 0,
        'main_mutation': False,
        'formal_model_mutation': False,
    },
}
OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False, indent=2))
if len(selected) != 500:
    raise SystemExit('high-quality strict-PIT fixed500 not yet available under this gate')
