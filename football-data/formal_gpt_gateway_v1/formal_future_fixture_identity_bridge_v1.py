#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from typing import Any

import live_gateway_patch_v1 as live_gateway
import permanent_team_identity_bridge_v1 as identity_bridge
import runtime as rt

SCHEMA = "football3-formal-future-fixture-identity-bridge-v1"


def _resolve(repo_root: Path, state_root: Path, comp: str, season: str,
             home: str, away: str, kickoff):
    loaded = rt.validate_bundle(state_root / "bundle")
    return identity_bridge.resolve_fixture(
        repo_root, loaded["state"], comp, season, home, away, kickoff
    )


def install(gateway_module) -> dict[str, Any]:
    """Canonicalize future/absent formal fixture team IDs from frozen state only.

    The live gateway may create a provisional raw-name fixture before it has acquired
    and sealed the requested state. We therefore leave that provisional identity alone
    for source acquisition/exclusion, but replace the fixture in the sealed runtime input
    after the state has been validated. No result, xG value, fuzzy score or post-target
    information participates in identity selection.
    """
    original = gateway_module.normal_request

    def normal_request(req: dict[str, Any], state_root: Path, out: Path, repo_root: Path,
                       understat_db: Path, confirmation_dir: Path) -> dict[str, Any]:
        original_make_future = gateway_module.make_future_fixture
        original_sealed_input = live_gateway._sealed_input
        identity_used: dict[str, Any] | None = None

        def canonical_make_future_fixture(inner_req: dict[str, Any]):
            raw_fixture, kickoff, cutoff = original_make_future(inner_req)
            m = inner_req.get("match")
            if type(m) is not dict:
                return raw_fixture, kickoff, cutoff
            fixture, _identity = _resolve(
                repo_root, state_root,
                str(m.get("competition_id") or ""),
                str(m.get("season") or ""),
                str(m.get("home_team_name") or "").strip(),
                str(m.get("away_team_name") or "").strip(),
                kickoff,
            )
            return fixture, kickoff, cutoff

        def canonical_sealed_input(raw_fixture: dict[str, Any], cutoff, report: dict[str, Any]):
            nonlocal identity_used
            fixture, identity_used = _resolve(
                repo_root, state_root,
                str(raw_fixture.get("competition_id") or ""),
                str(raw_fixture.get("season") or ""),
                str(raw_fixture.get("home_team_name") or "").strip(),
                str(raw_fixture.get("away_team_name") or "").strip(),
                rt._parse_dt(str(raw_fixture.get("kickoff") or ""), "fixture kickoff"),
            )
            return original_sealed_input(fixture, cutoff, report)

        gateway_module.make_future_fixture = canonical_make_future_fixture
        live_gateway._sealed_input = canonical_sealed_input
        try:
            result = original(req, state_root, out, repo_root, understat_db, confirmation_dir)
            if result.get("status") == "PASS" and type(result.get("fixture")) is dict:
                f = result["fixture"]
                fixture, identity_used = _resolve(
                    repo_root, state_root,
                    str(f.get("competition_id") or ""),
                    str(f.get("season") or ""),
                    str(f.get("home_team_name") or "").strip(),
                    str(f.get("away_team_name") or "").strip(),
                    rt._parse_dt(str(f.get("kickoff") or ""), "fixture kickoff"),
                )
                result["fixture"] = fixture
                if identity_used is not None:
                    gateway_module.write_json(out / "team_identity_bridge.json", identity_used)
            return result
        finally:
            gateway_module.make_future_fixture = original_make_future
            live_gateway._sealed_input = original_sealed_input

    gateway_module.normal_request = normal_request
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "scope": "future_or_absent_formal_fixture_identity_only",
        "provisional_live_acquisition_fixture_preserved": True,
        "sealed_runtime_input_uses_canonical_identity": True,
        "request_names_preserved": True,
        "fixture_time_preserved": True,
        "identity_source": "validated_frozen_state_plus_permanent_team_identity_bridge",
        "result_or_xg_identity_selection": False,
        "fuzzy_cross_club_substitution": False,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
