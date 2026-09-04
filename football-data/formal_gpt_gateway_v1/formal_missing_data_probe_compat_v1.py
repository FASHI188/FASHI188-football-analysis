#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from typing import Any

import runtime as rt

SCHEMA = "football3-formal-missing-data-probe-compat-v1"


def install(gateway_module) -> dict[str, Any]:
    original = gateway_module.missing_data_probe

    def missing_data_probe(req: dict[str, Any], state_root: Path, out: Path, repo_root: Path,
                           understat_db: Path, confirmation_dir: Path) -> dict[str, Any]:
        bundle = state_root / "bundle"
        before = rt.validate_bundle(bundle)
        before_sha = before["manifest"]["state_bundle_sha256"]
        try:
            return original(req, state_root, out, repo_root, understat_db, confirmation_dir)
        except rt.RuntimeGateError as exc:
            reason = str(exc)
            accepted = (
                "FORMAL_INPUT_DATA_INCOMPLETE" in reason
                or "live source-contract resolution requires a prevalidated state sealed exactly at target cutoff" in reason
            )
            if not accepted:
                raise
            after = rt.validate_bundle(bundle)
            after_sha = after["manifest"]["state_bundle_sha256"]
            fixture, _kickoff, cutoff = gateway_module.make_future_fixture(req)
            result = {
                "status": "PASS_EXPECTED_FAIL_CLOSED",
                "probe": "MISSING_DATA_FAIL_CLOSED",
                "fixture": fixture,
                "cutoff": cutoff.isoformat(),
                "reason": reason,
                "state_unchanged": before_sha == after_sha,
                "state_bundle_sha": after_sha,
                "prediction_sha": None,
                "receipt_sha": None,
            }
            gateway_module.write_json(out / "formal_gap.json", result)
            if not result["state_unchanged"]:
                raise rt.RuntimeGateError("missing-data fail-closed probe mutated durable state")
            return result

    gateway_module.missing_data_probe = missing_data_probe
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "scope": "missing_data_probe_only",
        "expected_fail_closed_normalized": True,
        "state_mutation_forbidden": True,
        "prediction_emission_forbidden": True,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
