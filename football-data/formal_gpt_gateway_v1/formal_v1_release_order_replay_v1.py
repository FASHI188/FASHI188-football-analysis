#!/usr/bin/env python3
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Iterator

import runtime as rt

SCHEMA = "football3-formal-v1-release-order-replay-v1"
_INSTALLED = False


def _v1_fixture(row: dict[str, Any], kickoff: datetime):
    return rt.v1_engine.Fixture(
        str(row["fixture_id"]), str(row["competition_id"]), str(row["season"]), kickoff,
        str(row["home_team_id"]), str(row["away_team_id"]),
    )


def _event_key(row: dict[str, Any]) -> tuple[Any, ...]:
    order = {"LABEL_RELEASE": 0, "FIXTURE_FREEZE": 1}
    kind = str(row.get("event_type") or "")
    if kind not in order:
        raise rt.RuntimeGateError(f"unsupported formal replay event type: {kind}")
    return (
        rt._parse_dt(str(row["event_at"]), "event_at"),
        order[kind],
        rt._parse_dt(str(row["kickoff"]), "kickoff"),
        str(row["competition_id"]),
        str(row["fixture_id"]),
        0 if row.get("enters_xg") and not row.get("enters_v1") else 1,
    )


def _event_groups(events: list[dict[str, Any]], as_of: datetime) -> Iterator[tuple[datetime, list[dict[str, Any]]]]:
    """Yield deterministic event-time transactions valid at an exact PIT cutoff.

    LABEL_RELEASE is inclusive at as_of. FIXTURE_FREEZE is strictly before as_of,
    matching runtime.history_delta_events. This prevents a caller from advancing a
    freeze at the cutoff while still allowing an authoritative label released at it.
    """
    ordered = sorted(events, key=_event_key)
    cursor = 0
    previous_at: datetime | None = None
    while cursor < len(ordered):
        at = rt._parse_dt(str(ordered[cursor]["event_at"]), "event_at")
        if previous_at is not None and at < previous_at:
            raise rt.RuntimeGateError("formal replay event cursor time reversal")
        if at > as_of:
            raise rt.RuntimeGateError("formal replay event exceeds exact cutoff")
        end = cursor + 1
        while end < len(ordered) and rt._parse_dt(str(ordered[end]["event_at"]), "event_at") == at:
            end += 1
        same = ordered[cursor:end]
        if at == as_of and any(str(row["event_type"]) == "FIXTURE_FREEZE" for row in same):
            raise rt.RuntimeGateError("fixture freeze at exact cutoff is not PIT-eligible")
        yield at, same
        previous_at = at
        cursor = end


def _rebuild_v1_from_available(state, available: dict[str, dict[str, Any]]) -> None:
    """Rebuild Frozen V1 from labels actually available at the current PIT boundary."""
    rebuilt = rt.v1_engine.EngineState(params=state.base.params)
    by_kickoff: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
    for row in available.values():
        by_kickoff[rt._parse_dt(str(row["kickoff"]), "kickoff")].append(row)
    for kickoff, rows in sorted(by_kickoff.items()):
        rows = sorted(rows, key=lambda row: (str(row["competition_id"]), str(row["fixture_id"])))
        fixtures = [_v1_fixture(row, kickoff) for row in rows]
        labels = {str(row["fixture_id"]): (int(row["home_goals"]), int(row["away_goals"])) for row in rows}
        rebuilt.apply_batch(fixtures, labels)
    state.base = rebuilt


