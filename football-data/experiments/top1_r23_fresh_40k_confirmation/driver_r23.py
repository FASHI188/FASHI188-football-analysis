#!/usr/bin/env python3
import json
from pathlib import Path
import run_experiment_r23 as m
try:
    m.run()
except Exception as exc:
    p=Path(__file__).resolve().parent/'results'/'error_r23.json';p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps({'exception_type':type(exc).__name__,'message':str(exc)},indent=2,ensure_ascii=False)+'\n',encoding='utf-8');raise
