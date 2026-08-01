#!/usr/bin/env python3
"""Compatibility entrypoint for the bidirectional draw-signal closure audit.

The wrapper also enforces the fail-closed UNKNOWN-field policy: an UNKNOWN PIT field
must remain visible in the near-miss ledger even when an old experiment accessed it.
Prior use does not prove prediction-time availability.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import draw_signal_closure_engine_v502 as _engine
from draw_signal_closure_engine_v502 import *  # noqa: F401,F403

_original_profile_fields = _engine.profile_fields


def preserve_unknown_near_miss(result: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy where every non-candidate UNKNOWN field remains a near-miss."""
    output = dict(result)
    fields = list(output.get("fields", []))
    near = list(output.get("UNTESTED_BUT_NOT_PIT_SAFE_OR_COVERED", []))
    near_by_name = {str(row.get("field")): row for row in near}
    for row in fields:
        if row.get("classification") != "UNKNOWN_PIT_STATUS":
            continue
        if row.get("qualifies_existing_pit_safe_untested"):
            continue
        near_by_name[str(row.get("field"))] = row
    output["UNTESTED_BUT_NOT_PIT_SAFE_OR_COVERED"] = [near_by_name[key] for key in sorted(near_by_name, key=str.lower)]
    return output


def profile_fields(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return preserve_unknown_near_miss(_original_profile_fields(*args, **kwargs))


# build_audit() resolves profile_fields from the engine module at runtime.
_engine.profile_fields = profile_fields
_engine_main = _engine.main

if __name__ == "__main__":
    raise SystemExit(_engine_main())
