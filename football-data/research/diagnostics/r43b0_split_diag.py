#!/usr/bin/env python3
from collections import Counter
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MOD = ROOT / 'football-data' / 'experiments' / 'r43b0_probabilistic_lineup_baseline'
sys.path.insert(0, str(MOD))
import run_r43b0_probabilistic_lineup as r

rows = r.load_matches()
phases = r.split_dates(rows)
player_map, meta = r.prepare_player_rows({x['fixture_id'] for x in rows})
examples, sides = r.build_examples(rows, player_map, phases)
print(json.dumps({
    'source_meta': meta,
    'fixture_phase_counts': dict(Counter(phases.values())),
    'example_phase_counts': dict(Counter(x['phase'] for x in examples)),
    'side_phase_counts': dict(Counter(x['phase'] for x in sides)),
    'side_dates': {p: [min([x['date'] for x in sides if x['phase']==p], default=None), max([x['date'] for x in sides if x['phase']==p], default=None)] for p in ['burn','train','val','test']},
    'player_map_sides': len(player_map),
    'examples': len(examples),
    'sides': len(sides),
}, indent=2))
