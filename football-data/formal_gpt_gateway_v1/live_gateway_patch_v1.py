#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import live_delta_acquisition_v1 as live
import runtime as rt


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(rt._canon_bytes(obj))


def _fixture(comp: str, season: str, home: str, away: str, kickoff) -> dict[str, Any]:
    return {
        "fixture_id": rt._fixture_id(comp, season, kickoff, home, away),
        "competition_id": comp,
        "season": season,
        "kickoff": kickoff.isoformat(),
        "home_team_id": rt._global_team_id(home),
        "away_team_id": rt._global_team_id(away),
        "home_team_name": home,
        "away_team_name": away,
    }


def _build_frozen_base(bundle: Path, repo_root: Path, understat_db: Path, confirmation_dir: Path) -> dict[str, Any]:
    state, source, identity = rt.build_production_state(repo_root, understat_db, confirmation_dir)
    manifest = rt.seal_bundle(state, bundle, source, identity, rt.BASE_HISTORY_CUTOFF, "FULL_REBUILD_PATH")
    return {"manifest": manifest, "state": rt.validate_bundle(bundle)["state"], "cutoff": rt._parse_dt(rt.BASE_HISTORY_CUTOFF, "base cutoff")}


def _complete_input(fixture: dict[str, Any], lower, cutoff, delta: list[dict[str, Any]], report: dict[str, Any]) -> dict[str, Any]:
    coverage = {
        "schema_version": rt.DELTA_SCHEMA,
        "status": "COMPLETE",
        "verification": "VERIFIED_COMPLETE",
        "v1_status": "COMPLETE",
        "xg_status": "COMPLETE",
        "from": lower.isoformat(),
        "to": cutoff.isoformat(),
        "records_sha256": rt._sha_bytes(rt._canon_bytes(delta)),
        "source_set_sha256": report["source_set_sha256"],
        "acquisition_schema": report["schema_version"],
        "acquisition_observed_at": report["observed_at"],
    }
    return {"schema_version": rt.INPUT_SCHEMA, "fixture": fixture, "cutoff": cutoff.isoformat(), "delta_coverage": coverage, "model_delta": delta}


