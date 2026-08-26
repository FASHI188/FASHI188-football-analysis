#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"

try:
    import run_stage2 as m
    m.run()
except Exception as exc:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "error_stage2.json").write_text(
        json.dumps({"exception_type": type(exc).__name__, "message": str(exc)}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    raise
