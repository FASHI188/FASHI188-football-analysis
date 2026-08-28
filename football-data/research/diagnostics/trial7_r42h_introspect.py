#!/usr/bin/env python3
from pathlib import Path
import base64,gzip,importlib.util,inspect,json
ROOT=Path(__file__).resolve().parents[3]
enc=ROOT/'football-data/experiments/r42h_player_technical_translation/encoded'
out=Path('/tmp/run_r42h.py')
b64=''.join((enc/f'run_r42h.py.gz.b64.part{i}').read_text().strip() for i in (0,1))
out.write_bytes(gzip.decompress(base64.b64decode(b64)))
spec=importlib.util.spec_from_file_location('r42h',out);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
print('MODULE_FILE',out)
print('PUBLIC_NAMES')
for n in sorted(x for x in dir(m) if not x.startswith('_')):
    o=getattr(m,n)
    if inspect.isfunction(o):
        try:sig=str(inspect.signature(o))
        except Exception:sig='?'
        print('FUNC',n,sig)
    elif isinstance(o,(str,int,float,bool,tuple,list,dict)):
        s=repr(o)
        if len(s)<400: print('CONST',n,s)
print('SOURCE_LINES',len(out.read_text().splitlines()))
