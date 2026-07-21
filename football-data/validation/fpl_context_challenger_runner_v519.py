#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

import fpl_context_challenger_v519 as core

FPL_TEAM_CODE_TO_NAME: dict[str, str] = {}
_ORIGINAL_TEAM_FEATURES = core._team_features
_ORIGINAL_PAIR = core._pair_processed_match


def _numeric_token(value) -> str:
    token = str(value or "").strip()
    try:
        number = float(token)
        if math.isfinite(number) and abs(number - round(number)) < 1e-9:
            return str(int(round(number)))
    except Exception:
        pass
    return token


def _token_variants(value) -> set[str]:
    raw = str(value or "").strip()
    token = _numeric_token(value)
    variants = {item for item in (raw, token) if item}
    try:
        number = float(token)
        if math.isfinite(number) and abs(number - round(number)) < 1e-9:
            variants.add(f"{int(round(number))}.0")
    except Exception:
        pass
    return variants


def _patched_team_features(bundle):
    features, audit = _ORIGINAL_TEAM_FEATURES(bundle)
    for row in bundle["teams.csv"]["rows"]:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        # fixtures.csv is keyed by team *code*, so only code may drive fixture identity.
        code_token = _numeric_token(row.get("code"))
        if code_token:
            FPL_TEAM_CODE_TO_NAME[code_token] = name
        # Feature lookup may encounter code/id serialized as integer-like or .0 strings.
        for raw_token in (row.get("code"), row.get("id")):
            for variant in _token_variants(raw_token):
                if name in features:
                    features[variant] = features[name]
    audit = dict(audit)
    audit["fixture_team_code_bridge_count"] = len(FPL_TEAM_CODE_TO_NAME)
    audit["feature_alias_key_count"] = sum(1 for key in features if str(key).replace(".", "", 1).isdigit())
    return features, audit


def _patched_pair_processed_match(lookup, date: str, home: str, away: str):
    home_name = FPL_TEAM_CODE_TO_NAME.get(_numeric_token(home), str(home))
    away_name = FPL_TEAM_CODE_TO_NAME.get(_numeric_token(away), str(away))
    return _ORIGINAL_PAIR(lookup, date, home_name, away_name)


core._team_features = _patched_team_features
core._pair_processed_match = _patched_pair_processed_match


def main() -> int:
    try:
        return int(core.main())
    except Exception as exc:
        payload = {
            "schema_version": "V5.1.9-fpl-context-challenger-execution-r5",
            "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "competition_id": "ENG_PremierLeague",
            "season": "2025/26",
            "status": "EXECUTION_FAILURE_KEEP_FORMAL_WEIGHT_0",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback_tail": traceback.format_exc().splitlines()[-30:],
            "fixture_team_code_bridge_count": len(FPL_TEAM_CODE_TO_NAME),
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
