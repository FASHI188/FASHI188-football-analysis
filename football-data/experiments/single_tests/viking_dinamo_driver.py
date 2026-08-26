#!/usr/bin/env python3
import json
from pathlib import Path
import viking_dinamo_20260826 as m

try:
    m.main()
except Exception as exc:
    p = Path(__file__).resolve().parent / 'results' / 'viking_dinamo_error.json'
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({'exception_type': type(exc).__name__, 'message': str(exc)}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    raise
