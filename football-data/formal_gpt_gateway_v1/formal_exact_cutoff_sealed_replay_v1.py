#!/usr/bin/env python3
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import permanent_team_identity_bridge_v1 as identity_bridge
import runtime as rt

SCHEMA = "football3-formal-exact-cutoff-sealed-replay-v1"


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(rt._canon_bytes(obj))


def _source_set_sha(loaded: dict[str, Any]) -> str:
    source = loaded.get("source") or {}
    queue = [source]
    while queue:
        item = queue.pop(0)
        if type(item) is not dict:
            continue
        live_acq = item.get("live_acquisition")
        if type(live_acq) is dict and live_acq.get("source_set_sha256"):
            return str(live_acq["source_set_sha256"])
        prior = item.get("prior_state_source")
        if type(prior) is dict:
            queue.append(prior)
    return rt._sha_bytes(rt._canon_bytes(source))


def _sealed_input(fixture: dict[str, Any], cutoff, loaded: dict[str, Any]) -> dict[str, Any]:
    empty: list[dict[str, Any]] = []
    coverage = {
        "schema_version": rt.DELTA_SCHEMA,
        "status": "COMPLETE",
        "verification": "VERIFIED_COMPLETE",
        "v1_status": "COMPLETE",
        "xg_status": "COMPLETE",
        "from": cutoff.isoformat(),
        "to": cutoff.isoformat(),
        "records_sha256": rt._sha_bytes(rt._canon_bytes(empty)),
        "source_set_sha256": _source_set_sha(loaded),
        "acquisition_schema": "SEALED_EXACT_CUTOFF_REPLAY",
        "acquisition_observed_at": cutoff.isoformat(),
        "state_preapplied_and_sealed": True,
    }
    return {
        "schema_version": rt.INPUT_SCHEMA,
        "fixture": fixture,
        "cutoff": cutoff.isoformat(),
        "delta_coverage": coverage,
        "model_delta": empty,
    }


def install(gateway_module) -> dict[str, Any]:
    """Reuse an already validated bundle sealed exactly at the requested cutoff.

    This route is intentionally source-silent. It cannot advance a state, repair a state,
    or create a cache. Those responsibilities remain with the integrity guard/full rebuild.
    It only prevents an exact sealed historical/prematch state from being needlessly sent
    back through prospective live acquisition after its bytes have already been frozen.
    """
    original = gateway_module.normal_request

    def normal_request(req: dict[str, Any], state_root: Path, out: Path, repo_root: Path,
                       understat_db: Path, confirmation_dir: Path) -> dict[str, Any]:
        m = req.get("match")
        if type(m) is not dict:
            return original(req, state_root, out, repo_root, understat_db, confirmation_dir)
        comp = str(m.get("competition_id") or "")
        season = str(m.get("season") or "")
        home = str(m.get("home_team_name") or "").strip()
        away = str(m.get("away_team_name") or "").strip()
        if comp not in rt.FORMAL_SCOPE or not season or not home or not away:
            return original(req, state_root, out, repo_root, understat_db, confirmation_dir)
        kickoff = rt._parse_dt(str(m.get("kickoff") or ""), "kickoff")
        cutoff = rt._parse_dt(str(m.get("cutoff") or ""), "cutoff")
        if cutoff > kickoff - timedelta(minutes=60):
            raise rt.RuntimeGateError("formal cutoff violates T_minus_60_minutes_or_earlier")
        if cutoff <= rt._parse_dt(rt.BASE_HISTORY_CUTOFF, "base history cutoff"):
            return original(req, state_root, out, repo_root, understat_db, confirmation_dir)

        bundle = state_root / "bundle"
        try:
            loaded = rt.validate_bundle(bundle)
            sealed_cutoff = rt._parse_dt(str(loaded["meta"]["historical_cutoff"]), "bundle cutoff")
        except rt.RuntimeGateError:
            return original(req, state_root, out, repo_root, understat_db, confirmation_dir)
        if sealed_cutoff != cutoff:
            return original(req, state_root, out, repo_root, understat_db, confirmation_dir)

        before_bundle_sha = str(loaded["manifest"]["state_bundle_sha256"])
        fixture, identity = identity_bridge.resolve_fixture(
            repo_root, loaded["state"], comp, season, home, away, kickoff
        )
        inp = _sealed_input(fixture, cutoff, loaded)
        input_path = out / "runtime_input.json"
        _write_json(input_path, inp)
        _write_json(out / "team_identity_bridge.json", identity)
        receipt = rt.predict_match(
            comp, home, away, kickoff, cutoff, bundle, input_path,
            repo_root, understat_db, confirmation_dir, False,
        )
        after = rt.validate_bundle(bundle)
        after_bundle_sha = str(after["manifest"]["state_bundle_sha256"])
        if after_bundle_sha != before_bundle_sha:
            raise rt.RuntimeGateError("exact-cutoff sealed replay mutated durable state")
        _write_json(out / "prediction_receipt.json", receipt)
        return {
            "status": "PASS",
            "probe": "PREDICTION",
            "fixture": fixture,
            "requested_ceiling": cutoff.isoformat(),
            "cutoff": cutoff.isoformat(),
            "gateway_route": "SEALED_EXACT_CUTOFF_REPLAY",
            "calculation_path": receipt["calculation_path"],
            "model_route": receipt["model_route"],
            "fallback_exact_v1": receipt["fallback_exact_v1"],
            "input_sha": receipt["runtime_input_sha"],
            "state_sha": receipt["state_sha256"],
            "state_bundle_sha": receipt["state_bundle_sha"],
            "prediction_sha": receipt["prediction_sha"],
            "receipt_sha": receipt["receipt_sha"],
            "acquisition_records": 0,
            "acquisition_source_set_sha256": _source_set_sha(loaded),
            "acquisition_observed_at": cutoff.isoformat(),
            "source_refetch_used": False,
            "state_mutated": False,
            "manual_or_auxiliary_fallback_used": False,
        }

    gateway_module.normal_request = normal_request
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "scope": "validated_bundle_exact_cutoff_only",
        "source_refetch_used": False,
        "state_advance_allowed": False,
        "state_mutation_allowed": False,
        "unique_canonical_identity_required": True,
        "result_or_xg_identity_selection": False,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
