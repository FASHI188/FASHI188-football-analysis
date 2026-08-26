#!/usr/bin/env python3
from __future__ import annotations
import json,sys
from pathlib import Path
import run_experiment_r26b as m

m.DIVS=('T1','SC1','SC2','SC3')
m.MIN_ACTIVE_DIVS=4

try:
    if len(sys.argv)!=2 or sys.argv[1] not in {'run','verify'}:
        raise SystemExit('usage: run_experiment_r26b_v2.py {run|verify}')
    if sys.argv[1]=='run':
        m.run()
        p=m.OUT/'summary_r26b.json'; s=json.loads(p.read_text(encoding='utf-8'))
        s['governance']['domain']='Turkey T1 and Scotland SC1/SC2/SC3; disjoint from R17/R18/R23/R25'
        s['governance']['mapping_feasibility_revision']='G1 removed before any R26b source-label retrieval because HF league metadata had no G1 mapping'
        p.write_text(json.dumps(s,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    else:
        m.verify()
except Exception as exc:
    if not isinstance(exc,SystemExit):
        p=Path(__file__).resolve().parent/'results'/'error_r26b.json';p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps({'exception_type':type(exc).__name__,'message':str(exc)},indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    raise
