#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import runtime as rt

FOOTBALL3_GOVERNED_RESEARCH_REPLAY = "football3-current-formal-retrospective-research-replay-v1"
MODE = "CURRENT_V2_RETROSPECTIVE_REPLAY"
SCHEMA = "football3-current-v2-retrospective-utc-day-history-boundary-v1"


def safe_history_upper(target_kickoff: datetime) -> datetime:
    """Return the exclusive history ceiling at 00:00 UTC on the target UTC date."""
    if not isinstance(target_kickoff, datetime):
        raise rt.RuntimeGateError("CURRENT_V2_RETROSPECTIVE_REPLAY target kickoff must be datetime")
    if target_kickoff.tzinfo is None or target_kickoff.utcoffset() is None:
        raise rt.RuntimeGateError(
            "CURRENT_V2_RETROSPECTIVE_REPLAY target kickoff must be timezone-aware; naive datetime rejected"
        )
    target_utc = target_kickoff.astimezone(timezone.utc)
    return datetime(target_utc.year, target_utc.month, target_utc.day, tzinfo=timezone.utc)


def install(replay_module) -> dict[str, Any]:
    replay_module._safe_history_upper = safe_history_upper
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "request_mode": MODE,
        "history_upper_semantics": "TARGET_UTC_DATE_START_EXCLUSIVE",
        "target_utc_day_excluded": True,
        "naive_datetime_policy": "FAIL_CLOSED",
        "target_fixture_kickoff_changed": False,
        "prospective_path_changed": False,
        "strict_pit_path_changed": False,
        "production_source_changed": False,
        "model_or_current_or_weight_changed": False,
    }
