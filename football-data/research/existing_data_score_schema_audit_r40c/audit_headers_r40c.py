#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

ROOT = Path('.')
FILES = sorted(ROOT.glob('football-data/training_datasets/*/point_in_time.csv'))
if not FILES:
    raise SystemExit('NO_EXISTING_POINT_IN_TIME_DATASETS')

rows = []
all_headers = None
for p in FILES:
    with p.open('r', encoding='utf-8-sig', newline='') as f:
        rd = csv.DictReader(f)
        fields = list(rd.fieldnames or [])
    # Header-only: never iterate a data row.
    raw_header = ','.join(fields).encode('utf-8')
    rows.append({
        'path': p.as_posix(),
        'field_count': len(fields),
        'fields': fields,
        'header_sha256': hashlib.sha256(raw_header).hexdigest(),
    })
    if all_headers is None:
        all_headers = fields

same_schema = all(r['fields'] == rows[0]['fields'] for r in rows)
fields = rows[0]['fields']
keywords = ('goal','score','fthg','ftag','home_goals','away_goals','total_goals','clean','btts')
score_like = [name for name in fields if any(k in name.lower() for k in keywords)]
label_like = [name for name in fields if 'label' in name.lower() or 'result' in name.lower()]

out = {
    'schema_version': 'R40C-SCORE-SCHEMA-AUDIT-1.0',
    'status': 'PASS_R40C_HEADER_ONLY_SCHEMA_AUDIT',
    'file_count': len(rows),
    'same_schema_across_files': same_schema,
    'canonical_field_count': len(fields),
    'canonical_fields': fields,
    'score_like_fields': score_like,
    'label_like_fields': label_like,
    'files': rows,
    'hard_boundaries': {
        'data_rows_iterated': 0,
        'target_labels_accessed': 0,
        'external_network_requests': 0,
        'football_api_requests': 0,
        'model_fits': 0,
        'candidate_probabilities': 0,
        'formal_weight': 0,
        'formal_model_mutation': False,
        'formal_data_mutation': False,
        'current_rule_mutation': False,
        'main_mutation': False,
    },
}
Path('/tmp/r40c-output').mkdir(parents=True, exist_ok=True)
raw = json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
Path('/tmp/r40c-output/r40c_header_audit.json').write_text(raw, encoding='utf-8')
Path('/tmp/r40c-output/r40c_header_audit.sha256').write_text(hashlib.sha256(raw.encode()).hexdigest()+'\n', encoding='ascii')
print(json.dumps({k:out[k] for k in ('status','file_count','same_schema_across_files','canonical_field_count','canonical_fields','score_like_fields','label_like_fields','hard_boundaries')}, ensure_ascii=False, indent=2))
