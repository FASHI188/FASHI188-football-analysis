#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from typing import Any

import permanent_team_identity_bridge_v1 as identity_bridge
import runtime as rt

SCHEMA = "football3-formal-future-fixture-identity-bridge-v1"


def install(gateway_module) -> dict[str, Any]:
    """Canonicalize only future/absent formal fixture IDs from the validated frozen state.

    The original request names and fixture timing are preserved. Team IDs are resolved
    through the permanent exact/registry identity bridge. No result, xG, fuzzy, score,
    or post-target information participates in identity selection.
    """
    original = gateway_module.normal_request

    def normal_request(req: dict[str, Any], state_root: Path, out: Path, repo_root: Path,
                       understat_db: Path, confirmation_dir: Path) -> dict[str, Any]:
        original_make_future = gateway_module.make_future_fixture

        def canonical_make_future_fixture(inner_req: dict[str, Any]):
            raw_fixture, kickoff, cutoff = original_make_future(inner_req)
            m = inner_req.get("match")
            if type(m) is not dict:
                return raw_fixture, kickoff, cutoff
            comp = str(m.get("competition_id") or "")
            season = str(m.get("season") or "")
            home = str(m.get("home_team_name") or "").strip()
            away = str(m.get("away_team_name") or "").strip()
            loaded = rt.validate_bundle(state_root / "bundle")
            fixture, _identity = identity_bridge.resolve_fixture(
                repo_root, loaded["state"], comp, season, home, away, kickoff
            )
            # Preserve request-facing names/time/fixture identity while replacing only
            # canonical team IDs with frozen-state identity. resolve_fixture already
            # preserves those request-facing fields and fails closed on ambiguity.
            return fixture, kickoff, cutoff

        gateway_module.make_future_fixture = canonical_make_future_fixture
        try:
            return original(req, state_root, out, repo_root, understat_db, confirmation_dir)
        finally:
            gateway_module.make_future_fixture = original_make_future

    gateway_module.normal_request = normal_request
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "scope": "future_or_absent_formal_fixture_identity_only",
        "request_names_preserved": True,
        "fixture_time_preserved": True,
        "identity_source": "validated_frozen_state_plus_permanent_team_identity_bridge",
        "result_or_xg_identity_selection": False,
        "fuzzy_cross_club_substitution": False,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
