#!/usr/bin/env python3
from pathlib import Path
import base64,gzip
ROOT=Path(__file__).resolve().parents[3]
enc=ROOT/'football-data/experiments/r42h_player_technical_translation/encoded'
b64=''.join((enc/f'run_r42h.py.gz.b64.part{i}').read_text().strip() for i in (0,1))
lines=gzip.decompress(base64.b64decode(b64)).decode('utf-8').splitlines()
for i,line in enumerate(lines[218:563],219): print(f'{i:04d}: {line}')
