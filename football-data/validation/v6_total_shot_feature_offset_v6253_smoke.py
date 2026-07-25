#!/usr/bin/env python3
from __future__ import annotations
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "engine", ROOT / "validation"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from v6_total_shot_feature_offset_v6253 import _domain

OUT = ROOT / "manifests" / "v6_total_shot_feature_offset_v6253_smoke_status.json"

def main() -> int:
    reports = {}
    failures = {}
    for cid in ("ENG_PremierLeague", "GER_Bundesliga"):
        try:
            result, rows = _domain(cid)
            reports[cid] = {
                "result": result,
                "row_count": len(rows),
            }
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if not failures else "FAIL",
        "reports": reports,
        "failures": failures,
        "diagnostic_only": True,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
