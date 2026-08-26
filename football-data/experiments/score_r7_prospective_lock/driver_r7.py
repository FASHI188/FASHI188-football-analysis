#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
try:
    import run_score_r7 as m
    m.main()
except Exception as exc:
    p=HERE/'results'/'error_score_r7.json'; p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps({'exception_type':type(exc).__name__,'message':str(exc)},indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    raise
