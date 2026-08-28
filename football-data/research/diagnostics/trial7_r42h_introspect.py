#!/usr/bin/env python3
from pathlib import Path
import base64,gzip,re
ROOT=Path(__file__).resolve().parents[3]
enc=ROOT/'football-data/experiments/r42h_player_technical_translation/encoded'
b64=''.join((enc/f'run_r42h.py.gz.b64.part{i}').read_text().strip() for i in (0,1))
src=gzip.decompress(base64.b64decode(b64)).decode('utf-8')
lines=src.splitlines()
print('SOURCE_LINES',len(lines))
print('---HEAD---')
for i,line in enumerate(lines[:220],1): print(f'{i:04d}: {line}')
print('---SYMBOLS---')
for i,line in enumerate(lines,1):
    s=line.strip()
    if s.startswith(('def ','class ')) or re.match(r'^[A-Z][A-Z0-9_]+\s*=',s):
        print(f'{i:04d}: {line}')
