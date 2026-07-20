#!/usr/bin/env python3
from __future__ import annotations

import json
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "market_lomo_data_readiness_v470_status.json"


def main() -> int:
    try:
        import market_lomo_data_readiness_v470 as target
        return target.main()
    except Exception as exc:
        payload = {
            "schema_version": "V4.7.0-market-lomo-data-readiness-r1",
            "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "status": "FAIL",
            "reason": str(exc),
            "traceback_tail": traceback.format_exc().splitlines()[-20:],
            "production_lomo_validated_count": 0,
            "formal_ev_available_count": 0,
            "policy": "Failure receipt only. No LOMO receipt or EV activation is created by this audit.",
        }
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
