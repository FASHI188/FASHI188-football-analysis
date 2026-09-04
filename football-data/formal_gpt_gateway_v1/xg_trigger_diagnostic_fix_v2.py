#!/usr/bin/env python3
from __future__ import annotations

from typing import Any

import runtime as rt
from historical_xg_challenger_v1 import historical_xg_challenger as hxg
import target_identity_replay_fix_v1 as target_fix

SCHEMA = "football3-xg-trigger-diagnostic-fix-v2"


def _audit(state: Any, fixture: dict[str, Any]) -> dict[str, Any]:
    """Read the frozen challenger's trigger metadata from a state clone only.

    The challenger's lightweight prediction mode intentionally omits `dynamic` metadata.
    This diagnostic therefore requests the ordinary metadata-bearing prediction while
    still disabling the score matrix. The clone is never applied back to persisted state.
    """
    clone = rt.deserialize_state(rt.serialize_v1_state(state.base), rt.serialize_xg_state(state))
    f = hxg.FixtureRow(
        fixture["fixture_id"], fixture["competition_id"], fixture["season"],
        rt._parse_dt(fixture["kickoff"], "fixture kickoff"),
        fixture["home_team_id"], fixture["away_team_id"],
        fixture["home_team_name"], fixture["away_team_name"],
    )
    xg_predictions, _ = clone.predict_batch([f], include_matrix=False)
    if len(xg_predictions) != 1:
        raise rt.RuntimeGateError("XG trigger diagnostic cardinality mismatch")
    row = xg_predictions[0]
    dynamic = row.get("dynamic")
    if type(dynamic) is not dict:
        raise rt.RuntimeGateError(
            "XG trigger diagnostic metadata missing from metadata-bearing challenger prediction"
        )
    return {
        "schema_version": "football3-xg-trigger-audit-v2",
        "fixture_id": fixture["fixture_id"],
        "dynamic": dynamic,
        "frozen_min_effective_evidence": 3.0,
        "diagnostic_prediction_keys": sorted(str(k) for k in row.keys()),
        "diagnostic_state_clone_only": True,
        "score_matrix_requested": False,
        "lightweight_mode_used": False,
        "persisted_state_mutated": False,
        "model_parameters_or_weights_changed": False,
    }


def install() -> dict[str, Any]:
    target_fix._xg_trigger_audit = _audit
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "scope": "diagnostic metadata extraction only",
        "challenger_prediction_mode": "metadata-bearing; score matrix disabled",
        "diagnostic_state_clone_only": True,
        "persisted_state_mutated": False,
        "model_parameters_or_weights_changed": False,
    }
