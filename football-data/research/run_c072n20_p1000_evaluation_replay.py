#!/usr/bin/env python3
from pathlib import Path

p=Path(__file__).with_name('run_c072n20_p1000_evaluation.py')
s=p.read_text(encoding='utf-8')
old="y=test.T.to_numpy(int)"
new="y=test['T'].to_numpy(int)"
assert s.count(old)==1, 'frozen correction target count drift'
s=s.replace(old,new)
ns={'__name__':'__main__','__file__':str(p)}
exec(compile(s,str(p),'exec'),ns)
