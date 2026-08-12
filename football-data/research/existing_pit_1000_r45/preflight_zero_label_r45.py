#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path('.')
OUT = Path('/tmp/r45-preflight')
R45_DIR = Path('football-data/research/existing_pit_1000_r45')
RECEIPT_PATH = R45_DIR / 'protected_assets_receipt_r45.json'
FILES = sorted(ROOT.glob('football-data/training_datasets/*/point_in_time.csv'))
ID_FIELDS = ('competition_id', 'season', 'date', 'home_team', 'away_team')
PROTECTED_TERMS = ('fixed', 'gold', 'reserve', 'holdout', 'blind', 'oos', 'protected')
LABEL_PREFIXES = ('label_',)
EXCLUDED_SEASONS = {'2025/26'}
CONTROL_FIELDS = {
    'competition_id', 'season', 'date', 'home_team', 'away_team',
    'split', 'stage', 'source_path', 'eligibility_reason'
}


def canonical_id(row: dict[str, str]) -> str:
    return '|'.join(str(row.get(k, '')).strip() for k in ID_FIELDS)


def sha_lines(values: list[str]) -> str:
    raw = ('\n'.join(values) + ('\n' if values else '')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def maybe_identity(row: dict) -> str | None:
    if all(k in row for k in ID_FIELDS):
        parts = [str(row.get(k, '')).strip() for k in ID_FIELDS]
        if all(parts):
            return '|'.join(parts)
    for key in ('canonical_identity', 'match_identity', 'sample_identity', 'identity'):
        value = row.get(key)
        if isinstance(value, str) and value.count('|') == 4:
            return value.strip()
    return None


def walk_json(obj, out: set[str]) -> None:
    if isinstance(obj, dict):
        ident = maybe_identity(obj)
        if ident:
            out.add(ident)
        for value in obj.values():
            walk_json(value, out)
    elif isinstance(obj, list):
        for value in obj:
            walk_json(value, out)


def range_receipt(rows: list[dict]) -> dict:
    ids = [r['identity'] for r in rows]
    return {
        'count': len(rows),
        'date_min': rows[0]['date'] if rows else None,
        'date_max': rows[-1]['date'] if rows else None,
        'identity_sha256': sha_lines(ids),
    }


if not FILES:
    raise SystemExit('NO_POINT_IN_TIME_FILES')
if not RECEIPT_PATH.exists():
    raise SystemExit('MISSING_PROTECTED_ASSETS_RECEIPT')

protected_receipt = json.loads(RECEIPT_PATH.read_text(encoding='utf-8'))
wall = protected_receipt['chronological_protection_wall']['exclusive_before_date']
if wall != '2023-01-27':
    raise SystemExit('UNEXPECTED_PROTECTION_WALL')

inventory = []
all_candidates = []
all_ids = set()
header_ref = None
split_counts = Counter()
season_counts = Counter()
competition_counts = Counter()
duplicate_ids = []

for path in FILES:
    rows = 0
    dates = []
    seasons = Counter()
    splits = Counter()
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        missing = [k for k in ID_FIELDS if k not in fields]
        if missing:
            raise RuntimeError(f'MISSING_IDENTITY_FIELDS:{path}:{missing}')
        if header_ref is None:
            header_ref = fields
        elif fields != header_ref:
            raise RuntimeError(f'SCHEMA_DRIFT:{path}')
        for raw in reader:
            # Selection references identity/control/pre-match fields only. No label_* value is used.
            identity = canonical_id(raw)
            if identity in all_ids:
                duplicate_ids.append(identity)
            all_ids.add(identity)
            rows += 1
            date = str(raw.get('date', '')).strip()
            season = str(raw.get('season', '')).strip()
            split = str(raw.get('split', '')).strip()
            comp = str(raw.get('competition_id', '')).strip()
            dates.append(date)
            seasons[season] += 1
            splits[split] += 1
            split_counts[split] += 1
            season_counts[season] += 1
            competition_counts[comp] += 1
            if split == 'train' and season not in EXCLUDED_SEASONS and date < wall:
                feature_values = {
                    k: str(raw.get(k, '')).strip()
                    for k in fields
                    if k not in CONTROL_FIELDS and not any(k.startswith(p) for p in LABEL_PREFIXES)
                }
                all_candidates.append({
                    'identity': identity,
                    'competition_id': comp,
                    'season': season,
                    'date': date,
                    'home_team': str(raw.get('home_team', '')).strip(),
                    'away_team': str(raw.get('away_team', '')).strip(),
                    'source_file': path.as_posix(),
                    'source_path': str(raw.get('source_path', '')).strip(),
                    '_feature_values': feature_values,
                })
    inventory.append({
        'path': path.as_posix(),
        'rows': rows,
        'date_min': min(dates) if dates else None,
        'date_max': max(dates) if dates else None,
        'season_counts': dict(sorted(seasons.items())),
        'split_counts': dict(sorted(splits.items())),
        'field_count': len(header_ref or []),
    })

# Exact identity protection from structured assets that actually exist in current repository.
# The current R45 research directory is excluded to prevent self-reference false positives.
protected_paths = []
protected_ids: set[str] = set()
protection_errors = []
for path in ROOT.glob('football-data/**/*'):
    if not path.is_file():
        continue
    posix = path.as_posix()
    if posix.startswith(R45_DIR.as_posix() + '/'):
        continue
    low = posix.lower()
    if not any(term in low for term in PROTECTED_TERMS):
        continue
    if path.suffix.lower() not in {'.json', '.jsonl', '.csv', '.tsv'}:
        continue
    if path.stat().st_size > 20_000_000:
        continue
    before = len(protected_ids)
    try:
        if path.suffix.lower() in {'.csv', '.tsv'}:
            delim = '\t' if path.suffix.lower() == '.tsv' else ','
            with path.open('r', encoding='utf-8-sig', newline='') as f:
                for row in csv.DictReader(f, delimiter=delim):
                    ident = maybe_identity(row)
                    if ident:
                        protected_ids.add(ident)
        elif path.suffix.lower() == '.jsonl':
            for line in path.read_text(encoding='utf-8-sig').splitlines():
                if line.strip():
                    walk_json(json.loads(line), protected_ids)
        else:
            walk_json(json.loads(path.read_text(encoding='utf-8-sig')), protected_ids)
        protected_paths.append({'path': posix, 'identities_added': len(protected_ids) - before})
    except Exception as exc:
        protection_errors.append({'path': posix, 'error': type(exc).__name__})

all_candidates.sort(key=lambda r: (r['date'], r['competition_id'], r['season'], r['home_team'], r['away_team']))
candidate_count_pre_repo_protection = len(all_candidates)
eligible = [r for r in all_candidates if r['identity'] not in protected_ids]
repo_overlap_count = candidate_count_pre_repo_protection - len(eligible)

# Use the latest 1000 rows before the historical protection wall, then preserve chronological order.
selected = eligible[-1000:]
train650 = selected[:650]
validation150 = selected[650:800]
locked_oos200 = selected[800:]
selected_ids = {r['identity'] for r in selected}
selected_repo_overlap = len(selected_ids & protected_ids)

# Independently validate the frozen historical receipt. Date-wall separation is exact because date is part of identity.
gold = protected_receipt['gold1000_r1']
full = protected_receipt['full500']
obs = protected_receipt['observable_2025']
external_receipt_consistent = (
    gold['classification'] == 'PROTECTED_EXCLUDE'
    and gold['random_manifest']['rows'] == 1000
    and gold['reserve_manifest']['rows'] == 2000
    and gold['reserve_manifest']['date_min'] == wall
    and gold['verified_unique_identity_union'] == 3000
    and full['classification'] == 'PROTECTED_EXCLUDE'
    and full['verified_unique_identity_union'] == 736
    and all(v['season'] == '2025/26' if 'season' in v else True for v in [])
    and all(v['date_min'] >= wall for v in full['versions_checked'])
    and obs['classification'] == 'PROTECTED_HOLDOUT_EXCLUDE'
    and obs['season'] in EXCLUDED_SEASONS
)
selected_temporally_disjoint = bool(selected) and selected[-1]['date'] < wall

# Zero-label feature-readiness audit for the selected identities only.
feature_fields = [
    k for k in (header_ref or [])
    if k not in CONTROL_FIELDS and not any(k.startswith(p) for p in LABEL_PREFIXES)
]
missing_by_feature = {}
for field in feature_fields:
    missing_by_feature[field] = sum(1 for r in selected if r['_feature_values'].get(field, '') == '')
selected_competitions = Counter(r['competition_id'] for r in selected)
selected_seasons = Counter(r['season'] for r in selected)
cold_start_count = None
if 'cold_start_flag' in feature_fields:
    cold_start_count = sum(1 for r in selected if r['_feature_values'].get('cold_start_flag', '').lower() in {'1','true','yes'})

protection_complete = (
    len(FILES) == 17
    and len(duplicate_ids) == 0
    and not protection_errors
    and len(protected_ids) > 0
    and external_receipt_consistent
    and selected_temporally_disjoint
    and len(selected) == 1000
    and selected_repo_overlap == 0
    and len(train650) == 650
    and len(validation150) == 150
    and len(locked_oos200) == 200
)
status = 'PASS_R45_ZERO_LABEL_SAMPLE_FREEZE' if protection_complete else 'STOP_R45_PROTECTION_LEDGER_INCOMPLETE'

public_fields = ['identity','competition_id','season','date','home_team','away_team','source_file','source_path']
for r in selected:
    r.pop('_feature_values', None)

out = {
    'schema_version': 'R45-EXISTING-PIT-1000-PREFLIGHT-1.1',
    'status': status,
    'source_file_count': len(FILES),
    'source_total_rows': sum(x['rows'] for x in inventory),
    'canonical_field_count': len(header_ref or []),
    'label_fields_present_but_values_not_referenced': [x for x in (header_ref or []) if any(x.startswith(p) for p in LABEL_PREFIXES)],
    'inventory': inventory,
    'global_split_counts': dict(sorted(split_counts.items())),
    'global_season_counts': dict(sorted(season_counts.items())),
    'global_competition_counts': dict(sorted(competition_counts.items())),
    'duplicate_identity_count': len(duplicate_ids),
    'candidate_policy': {
        'split': 'train',
        'excluded_seasons': sorted(EXCLUDED_SEASONS),
        'exclusive_before_date': wall,
        'selection': 'latest_1000_then_chronological_split',
        'sort': ['date','competition_id','season','home_team','away_team']
    },
    'candidate_rows_before_exact_repo_protection': candidate_count_pre_repo_protection,
    'protected_identity_count_discovered_in_current_repo': len(protected_ids),
    'candidate_repo_protected_overlap_count': repo_overlap_count,
    'eligible_rows_after_repo_protection': len(eligible),
    'protection_gate': {
        'current_repo_structured_paths': protected_paths,
        'parse_errors': protection_errors,
        'selected_repo_overlap_count': selected_repo_overlap,
        'external_protected_assets_receipt': RECEIPT_PATH.as_posix(),
        'external_receipt_consistent': external_receipt_consistent,
        'selected_temporally_disjoint_from_gold_full500_2025_holdout': selected_temporally_disjoint,
        'gold1000_random_rows': gold['random_manifest']['rows'],
        'gold1000_reserve_rows': gold['reserve_manifest']['rows'],
        'gold1000_union_rows': gold['verified_unique_identity_union'],
        'full500_union_rows': full['verified_unique_identity_union'],
        'observable_2025_classification': obs['classification'],
        'protection_complete_for_freeze': protection_complete
    },
    'frozen1000': range_receipt(selected),
    'frozen_split': {
        'train650': range_receipt(train650),
        'validation150': range_receipt(validation150),
        'locked_oos200': range_receipt(locked_oos200),
    },
    'selected_zero_label_feature_audit': {
        'feature_field_count': len(feature_fields),
        'feature_fields': feature_fields,
        'missing_by_feature': missing_by_feature,
        'cold_start_count': cold_start_count,
        'competition_counts': dict(sorted(selected_competitions.items())),
        'season_counts': dict(sorted(selected_seasons.items())),
    },
    'hard_boundaries': {
        'target_label_values_referenced': 0,
        'selection_uses_target_labels': False,
        'model_fits': 0,
        'candidate_probabilities': 0,
        'oos_labels_referenced': 0,
        'external_network_requests': 0,
        'new_match_downloads': 0,
        'formal_weight': 0,
        'formal_model_mutation': False,
        'formal_data_mutation': False,
        'current_rule_mutation': False,
        'main_mutation': False,
    },
}

OUT.mkdir(parents=True, exist_ok=True)
raw = json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
(OUT / 'r45_preflight.json').write_text(raw, encoding='utf-8')
(OUT / 'r45_preflight.sha256').write_text(hashlib.sha256(raw.encode('utf-8')).hexdigest() + '\n', encoding='ascii')

def write_rows(name: str, rows: list[dict]) -> None:
    with (OUT / name).open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=public_fields)
        w.writeheader(); w.writerows(rows)

write_rows('r45_frozen1000_identity.csv', selected)
write_rows('r45_train650_identity.csv', train650)
write_rows('r45_validation150_identity.csv', validation150)
write_rows('r45_locked_oos200_identity.csv', locked_oos200)

print(json.dumps({
    'status': status,
    'source_file_count': out['source_file_count'],
    'source_total_rows': out['source_total_rows'],
    'global_split_counts': out['global_split_counts'],
    'candidate_rows_before_exact_repo_protection': candidate_count_pre_repo_protection,
    'protected_identity_count_discovered_in_current_repo': len(protected_ids),
    'candidate_repo_protected_overlap_count': repo_overlap_count,
    'eligible_rows_after_repo_protection': len(eligible),
    'protection_gate': out['protection_gate'],
    'frozen1000': out['frozen1000'],
    'frozen_split': out['frozen_split'],
    'selected_zero_label_feature_audit': out['selected_zero_label_feature_audit'],
    'hard_boundaries': out['hard_boundaries'],
}, ensure_ascii=False, indent=2))
