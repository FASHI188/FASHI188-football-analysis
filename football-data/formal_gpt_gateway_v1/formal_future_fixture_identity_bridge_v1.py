#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from typing import Any

import live_gateway_patch_v1 as live_gateway
import permanent_team_identity_bridge_v1 as identity_bridge
import runtime as rt

import cross_season_team_state_binding_v1 as cross_season_identity
CROSS_SEASON_IDENTITY = cross_season_identity.install()

import cross_season_authority_contract_v1 as cross_season_authority

import formal_receipt_distribution_contract_v1 as receipt_distribution
RECEIPT_DISTRIBUTION = receipt_distribution.install()

SCHEMA = "football3-formal-future-fixture-identity-bridge-v1"
_BASE_RESOLVE_TEAM = identity_bridge.resolve_team
_BASE_RESOLVE_FIXTURE = identity_bridge.resolve_fixture


def _authority_season(repo_root: Path, comp: str, request_season: str) -> str:
    """Map only the current natural-year compatibility alias to its authority season.

    The model/request season remains unchanged. This exists for domains such as JPN_J1
    where the production request uses 2026/27 while the frozen participation authority
    deliberately uses the formal compatibility key 2026.
    """
    request_season = str(request_season or "").strip()
    expected = cross_season_identity.current_season(repo_root, comp)
    if request_season == expected:
        return expected
    if comp not in cross_season_identity.NATURAL_YEAR_COMPETITIONS:
        return request_season
    request_start, _ = rt._season_years(request_season)
    authority_start, _ = rt._season_years(expected)
    if request_start is not None and request_start == authority_start:
        return expected
    return request_season


def _alias_aware_resolve_team(repo_root: Path, state: Any, comp: str, season: str, requested: str):
    authority_season = _authority_season(repo_root, comp, season)
    ident = _BASE_RESOLVE_TEAM(repo_root, state, comp, authority_season, requested)
    if authority_season != season:
        ident = dict(ident)
        ident["request_season"] = season
        ident["authority_season"] = authority_season
        ident["season_alias_resolution"] = "NATURAL_YEAR_CURRENT_SEASON_COMPATIBILITY_KEY"
    return ident


def _alias_aware_resolve_fixture(repo_root: Path, state: Any, comp: str, season: str,
                                 home: str, away: str, kickoff):
    h = _alias_aware_resolve_team(repo_root, state, comp, season, home)
    a = _alias_aware_resolve_team(repo_root, state, comp, season, away)
    if h["strength_team_id"] == a["strength_team_id"]:
        raise rt.RuntimeGateError("resolved home/away strength identity collision")
    fixture = {
        "fixture_id": rt._fixture_id(comp, season, kickoff, home, away),
        "competition_id": comp,
        "season": season,
        "kickoff": kickoff.isoformat(),
        "home_team_id": h["strength_team_id"],
        "away_team_id": a["strength_team_id"],
        "home_team_name": home,
        "away_team_name": away,
    }
    authority_season = _authority_season(repo_root, comp, season)
    audit = {
        "schema_version": cross_season_identity.SCHEMA,
        "competition_id": comp,
        "season": season,
        "authority_season": authority_season,
        "fixture_id": fixture["fixture_id"],
        "home": h,
        "away": a,
        "identity_selection_uses_result_or_xg": False,
        "fuzzy_cross_club_substitution": False,
        "request_season_preserved": True,
    }
    audit["mapping_sha256"] = cross_season_identity._sha(audit)
    return fixture, audit


def _install_season_alias_bridge() -> dict[str, Any]:
    identity_bridge.resolve_team = _alias_aware_resolve_team
    identity_bridge.resolve_fixture = _alias_aware_resolve_fixture
    return {
        "schema_version": "football3-natural-year-authority-season-alias-v1",
        "installed": True,
        "natural_year_competitions": sorted(cross_season_identity.NATURAL_YEAR_COMPETITIONS),
        "request_season_preserved": True,
        "authority_season_separate": True,
        "same_start_year_required": True,
        "historical_season_aliasing_allowed": False,
        "club_specific_runtime_branching": False,
        "model_parameters_or_weights_changed": False,
    }


SEASON_ALIAS_CONTRACT = _install_season_alias_bridge()


def _resolve(repo_root: Path, state_root: Path, comp: str, season: str,
             home: str, away: str, kickoff):
    loaded = rt.validate_bundle(state_root / "bundle")
    fixture, audit = identity_bridge.resolve_fixture(
        repo_root, loaded["state"], comp, season, home, away, kickoff
    )
    audit["authority_contract"] = cross_season_authority.validate_fixture(repo_root, audit)
    return fixture, audit


def install(gateway_module) -> dict[str, Any]:
    """Canonicalize future/absent formal fixture team IDs from frozen state only."""
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
        "identity_source": "validated_frozen_state_plus_cross_season_permanent_identity_bridge",
        "cross_season_identity_contract": CROSS_SEASON_IDENTITY,
        "season_alias_contract": SEASON_ALIAS_CONTRACT,
        "cross_season_authority_contract": cross_season_authority.SCHEMA,
        "receipt_distribution_contract": RECEIPT_DISTRIBUTION,
        "result_or_xg_identity_selection": False,
        "fuzzy_cross_club_substitution": False,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
