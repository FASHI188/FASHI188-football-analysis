#!/usr/bin/env python3
"""V6.20.6 fail-closed context residual gate.

Context is optional residual information, never a prerequisite for the independent
1X2 / total / score base tracks. Missing or stale context produces zero context
adjustment rather than prior-season backfill or fabricated values.
"""
from __future__ import annotations
from typing import Any


def context_residual_eligibility(evidence: dict[str, Any]) -> dict[str, Any]:
    roster_ok = str(evidence.get('roster_state') or '') == 'STRICT_CURRENT'
    manager_ok = bool(evidence.get('manager_verified_current'))
    availability_ok = bool(evidence.get('availability_time_valid'))
    xg_ok = bool(evidence.get('xg_panel_hash_verified')) and str(evidence.get('xg_panel_status') or '') == 'PASS'
    enabled = {
        'roster_residual': roster_ok,
        'manager_residual': manager_ok,
        'availability_residual': availability_ok,
        'xg_residual': xg_ok,
    }
    unavailable = [k for k,v in enabled.items() if not v]
    return {
        'base_tracks_executable': True,
        'enabled_residuals': enabled,
        'unavailable_residuals': unavailable,
        'missing_residual_value': 0.0,
        'prior_season_roster_backfill_allowed': False,
        'unverified_manager_backfill_allowed': False,
        'live_or_postmatch_xg_backfill_allowed': False,
        'probability_fabrication_allowed': False,
        'status': 'PASS' if not unavailable else 'DEGRADED_CONTEXT_ONLY',
    }
