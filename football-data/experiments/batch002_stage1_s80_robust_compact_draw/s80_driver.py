#!/usr/bin/env python3
# Trigger-only commit after the preregistered workflow exists; model logic unchanged.
import json
from pathlib import Path
import run_batch002_s80 as m

OUT = Path(__file__).resolve().parent / "results"
OUT.mkdir(parents=True, exist_ok=True)
try:
    m.run()
except Exception as exc:
    (OUT / "error_batch002_s80.json").write_text(
        json.dumps({"exception_type": type(exc).__name__, "message": str(exc)[:1200]}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    raise
