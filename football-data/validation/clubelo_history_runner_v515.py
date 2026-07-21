#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

import clubelo_history_ingest_v515 as core


def main() -> int:
    try:
        return int(core.main())
    except Exception as exc:
        payload = {
            "schema_version": "V5.1.5-clubelo-history-ingest-r2",
            "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "status": "EXECUTION_FAILURE",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback_tail": traceback.format_exc().splitlines()[-30:],
            "requested_domains": list(core.load_json(core.CONFIG).get("domains", {})),
            "passed_domains": [],
            "domain_reports": {},
            "formal_weight_change": False,
            "probability_change": False,
            "automatic_promotion": False,
        }
        core.MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        core.MANIFEST.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
