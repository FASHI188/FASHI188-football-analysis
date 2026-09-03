#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from typing import Any

import live_delta_acquisition_v1 as live
import runtime as rt

SCHEMA = "football3-live-source-failure-probe-v1"


def install(gateway_module) -> dict[str, Any]:
    original = gateway_module.missing_data_probe

    def missing_data_probe(req: dict[str, Any], state_root: Path, out: Path, repo_root: Path,
                           understat_db: Path, confirmation_dir: Path) -> dict[str, Any]:
        if not bool(req.get("simulate_live_source_failure")):
            return original(req, state_root, out, repo_root, understat_db, confirmation_dir)

        bundle = state_root / "bundle"
        before = rt.validate_bundle(bundle)
        before_bundle_sha = str(before["manifest"]["state_bundle_sha256"])
        before_state_sha = str(before["manifest"]["state_sha256"])
        lower = rt._parse_dt(str(before["meta"]["historical_cutoff"]), "cached cutoff")
        fixture, kickoff, cutoff = gateway_module.make_future_fixture(req)
        failure_comp = str(req.get("failure_competition_id") or "FRA_Ligue1")
        failure_start = int(req.get("failure_season_start") or 2026)
        if failure_comp not in live.UNDERSTAT:
            raise rt.RuntimeGateError("live-source-failure probe competition is not an Understat-designated formal source")

        original_payload = live._understat_payload
        hit = {"n": 0}

        def failing_payload(comp: str, league: str, start: int):
            if comp == failure_comp and int(start) == failure_start:
                hit["n"] += 1
                raise RuntimeError("SIMULATED_FORMAL_LIVE_SOURCE_UNAVAILABLE")
            return original_payload(comp, league, start)

        live._understat_payload = failing_payload
        try:
            try:
                live.acquire_verified_delta(repo_root, lower, cutoff, fixture["fixture_id"], before["state"])
            except live.AcquisitionError as exc:
                reason = str(exc)
                report = exc.report
            else:
                raise rt.RuntimeGateError("live-source-failure probe unexpectedly acquired VERIFIED_COMPLETE delta")
        finally:
            live._understat_payload = original_payload

        after = rt.validate_bundle(bundle)
        after_bundle_sha = str(after["manifest"]["state_bundle_sha256"])
        after_state_sha = str(after["manifest"]["state_sha256"])
        if hit["n"] < 1:
            raise rt.RuntimeGateError("live-source-failure probe did not intercept designated source")
        if "FORMAL_INPUT_DATA_INCOMPLETE: Understat acquisition failed" not in reason:
            raise rt.RuntimeGateError(f"live-source-failure probe failed with unexpected reason: {reason}")
        if before_bundle_sha != after_bundle_sha or before_state_sha != after_state_sha:
            raise rt.RuntimeGateError("live-source-failure probe mutated durable state")
        if (out / "prediction_receipt.json").exists():
            raise rt.RuntimeGateError("live-source-failure probe unexpectedly produced prediction receipt")

        result = {
            "status": "PASS_EXPECTED_FAIL_CLOSED",
            "probe": "LIVE_SOURCE_FAILURE_FAIL_CLOSED",
            "fixture": fixture,
            "cutoff": cutoff.isoformat(),
            "cached_cutoff": lower.isoformat(),
            "failure_competition_id": failure_comp,
            "failure_season_start": failure_start,
            "simulated_source_failure_hits": hit["n"],
            "reason": reason,
            "acquisition_report": report,
            "state_unchanged": True,
            "state_bundle_sha_before": before_bundle_sha,
            "state_bundle_sha_after": after_bundle_sha,
            "state_sha_before": before_state_sha,
            "state_sha_after": after_state_sha,
            "prediction_sha": None,
            "receipt_sha": None,
            "manual_or_auxiliary_fallback_used": False,
        }
        gateway_module.write_json(out / "formal_gap.json", result)
        gateway_module.write_json(out / "live_source_failure_probe.json", result)
        return result

    gateway_module.missing_data_probe = missing_data_probe
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "probe_only": True,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
