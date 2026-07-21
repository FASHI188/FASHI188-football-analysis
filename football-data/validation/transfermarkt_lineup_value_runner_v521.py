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

import transfermarkt_lineup_value_readiness_v521 as core

_ORIGINAL_READ = core.read_fixture_lineups


def _normalize_player_id(value: str) -> str:
    token = str(value or "").strip()
    if token.lower().startswith("transfermarkt:"):
        token = token.split(":", 1)[1]
    return token


def _patched_read_fixture_lineups(domain: str):
    fixtures = _ORIGINAL_READ(domain)
    for fixture in fixtures:
        fixture["home_starters"] = [_normalize_player_id(item) for item in fixture["home_starters"]]
        fixture["away_starters"] = [_normalize_player_id(item) for item in fixture["away_starters"]]
    return fixtures


core.read_fixture_lineups = _patched_read_fixture_lineups


def main() -> int:
    try:
        return int(core.main())
    except Exception as exc:
        payload = {
            "schema_version": "V5.2.1-transfermarkt-lineup-value-readiness-execution-r3",
            "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "status": "EXECUTION_FAILURE",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback_tail": traceback.format_exc().splitlines()[-30:],
            "formal_weight_change": False,
            "probability_change": False,
            "automatic_promotion": False,
        }
        core.OUT.parent.mkdir(parents=True, exist_ok=True)
        core.OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
