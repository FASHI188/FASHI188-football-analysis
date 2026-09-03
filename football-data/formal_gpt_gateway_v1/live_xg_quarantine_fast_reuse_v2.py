#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import live_delta_acquisition_v1 as live
import live_source_contract_resolution_v1 as src
import runtime as rt

SCHEMA = "football3-live-xg-quarantine-fast-reuse-v2"

# Frozen formal identity for the single explicit Rennes-PSG row-local XG quarantine.
# The fixture id is derived from the formal V1 identity and does not depend on result/xG values.
FORMAL_FIXTURE_ID = "c137e871d2dda8c5336dbf34"
FORMAL_SEASON = "2026/27"
FORMAL_KICKOFF = datetime(2026, 8, 23, tzinfo=timezone.utc)
_ORIGINAL = src._apply_role_reversal


def _matches_contract_row(r: Any) -> bool:
    c = src.ROLE_REVERSAL
    return (
        r.competition_id == c["competition_id"]
        and r.kickoff.date().isoformat() == c["date"]
        and r.home_team_name == c["formal_home"]
        and r.away_team_name == c["formal_away"]
    )


def _remove_carried_unjoined_audit(report: dict[str, Any]) -> None:
    c = src.ROLE_REVERSAL
    for s in report.get("sources", []):
        if s.get("competition_id") != c["competition_id"] or s.get("season_start") != 2026:
            continue
        old = list(s.get("unjoined_results") or [])
        kept = [
            row for row in old
            if not (
                row.get("date") == c["date"]
                and row.get("home") == c["source_home"]
                and row.get("away") == c["source_away"]
            )
        ]
        if len(kept) != len(old):
            s["unjoined_results"] = kept
            s["unjoined_result_n"] = len(kept)
        s.setdefault("carried_xg_quarantines", []).append({
            "schema_version": SCHEMA,
            "fixture_id": FORMAL_FIXTURE_ID,
            "formal_season": FORMAL_SEASON,
            "formal_kickoff": FORMAL_KICKOFF.isoformat(),
            "resolution": "DURABLE_STATE_CARRY_FORWARD",
            "requires_seen_formal_v1_fixture": True,
        })


def _apply_role_reversal(v1_rows, lower, ceiling, state: Any,
                         result_xg: dict[str, dict[str, Any]], freezes: list[dict[str, Any]], report: dict[str, Any]):
    formal = [r for r in v1_rows if _matches_contract_row(r)]
    if formal:
        return _ORIGINAL(v1_rows, lower, ceiling, state, result_xg, freezes, report)

    # FAST windows beginning after this historical row legitimately omit it from v1_rows.
    # Reuse is allowed only when the validated durable V1 state proves that exact formal fixture was already applied.
    seen_v1 = set(getattr(getattr(state, "base", None), "seen_fixtures", set()) or set())
    if lower > FORMAL_KICKOFF and FORMAL_FIXTURE_ID in seen_v1:
        result_xg.pop(FORMAL_FIXTURE_ID, None)
        freezes[:] = [e for e in freezes if str(e.get("fixture_id")) != FORMAL_FIXTURE_ID]
        qids = report.setdefault("quarantined_fixture_ids", [])
        if FORMAL_FIXTURE_ID not in qids:
            qids.append(FORMAL_FIXTURE_ID)
        carried = {
            "schema_version": SCHEMA,
            "fixture_id": FORMAL_FIXTURE_ID,
            "formal_season": FORMAL_SEASON,
            "formal_kickoff": FORMAL_KICKOFF.isoformat(),
            "resolution": "DURABLE_STATE_CARRY_FORWARD",
            "durable_v1_seen": True,
            "xg_row_remains_quarantined": True,
            "result_or_xg_used_to_select_identity": False,
            "model_parameters_or_weights_changed": False,
        }
        report.setdefault("carried_xg_quarantines", []).append(carried)
        _remove_carried_unjoined_audit(report)
        return None

    # No durable-state proof: retain the original strict fail-closed behavior.
    return _ORIGINAL(v1_rows, lower, ceiling, state, result_xg, freezes, report)


def install() -> dict[str, Any]:
    src._apply_role_reversal = _apply_role_reversal
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "formal_fixture_id": FORMAL_FIXTURE_ID,
        "policy": "historical quarantine may carry into FAST only when validated durable V1 state already contains the exact formal fixture; otherwise fail closed",
        "result_or_xg_used_to_select_identity": False,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
