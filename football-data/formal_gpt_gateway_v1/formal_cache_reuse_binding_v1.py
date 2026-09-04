#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from typing import Any

import formal_state_integrity_guard_v1 as guard
import permanent_team_identity_bridge_v1 as identity_bridge
import runtime as rt

SCHEMA = "football3-formal-cache-reuse-binding-v1"


def install(gateway_module) -> dict[str, Any]:
    """Seal a guard binding after the existing trusted cache-reuse probe succeeds.

    This does not make an unbound cache FAST-eligible. It creates the same binding the
    integrity guard requires, using the post-update validated state and exact fixture /
    cutoff produced by the trusted cache-reuse path.
    """
    original = gateway_module.cache_reuse_probe

    def cache_reuse_probe(req: dict[str, Any], state_root: Path, out: Path, repo_root: Path,
                          understat_db: Path, confirmation_dir: Path) -> dict[str, Any]:
        result = original(req, state_root, out, repo_root, understat_db, confirmation_dir)
        if result.get("status") != "PASS" or result.get("calculation_path") != "FAST_PATH":
            return result
        fixture = result.get("fixture")
        if type(fixture) is not dict:
            raise rt.RuntimeGateError("cache reuse PASS missing fixture")
        cutoff = rt._parse_dt(str(result.get("cutoff") or ""), "cache reuse cutoff")
        loaded = rt.validate_bundle(state_root / "bundle")
        resolved, ident = identity_bridge.resolve_fixture(
            repo_root,
            loaded["state"],
            str(fixture.get("competition_id") or ""),
            str(fixture.get("season") or ""),
            str(fixture.get("home_team_name") or ""),
            str(fixture.get("away_team_name") or ""),
            rt._parse_dt(str(fixture.get("kickoff") or ""), "cache reuse kickoff"),
        )
        if resolved["home_team_id"] != fixture.get("home_team_id") or resolved["away_team_id"] != fixture.get("away_team_id"):
            raise rt.RuntimeGateError("cache reuse fixture canonical identity mismatch")
        binding = guard._binding_payload(
            loaded,
            resolved["competition_id"],
            resolved["season"],
            cutoff,
            ident,
        )
        guard._write_json(state_root / "fast_cache_binding_v1.json", binding)
        guard._write_json(out / "fast_cache_binding_v1.json", binding)
        result["fast_cache_binding_sha256"] = binding["binding_sha256"]
        result["fast_cache_binding_created"] = True
        return result

    gateway_module.cache_reuse_probe = cache_reuse_probe
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "scope": "trusted_cache_reuse_probe_post_success_only",
        "unbound_cache_fast_eligible": False,
        "validated_state_required": True,
        "exact_fixture_identity_required": True,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
