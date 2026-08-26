#!/usr/bin/env python3
import json,traceback
from pathlib import Path
import run_stage4a as m
OUT=Path(__file__).resolve().parent/'results';OUT.mkdir(parents=True,exist_ok=True)
try:
    m.run()
except Exception as e:
    (OUT/'error_stage4a.json').write_text(json.dumps({'exception_type':type(e).__name__,'message':str(e)[:1000]},indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    raise
