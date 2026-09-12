#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

TARGET = Path(__file__).with_name("prelabel_semantic_closure_v1.py")
OLD = 'if int(ib.get("missing_n") or -1) != 0 or int(ib.get("extra_n") or -1) != 0:'
NEW = 'if int(ib.get("missing_n", -1)) != 0 or int(ib.get("extra_n", -1)) != 0:'

raw = TARGET.read_text(encoding="utf-8")
if raw.count(OLD) != 1:
    raise RuntimeError(f"execution-only zero-value assertion patch target count={raw.count(OLD)}")
patched = raw.replace(OLD, NEW)
if len(patched) != len(raw) - len(OLD) + len(NEW):
    raise RuntimeError("unexpected execution-only patch length")
code = compile(patched, str(TARGET), "exec")
ns = {"__name__": "__main__", "__file__": str(TARGET), "__package__": None}
exec(code, ns, ns)
