#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path
try:
    import run_experiment_r27 as m
    m.run()
except Exception as exc:
    p=Path(__file__).resolve().parent/'results'/'error_r27.json';p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps({'exception_type':type(exc).__name__,'message':str(exc)},indent=2,ensure_ascii=False)+'\n',encoding='utf-8');raise
