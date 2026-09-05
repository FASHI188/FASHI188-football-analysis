#!/usr/bin/env python3
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

import runtime as rt

SCHEMA = "football3-formal-v1-release-order-replay-v1"
_INSTALLED = False


def _v1_fixture(row: dict[str, Any], kickoff: datetime):
    return rt.v1_engine.Fixture(
        str(row["fixture_id"]), str(row["competition_id"]), str(row["season"]), kickoff,
        str(row["home_team_id"]), str(row["away_team_id"]),
    )


def _rebuild_v1_from_available(state, available: dict[str, dict[str, Any]]) -> None:
    """Rebuild Frozen V1 from labels actually available at the current PIT boundary.

    Frozen V1 is kickoff-ordered. A delayed authoritative settlement can become
    available after newer fixtures have already updated the live state. In a FULL
    replay we can deterministically reconstruct the V1 component from every label
    observed so far, preserving availability gating while restoring kickoff order.
    """
    rebuilt = rt.v1_engine.EngineState(params=state.base.params)
    by_kickoff: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
    for row in available.values():
        by_kickoff[rt._parse_dt(str(row["kickoff"]), "kickoff")].append(row)
    for kickoff, rows in sorted(by_kickoff.items()):
        fixtures = [_v1_fixture(row, kickoff) for row in rows]
        labels = {
            str(row["fixture_id"]): (int(row["home_goals"]), int(row["away_goals"]))
            for row in rows
        }
        rebuilt.apply_batch(fixtures, labels)
    state.base = rebuilt


def _apply_events(state, events: list[dict[str, Any]], as_of):
    order = {"LABEL_RELEASE": 0, "FIXTURE_FREEZE": 1}
    events = sorted(events, key=lambda e: (
        rt._parse_dt(str(e["event_at"]), "event_at"), order[str(e["event_type"])],
        rt._parse_dt(str(e["kickoff"]), "kickoff"), str(e["competition_id"]), str(e["fixture_id"]),
        0 if e.get("enters_xg") and not e.get("enters_v1") else 1,
    ))

    # If the incoming state is already populated, this invocation is an incremental
    # transition. We can apply monotone releases directly, but an older kickoff
    # arriving late must fail the FAST path so the caller performs a trusted FULL
    # rebuild; the delta alone cannot reconstruct labels that predate the base state.
    initial_v1_seen = set(state.base.seen_fixtures)
    available_v1: dict[str, dict[str, Any]] = {}
    applied_v1 = applied_xg = frozen_xg = 0
    i = 0
    while i < len(events):
        at = rt._parse_dt(str(events[i]["event_at"]), "event_at")
        same: list[dict[str, Any]] = []
        while i < len(events) and rt._parse_dt(str(events[i]["event_at"]), "event_at") == at:
            same.append(events[i]); i += 1

        releases = [e for e in same if e["event_type"] == "LABEL_RELEASE"]
        freezes = [e for e in same if e["event_type"] == "FIXTURE_FREEZE"]
        if releases:
            # XG labels are applied against the exact prediction frozen at kickoff;
            # update_base=False keeps the V1 settlement semantics independent.
            by_kickoff_xg: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
            for row in releases:
                if row["enters_xg"]:
                    by_kickoff_xg[rt._parse_dt(str(row["kickoff"]), "kickoff")].append(row)
            for kickoff, rows in sorted(by_kickoff_xg.items()):
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
                        fixtures = [_v1_fixture(row, kickoff) for row in rows]
                        labels = {
                            str(row["fixture_id"]): (int(row["home_goals"]), int(row["away_goals"]))
                            for row in rows
                        }
                        state.base.apply_batch(fixtures, labels)
                applied_v1 += len(vrows)

        if freezes:
            by_kickoff: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
            for row in freezes:
                by_kickoff[rt._parse_dt(str(row["kickoff"]), "kickoff")].append(row)
            for kickoff, rows in sorted(by_kickoff.items()):
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
        }
    rt._apply_events = _apply_events
    _INSTALLED = True
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "idempotent": True,
        "fast_late_release_policy": "FAIL_TO_TRUSTED_FULL_REBUILD",
        "full_late_release_policy": "REPLAY_AVAILABLE_V1_LABELS_IN_KICKOFF_ORDER",
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
