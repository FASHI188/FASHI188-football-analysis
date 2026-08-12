#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path('.')
OUT = Path('/tmp/r45-preflight')
FILES = sorted(ROOT.glob('football-data/training_datasets/*/point_in_time.csv'))
ID_FIELDS = ('competition_id', 'season', 'date', 'home_team', 'away_team')
PROTECTED_TERMS = ('fixed', 'gold', 'reserve', 'holdout', 'blind', 'oos', 'protected')
LABEL_PREFIXES = ('label_',)
EXCLUDED_SEASONS = {'2025/26'}


def canonical_id(row: dict[str, str]) -> str:
    return '|'.join(str(row.get(k, '')).strip() for k in ID_FIELDS)


def sha_lines(values: list[str]) -> str:
    raw = ('\n'.join(values) + ('\n' if values else '')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def maybe_identity(row: dict) -> str | None:
    if all(k in row for k in ID_FIELDS):
        ident = '|'.join(str(row.get(k, '')).strip() for k in ID_FIELDS)
        if all(ident.split('|')):
            return ident
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


if not FILES:
    raise SystemExit('NO_POINT_IN_TIME_FILES')

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
            # Target values are never referenced. Only identity/control metadata are used here.
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
            if split == 'train' and season not in EXCLUDED_SEASONS:
                all_candidates.append({
                    'identity': identity,
                    'competition_id': comp,
                    'season': season,
                    'date': date,
                    'home_team': str(raw.get('home_team', '')).strip(),
                    'away_team': str(raw.get('away_team', '')).strip(),
                    'source_file': path.as_posix(),
                    'source_path': str(raw.get('source_path', '')).strip(),
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

# Protection discovery is identity-only: no outcome field is referenced.
protected_paths = []
protected_ids: set[str] = set()
protection_errors = []
full500_hits = []
for path in ROOT.glob('football-data/**/*'):
    if not path.is_file():
        continue
    low = path.as_posix().lower()
    if 'full500' in low:
        full500_hits.append(path.as_posix())
    if not any(term in low for term in PROTECTED_TERMS):
        continue
    if path.suffix.lower() not in {'.json', '.jsonl', '.csv', '.tsv'}:
        protected_paths.append({'path': path.as_posix(), 'parsed': False, 'reason': 'NON_STRUCTURED_EXTENSION'})
        continue
    if path.stat().st_size > 20_000_000:
        protected_paths.append({'path': path.as_posix(), 'parsed': False, 'reason': 'SIZE_LIMIT'})
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
                if not line.strip():
                    continue
                obj = json.loads(line)
                walk_json(obj, protected_ids)
        else:
            obj = json.loads(path.read_text(encoding='utf-8-sig'))
            walk_json(obj, protected_ids)
        protected_paths.append({'path': path.as_posix(), 'parsed': True, 'identities_added': len(protected_ids)-before})
    except Exception as exc:
        protection_errors.append({'path': path.as_posix(), 'error': type(exc).__name__})
        protected_paths.append({'path': path.as_posix(), 'parsed': False, 'reason': type(exc).__name__})

# Also find literal full500 references without interpreting any target values.
for path in ROOT.glob('football-data/**/*'):
    if not path.is_file() or path.suffix.lower() not in {'.py','.md','.json','.yml','.yaml','.txt'}:
        continue
    if path.stat().st_size > 5_000_000:
        continue
    try:
        text = path.read_text(encoding='utf-8-sig', errors='ignore').lower()
    except Exception:
        continue
    if 'full500' in text and path.as_posix() not in full500_hits:
        full500_hits.append(path.as_posix())

all_candidates.sort(key=lambda r: (r['date'], r['competition_id'], r['season'], r['home_team'], r['away_team']))
pre_overlap_count = len(all_candidates)
eligible = [r for r in all_candidates if r['identity'] not in protected_ids]
overlap_count = pre_overlap_count - len(eligible)
preliminary1000 = eligible[:1000]
preliminary_ids = [r['identity'] for r in preliminary1000]

# Do not claim a frozen sample until protection coverage is auditable.
gold_paths = [p for p in protected_paths if 'gold' in p['path'].lower()]
reserve_paths = [p for p in protected_paths if 'reserve' in p['path'].lower()]
fixed_paths = [p for p in protected_paths if 'fixed' in p['path'].lower()]
holdout_paths = [p for p in protected_paths if 'holdout' in p['path'].lower()]

protection_complete = (
    len(protected_ids) > 0
    and len(gold_paths) > 0
    and not protection_errors
    and len(duplicate_ids) == 0
)
status = 'PASS_R45_ZERO_LABEL_SAMPLE_FREEZE' if protection_complete and len(preliminary1000) == 1000 else 'STOP_R45_PROTECTION_LEDGER_INCOMPLETE'

out = {
    'schema_version': 'R45-EXISTING-PIT-1000-PREFLIGHT-1.0',
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
    'candidate_policy': {'split': 'train', 'excluded_seasons': sorted(EXCLUDED_SEASONS), 'sort': ['date','competition_id','season','home_team','away_team']},
    'candidate_rows_before_exact_protection_overlap': pre_overlap_count,
    'protected_identity_count_discovered_in_repo': len(protected_ids),
    'candidate_protected_overlap_count': overlap_count,
    'eligible_rows_after_exact_repo_protection_overlap': len(eligible),
    'protection_scan': {
        'structured_paths_seen': protected_paths,
        'parse_errors': protection_errors,
        'gold_path_count': len(gold_paths),
        'reserve_path_count': len(reserve_paths),
        'fixed_path_count': len(fixed_paths),
        'holdout_path_count': len(holdout_paths),
        'full500_literal_hits': sorted(full500_hits),
        'full500_status': 'REPO_LITERAL_IDENTIFIED' if full500_hits else 'UNRESOLVED_NO_REPO_LITERAL_HIT',
        'protection_complete_for_freeze': protection_complete,
    },
    'preliminary1000': {
        'count': len(preliminary1000),
        'date_min': preliminary1000[0]['date'] if preliminary1000 else None,
        'date_max': preliminary1000[-1]['date'] if preliminary1000 else None,
        'identity_sha256': sha_lines(preliminary_ids),
        'not_a_frozen_sample_unless_status_is_PASS': True,
    },
    'planned_split': {'train': 650, 'validation': 150, 'locked_oos': 200},
    'hard_boundaries': {
        'target_label_values_referenced': 0,
        'model_fits': 0,
        'candidate_probabilities': 0,
        'oos_labels_referenced': 0,
        'external_network_requests': 0,
        'new_data_downloads': 0,
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
with (OUT / 'r45_preliminary1000_identity.csv').open('w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, fieldnames=['identity','competition_id','season','date','home_team','away_team','source_file','source_path'])
    w.writeheader()
    w.writerows(preliminary1000)
print(json.dumps({
    'status': status,
    'source_file_count': out['source_file_count'],
    'source_total_rows': out['source_total_rows'],
    'global_split_counts': out['global_split_counts'],
    'candidate_rows_before_exact_protection_overlap': pre_overlap_count,
    'protected_identity_count_discovered_in_repo': len(protected_ids),
    'candidate_protected_overlap_count': overlap_count,
    'eligible_rows_after_exact_repo_protection_overlap': len(eligible),
    'protection_scan': out['protection_scan'],
    'preliminary1000': out['preliminary1000'],
    'hard_boundaries': out['hard_boundaries'],
}, ensure_ascii=False, indent=2))