def install(gateway_module) -> dict[str, Any]:
    original = gateway_module.normal_request

    def normal_request(req: dict[str, Any], state_root: Path, out: Path, repo_root: Path,
                       understat_db: Path, confirmation_dir: Path) -> dict[str, Any]:
        m = req.get("match")
        if type(m) is not dict:
            raise rt.RuntimeGateError("prediction request missing match")
        comp = str(m.get("competition_id") or "")
        season = str(m.get("season") or "")
        home = str(m.get("home_team_name") or "").strip()
        away = str(m.get("away_team_name") or "").strip()
        kickoff = rt._parse_dt(str(m.get("kickoff") or ""), "kickoff")
        cutoff = rt._parse_dt(str(m.get("cutoff") or ""), "cutoff")
        if comp not in rt.FORMAL_SCOPE:
            raise rt.RuntimeGateError("competition outside Formal Fusion V2 scope")
        if cutoff > kickoff - timedelta(minutes=60):
            raise rt.RuntimeGateError("formal cutoff violates T_minus_60_minutes_or_earlier")
        if not season or not home or not away:
            raise rt.RuntimeGateError("canonical fixture identity incomplete")

        # Preserve the already-proven frozen historical path unchanged.
        base_limit = rt._parse_dt(rt.BASE_HISTORY_CUTOFF, "base history cutoff")
        if cutoff <= base_limit:
            return original(req, state_root, out, repo_root, understat_db, confirmation_dir)

        # Prospective live acquisition is legal only while the acquisition itself is before the requested cutoff.
        fixture = _fixture(comp, season, home, away, kickoff)
        bundle = state_root / "bundle"
        gateway_route = "FAST_ACQUIRE"
        fast_attempt_gap = None

        # A durable cache older than the immutable production base is not a live production state.
        try:
            loaded = rt.validate_bundle(bundle)
            lower = rt._parse_dt(loaded["meta"]["historical_cutoff"], "cached cutoff")
            if lower < base_limit or lower > cutoff:
                raise rt.RuntimeGateError("cached state outside live continuity window")
        except rt.RuntimeGateError:
            built = _build_frozen_base(bundle, repo_root, understat_db, confirmation_dir)
            loaded = rt.validate_bundle(bundle)
            lower = built["cutoff"]
            gateway_route = "FULL_ACQUIRE_REBUILD"

        def acquire_from_current_state():
            current = rt.validate_bundle(bundle)
            state = current["state"]
            current_lower = rt._parse_dt(current["meta"]["historical_cutoff"], "bundle cutoff")
            delta, report = live.acquire_verified_delta(repo_root, current_lower, cutoff, fixture["fixture_id"], state)
            return current_lower, delta, report

        try:
            lower, delta, report = acquire_from_current_state()
        except live.AcquisitionError as exc:
            fast_attempt_gap = {"reason": str(exc), "report": exc.report}
            # Missing/continuity failure does not stop at FAST: rebuild the immutable base and reacquire the complete interval.
            _build_frozen_base(bundle, repo_root, understat_db, confirmation_dir)
            gateway_route = "FULL_ACQUIRE_REBUILD"
            try:
                lower, delta, report = acquire_from_current_state()
            except live.AcquisitionError as full_exc:
                gap = {
                    "schema_version": "football3-formal-live-gap-v1",
                    "status": "FORMAL_INPUT_DATA_INCOMPLETE",
                    "probe": "PREDICTION",
                    "fixture": fixture,
                    "cutoff": cutoff.isoformat(),
                    "gateway_route": gateway_route,
                    "fast_attempt": fast_attempt_gap,
                    "full_attempt": {"reason": str(full_exc), "report": full_exc.report},
                    "prediction_sha": None,
                    "receipt_sha": None,
                    "manual_or_auxiliary_fallback_used": False,
                }
                _write_json(out / "formal_gap.json", gap)
                _write_json(out / "live_acquisition_report.json", full_exc.report)
                return gap

        _write_json(out / "live_acquisition_report.json", report)
        inp = _complete_input(fixture, lower, cutoff, delta, report)
        input_path = out / "runtime_input.json"
        _write_json(input_path, inp)

        # Execute the frozen original runner. The gateway route records whether the state was reused or rebuilt;
        # the runner receipt keeps its own FAST/FULL calculation_path unchanged.
        try:
            receipt = rt.predict_match(comp, home, away, kickoff, cutoff, bundle, input_path,
                                       repo_root, understat_db, confirmation_dir, False)
        except rt.RuntimeGateError as exc:
            gap = {
                "schema_version": "football3-formal-live-gap-v1",
                "status": "FORMAL_INPUT_DATA_INCOMPLETE",
                "probe": "PREDICTION",
                "fixture": fixture,
                "cutoff": cutoff.isoformat(),
                "gateway_route": gateway_route,
                "reason": str(exc),
                "acquisition": report,
                "prediction_sha": None,
                "receipt_sha": None,
                "manual_or_auxiliary_fallback_used": False,
            }
            _write_json(out / "formal_gap.json", gap)
            return gap

        _write_json(out / "prediction_receipt.json", receipt)
        return {
            "status": "PASS",
            "probe": "PREDICTION",
            "fixture": fixture,
            "cutoff": cutoff.isoformat(),
            "gateway_route": gateway_route,
            "calculation_path": receipt["calculation_path"],
            "model_route": receipt["model_route"],
            "fallback_exact_v1": receipt["fallback_exact_v1"],
            "input_sha": receipt["runtime_input_sha"],
            "state_sha": receipt["state_sha256"],
            "state_bundle_sha": receipt["state_bundle_sha"],
            "prediction_sha": receipt["prediction_sha"],
            "receipt_sha": receipt["receipt_sha"],
            "acquisition_records": report["records"],
            "acquisition_source_set_sha256": report["source_set_sha256"],
            "acquisition_observed_at": report["observed_at"],
            "manual_or_auxiliary_fallback_used": False,
        }

    gateway_module.normal_request = normal_request
    return {"schema_version": "football3-live-gateway-patch-v1", "installed": True,
            "frozen_runtime_head": gateway_module.BASELINE_RUNTIME_HEAD,
            "base_history_cutoff": rt.BASE_HISTORY_CUTOFF}
