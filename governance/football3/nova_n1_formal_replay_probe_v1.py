#!/usr/bin/env python3
from __future__ import annotations

import dataclasses
import inspect
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FOOTBALL_DATA = ROOT / "football-data"
for p in (ROOT, FOOTBALL_DATA, FOOTBALL_DATA / "new_engine_v1"):
    sys.path.insert(0, str(p))

from historical_xg_challenger_v1 import historical_xg_challenger as hxg  # noqa: E402
from new_engine_v1 import formal_fusion_v2 as formal  # noqa: E402


def signature(obj: Any) -> str:
    try:
        return str(inspect.signature(obj))
    except (TypeError, ValueError):
        return "UNAVAILABLE"


def dataclass_fields(obj: Any) -> list[dict[str, Any]]:
    if not dataclasses.is_dataclass(obj):
        return []
    out = []
    for f in dataclasses.fields(obj):
        out.append({
            "name": f.name,
            "type": str(f.type),
            "has_default": f.default is not dataclasses.MISSING or f.default_factory is not dataclasses.MISSING,
        })
    return out


def public_attrs(obj: Any) -> list[str]:
    return sorted(name for name in dir(obj) if not name.startswith("_"))


payload = {
    "schema_version": "football3-nova-n1-formal-replay-probe-v1",
    "labels_read": False,
    "artifacts_opened": False,
    "fixture_row": {
        "signature": signature(hxg.FixtureRow),
        "fields": dataclass_fields(hxg.FixtureRow),
        "annotations": {k: str(v) for k, v in getattr(hxg.FixtureRow, "__annotations__", {}).items()},
    },
    "xg_params": {
        "signature": signature(hxg.XGParams),
        "fields": dataclass_fields(hxg.XGParams),
    },
    "challenger_state": {
        "signature": signature(hxg.ChallengerState),
        "methods": {
            name: signature(getattr(hxg.ChallengerState, name))
            for name in public_attrs(hxg.ChallengerState)
            if callable(getattr(hxg.ChallengerState, name))
        },
    },
    "module_objects": {
        name: signature(getattr(hxg, name))
        for name in public_attrs(hxg)
        if callable(getattr(hxg, name)) and any(token in name.casefold() for token in ("fixture", "label", "load", "parse", "row", "batch"))
    },
    "formal": {
        "new_candidate_state": signature(formal.new_candidate_state),
        "predict_formal_batch": signature(formal.predict_formal_batch),
        "apply_completed_xg_batch": signature(formal.apply_completed_xg_batch),
        "blend_active_predictions": signature(formal.blend_active_predictions),
        "fusion_weight": formal.FUSION_WEIGHT,
    },
    "expected_v1_params_keys": sorted(getattr(hxg, "EXPECTED_V1_PARAMS", {}).keys()),
}
print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
