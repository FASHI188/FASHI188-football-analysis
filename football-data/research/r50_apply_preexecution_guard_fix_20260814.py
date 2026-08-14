#!/usr/bin/env python3
from pathlib import Path

p = Path('football-data/research/r50_pit_draw_architecture_dev_20260814.py')
s = p.read_text(encoding='utf-8')
old = 'if n < 50 or len(np.unique(y[mask_tr])) < 2:'
new = 'if n < Ztr.shape[1] + 2 or len(np.unique(y[mask_tr])) < 2:'
if s.count(old) != 1:
    raise SystemExit(f'expected exactly one guard occurrence, found {s.count(old)}')
p.write_text(s.replace(old, new), encoding='utf-8')
print('R50_PREEXECUTION_GUARD_FIX_APPLIED')