def _apply_events(state, events: list[dict[str, Any]], as_of):
    initial_v1_seen = set(state.base.seen_fixtures)
    available_v1: dict[str, dict[str, Any]] = {}
    applied_v1 = applied_xg = frozen_xg = group_n = 0
    for at, same in _event_groups(events, as_of):
        group_n += 1
        releases = [e for e in same if e["event_type"] == "LABEL_RELEASE"]
        freezes = [e for e in same if e["event_type"] == "FIXTURE_FREEZE"]
        if releases:
            by_kickoff_xg: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
            for row in releases:
                if row["enters_xg"]:
                    by_kickoff_xg[rt._parse_dt(str(row["kickoff"]), "kickoff")].append(row)
            for kickoff, rows in sorted(by_kickoff_xg.items()):
                rows = sorted(rows, key=lambda row: (str(row["competition_id"]), str(row["fixture_id"])))
                fixtures = [rt.hxg.FixtureRow(
                    str(row["fixture_id"]), str(row["competition_id"]), str(row["season"]), kickoff,
                    str(row["home_team_id"]), str(row["away_team_id"]),
                    str(row["home_team_name"]), str(row["away_team_name"]),
                ) for row in rows]
                for fixture in fixtures:
                    if fixture.fixture_id not in state.pending or state.pending[fixture.fixture_id]["fixture"] != fixture:
                        raise rt.RuntimeGateError("released XG label lacks exact cached fixture freeze")
                labels = {str(row["fixture_id"]): rt.hxg.ReleasedLabel(
                    int(row["home_goals"]), int(row["away_goals"]),
                    float(row["home_xg"]), float(row["away_xg"]),
                    rt._parse_dt(str(row["result_available_at"]), "result_available_at"),
                ) for row in rows}
                state.apply_released_batch(fixtures, labels, as_of=at, update_base=False)
                applied_xg += len(rows)

            vrows = [row for row in releases if row["enters_v1"]]
            if vrows:
                for row in vrows:
                    fid = str(row["fixture_id"])
                    if fid in available_v1 or fid in initial_v1_seen:
                        raise rt.RuntimeGateError("duplicate V1 release fixture")
                    available_v1[fid] = row
                last = state.base.last_update_time
                has_late_release = last is not None and any(
                    rt._parse_dt(str(row["kickoff"]), "kickoff") < last for row in vrows
                )
                if has_late_release:
                    if initial_v1_seen:
                        raise rt.RuntimeGateError("V1_LATE_RELEASE_REQUIRES_FULL_REBUILD")
                    _rebuild_v1_from_available(state, available_v1)
                else:
                    by_kickoff_v1: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
                    for row in vrows:
                        by_kickoff_v1[rt._parse_dt(str(row["kickoff"]), "kickoff")].append(row)
                    for kickoff, rows in sorted(by_kickoff_v1.items()):
                        rows = sorted(rows, key=lambda row: (str(row["competition_id"]), str(row["fixture_id"])))
                        fixtures = [_v1_fixture(row, kickoff) for row in rows]
                        labels = {str(row["fixture_id"]): (int(row["home_goals"]), int(row["away_goals"])) for row in rows}
                        state.base.apply_batch(fixtures, labels)
                applied_v1 += len(vrows)

        if freezes:
            by_kickoff: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
            for row in freezes:
                by_kickoff[rt._parse_dt(str(row["kickoff"]), "kickoff")].append(row)
            for kickoff, rows in sorted(by_kickoff.items()):
                rows = sorted(rows, key=lambda row: (str(row["competition_id"]), str(row["fixture_id"])))
                fixtures = [rt.hxg.FixtureRow(
                    str(row["fixture_id"]), str(row["competition_id"]), str(row["season"]), kickoff,
                    str(row["home_team_id"]), str(row["away_team_id"]),
                    str(row["home_team_name"]), str(row["away_team_name"]),
                ) for row in rows]
                if any(fixture.fixture_id in state.pending or fixture.fixture_id in state.seen for fixture in fixtures):
                    raise rt.RuntimeGateError("duplicate XG fixture freeze event")
                state.predict_batch(fixtures, include_matrix=False, lightweight=True)
                frozen_xg += len(fixtures)
    return {
        "applied_v1": applied_v1,
        "applied_xg": applied_xg,
        "frozen_xg": frozen_xg,
        "event_group_n": group_n,
        "as_of": as_of.isoformat(),
        "pending_xg_n": len(state.pending),
    }


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED and rt._apply_events is _apply_events:
        return {
            "schema_version": SCHEMA,
            "installed": True,
            "idempotent": True,
            "fast_late_release_policy": "FAIL_TO_TRUSTED_FULL_REBUILD",
            "full_late_release_policy": "REPLAY_AVAILABLE_V1_LABELS_IN_KICKOFF_ORDER",
            "exact_cutoff_release_inclusive": True,
            "exact_cutoff_freeze_exclusive": True,
        }
    rt._apply_events = _apply_events
    _INSTALLED = True
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "idempotent": True,
        "fast_late_release_policy": "FAIL_TO_TRUSTED_FULL_REBUILD",
        "full_late_release_policy": "REPLAY_AVAILABLE_V1_LABELS_IN_KICKOFF_ORDER",
        "event_group_cursor_monotone": True,
        "exact_cutoff_release_inclusive": True,
        "exact_cutoff_freeze_exclusive": True,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
