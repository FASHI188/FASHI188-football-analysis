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

import fpl_context_challenger_v519 as core

FPL_TEAM_ID_TO_NAME: dict[str, str] = {}
_ORIGINAL_TEAM_FEATURES = core._team_features
_ORIGINAL_PAIR = core._pair_processed_match


def _patched_team_features(bundle):
    features, audit = _ORIGINAL_TEAM_FEATURES(bundle)
    # fixtures.csv uses FPL team IDs; teams.csv provides the deterministic ID->name bridge.
    for row in bundle["teams.csv"]["rows"]:
        team_id = str(row.get("id") or "").strip()
        name = str(row.get("name") or "").strip()
        if not team_id or not name:
            continue
        FPL_TEAM_ID_TO_NAME[team_id] = name
        if name in features:
            features[team_id] = features[name]
    audit = dict(audit)
    audit["fixture_team_id_bridge_count"] = len(FPL_TEAM_ID_TO_NAME)
    return features, audit


def _patched_pair_processed_match(lookup, date: str, home: str, away: str):
    home_name = FPL_TEAM_ID_TO_NAME.get(str(home), str(home))
    away_name = FPL_TEAM_ID_TO_NAME.get(str(away), str(away))
    return _ORIGINAL_PAIR(lookup, date, home_name, away_name)


# Identity-layer correction only. No model feature, parameter grid, split or gate changes.
core._team_features = _patched_team_features
core._pair_processed_match = _patched_pair_processed_match


def main() -> int:
    try:
        return int(core.main())
    except Exception as exc:
        payload = {
            "schema_version": "V5.1.9-fpl-context-challenger-execution-r2",
            "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "competition_id": "ENG_PremierLeague",
            "season": "2025/26",
            "status": "EXECUTION_FAILURE_KEEP_FORMAL_WEIGHT_0",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback_tail": traceback.format_exc().splitlines()[-30:],
            "fixture_team_id_bridge_count": len(FPL_TEAM_ID_TO_NAME),
            "formal_weight": 0,
            "probability_change": False,
            "automatic_promotion": False,
        }
        core.OUT.parent.mkdir(parents=True, exist_ok=True)
        core.OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
