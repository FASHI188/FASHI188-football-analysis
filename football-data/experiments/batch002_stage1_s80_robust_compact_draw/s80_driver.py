#!/usr/bin/env python3
# Diagnostic-only enrichment plus metadata-only Batch-002 identity fallback; candidate logic unchanged.
import json
from pathlib import Path
import run_batch002_s80 as m
import safe_metadata_batch002 as sm

# Batch-002-only monkey patch. The fallback reads identity/kickoff/league/team metadata only.
m.s2.safe_target_metadata = lambda lock: sm.safe_target_metadata(m.s2, lock)

OUT = Path(__file__).resolve().parent / "results"
OUT.mkdir(parents=True, exist_ok=True)
try:
    m.run()
except Exception as exc:
    diag = {"exception_type": type(exc).__name__, "message": str(exc)[:1200]}
    mapping = m.S2 / "data" / "mapping_audit_stage2.json"
    if mapping.exists():
        try:
            audit = json.loads(mapping.read_text(encoding="utf-8"))
            unresolved = []
            for rec in audit:
                if rec.get("resolution_method") == "primary" and len(rec.get("fixture_candidates_primary", [])) != 1:
                    unresolved.append(rec)
            diag["unresolved"] = unresolved
            diag["mapping_audit_rows"] = len(audit)
        except Exception as audit_exc:
            diag["mapping_audit_read_error"] = f"{type(audit_exc).__name__}: {audit_exc}"
    (OUT / "error_batch002_s80.json").write_text(
        json.dumps(diag, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    raise

# CI trigger only: validate the metadata-only fallback from the current branch HEAD.
