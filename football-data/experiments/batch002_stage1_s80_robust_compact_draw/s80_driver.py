#!/usr/bin/env python3
# Diagnostic-only enrichment; candidate logic remains unchanged.
import json
from pathlib import Path
import run_batch002_s80 as m

OUT = Path(__file__).resolve().parent / "results"
OUT.mkdir(parents=True, exist_ok=True)
try:
    m.run()
except Exception as exc:
    diag = {"exception_type": type(exc).__name__, "message": str(exc)[:1200]}
    mapping = m.S2 / "data" / "mapping_audit_batch001_stage2.json"
    if mapping.exists():
        try:
            audit = json.loads(mapping.read_text(encoding="utf-8"))
            diag["unresolved"] = audit.get("unresolved", [])
            diag["mapping_audit_rows"] = audit.get("audit", [])[-10:]
        except Exception as audit_exc:
            diag["mapping_audit_read_error"] = f"{type(audit_exc).__name__}: {audit_exc}"
    (OUT / "error_batch002_s80.json").write_text(
        json.dumps(diag, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    raise
