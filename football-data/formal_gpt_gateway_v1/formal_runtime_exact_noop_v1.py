#!/usr/bin/env python3
from __future__ import annotations

from typing import Any

import runtime as rt

SCHEMA = "football3-formal-runtime-exact-noop-v1"
_INSTALLED = False
_ORIGINAL = None


def install() -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL
    if _INSTALLED:
        return {
            "schema_version": SCHEMA,
            "installed": True,
            "idempotent": True,
            "exact_cutoff_empty_delta": "SOURCE_SILENT_NO_OP_DELTA",
            "full_rebuild_used": False,
            "model_parameters_or_weights_changed": False,
        }

    original = rt.resolve_state_for_cutoff
    _ORIGINAL = original

    def resolve_state_for_cutoff(bundle_dir, inp, fixture, cutoff,
                                 repo_root=None, understat_db=None, confirmation_dir=None,
                                 engineering_history=None, engineering_xg_labels=None,
                                 engineering_source=None, engineering_identity=None):
        try:
            loaded = rt.validate_bundle(bundle_dir)
            bundle_cutoff = rt._parse_dt(loaded["meta"]["historical_cutoff"], "bundle cutoff")
            if bundle_cutoff == cutoff:
                checked = rt.validate_runtime_input(inp, fixture, bundle_cutoff, cutoff)
                if checked["fast_eligible"] and checked["delta"] == []:
                    return {
                        "state": rt.deserialize_state(
                            rt.serialize_v1_state(loaded["state"].base),
                            rt.serialize_xg_state(loaded["state"]),
                        ),
                        "path": "FAST_PATH",
                        "fast_failure": None,
                        "delta_result": {
                            "applied_v1": 0,
                            "applied_xg": 0,
                            "as_of": cutoff.isoformat(),
                            "transition_status": "NO_OP_DELTA",
                            "source_refetch_used": False,
                            "state_resealed": False,
                        },
                        "manifest": loaded["manifest"],
                        "input_check": checked,
                    }
        except rt.RuntimeGateError:
            # Non-exact, invalid or damaged inputs retain the existing governed path.
            # It will either take verified FAST, rebuild from trusted FULL sources, or fail closed.
            pass
        return original(
            bundle_dir, inp, fixture, cutoff,
            repo_root, understat_db, confirmation_dir,
            engineering_history, engineering_xg_labels,
            engineering_source, engineering_identity,
        )

    rt.resolve_state_for_cutoff = resolve_state_for_cutoff
    _INSTALLED = True
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "idempotent": True,
        "exact_cutoff_empty_delta": "SOURCE_SILENT_NO_OP_DELTA",
        "full_rebuild_used": False,
        "state_resealed": False,
        "target_specific_logic": False,
        "model_parameters_or_weights_changed": False,
        "formal_current_or_production_pointer_changed": False,
    }
