#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import formal_source_contract_v1 as strict
import runtime as rt

SCHEMA = "football3-formal-source-contract-resolution-v1"
BASE_LIMIT = rt._parse_dt(rt.BASE_HISTORY_CUTOFF, "base history cutoff")


def _resolved_audit(audit: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA,
        "status": "FULL_V1_PLUS_EXPLICIT_XG_QUARANTINE",
        "expected_xg_source_rows": int(audit.get("expected_join_n", rt.EXPECTED_XG_JOIN_N)),
        "accepted_xg_rows": int(audit.get("safe_join_n", 0)),
        "quarantined_time_semantic_rows": int(audit.get("time_semantic_gap_n", 0)),
        "quarantined_result_conflict_rows": int(audit.get("result_conflict_n", 0)),
        "time_semantic_gaps": audit.get("time_semantic_gaps", []),
        "result_conflicts": audit.get("result_conflicts", []),
        "policy": {
            "v1": "all frozen V1 rows retain their original official result/date semantics",
            "xg": "only source rows with exact auditable formal fixture identity and compatible result semantics enter XG residual state",
            "time_conflict": "XG row quarantined; neither V1 kickoff nor XG source kickoff/release is shifted",
            "result_conflict": "XG row quarantined; official V1 result and on-field XG-source result are never substituted for one another",
            "downstream_after_quarantine": "later uncontested rows remain usable; quarantine is row-local, not a global chronology stop",
            "model_parameters_or_weights_changed": False,
        },
        "original_strict_audit_sha256": rt._sha_bytes(rt._canon_bytes(audit)),
    }


def build_resolved_base(repo_root: Path, understat_db: Path, confirmation_dir: Path, bundle: Path) -> dict[str, Any]:
    history, v1_source = rt.load_frozen_v1_history(repo_root)
    labels, xg_source = strict.strict_load_xg_labels(history, understat_db, confirmation_dir)
    audit = strict.audit_snapshot()
    resolved = _resolved_audit(audit)
    expected_safe = rt.EXPECTED_XG_JOIN_N - resolved["quarantined_time_semantic_rows"] - resolved["quarantined_result_conflict_rows"]
    if resolved["accepted_xg_rows"] != expected_safe or len(labels) != expected_safe:
        raise rt.RuntimeGateError("resolved XG quarantine cardinality mismatch")
    state, replay = rt.replay_history_state(history, labels, BASE_LIMIT)
    if len(state.base.seen_fixtures) != rt.EXPECTED_V1_N:
        raise rt.RuntimeGateError("resolved base V1 cardinality mismatch")
    if len(state.seen) != expected_safe or state.pending:
        raise rt.RuntimeGateError("resolved base XG cardinality/pending mismatch")
    source = {
        "v1": v1_source,
        "xg": xg_source,
        "replay": replay,
        "source_scope": "FROZEN_HISTORICAL_WITH_EXPLICIT_XG_QUARANTINE",
        "source_contract_resolution": resolved,
    }
    identity = rt._production_identity()
    identity = {
        **identity,
        "xg_source_expected_n": rt.EXPECTED_XG_JOIN_N,
        "xg_accepted_n": expected_safe,
        "xg_quarantined_n": rt.EXPECTED_XG_JOIN_N - expected_safe,
        "xg_quarantine_policy": "row-local explicit quarantine; no time/result substitution",
    }
    manifest = rt.seal_bundle(state, bundle, source, identity, BASE_LIMIT.isoformat(), "FULL_REBUILD_PATH_EXPLICIT_XG_QUARANTINE")
    checked = rt.validate_bundle(bundle)
    return {"manifest": manifest, "loaded": checked, "audit": resolved, "cutoff": BASE_LIMIT}


def _is_sealed_live_input(inp: dict[str, Any], cutoff: datetime) -> bool:
    cov = inp.get("delta_coverage")
    delta = inp.get("model_delta")
    if type(cov) is not dict or delta != []:
        return False
    if cov.get("schema_version") != rt.DELTA_SCHEMA or cov.get("status") != "COMPLETE" or cov.get("verification") != "VERIFIED_COMPLETE":
        return False
    if cov.get("v1_status") != "COMPLETE" or cov.get("xg_status") != "COMPLETE":
        return False
    try:
        lower = rt._parse_dt(str(cov.get("from") or ""), "delta from")
        upper = rt._parse_dt(str(cov.get("to") or ""), "delta to")
    except rt.RuntimeGateError:
        return False
    return lower == cutoff and upper == cutoff and cov.get("records_sha256") == rt._sha_bytes(rt._canon_bytes([]))


def install() -> dict[str, Any]:
    original_resolve = strict._ORIGINAL_RESOLVE
    strict_resolve = strict.strict_resolve_state_for_cutoff

    def resolved_runtime_state(bundle_dir, inp, fixture, cutoff, repo_root=None, understat_db=None, confirmation_dir=None,
                               engineering_history=None, engineering_xg_labels=None, engineering_source=None, engineering_identity=None):
        cutoff = cutoff.astimezone(timezone.utc)
        if engineering_history is not None or cutoff <= BASE_LIMIT:
            return strict_resolve(bundle_dir, inp, fixture, cutoff, repo_root, understat_db, confirmation_dir,
                                  engineering_history, engineering_xg_labels, engineering_source, engineering_identity)
        if not _is_sealed_live_input(inp, cutoff):
            raise rt.RuntimeGateError("live source-contract resolution requires a prevalidated state sealed exactly at target cutoff")
        loaded = rt.validate_bundle(bundle_dir)
        bundle_cutoff = rt._parse_dt(str(loaded["meta"]["historical_cutoff"]), "bundle cutoff")
        if bundle_cutoff != cutoff:
            raise rt.RuntimeGateError("live source-contract resolved bundle cutoff mismatch")
        # Bypass only the old global first-quarantine guard. All frozen runtime bundle/input/model gates still run.
        return original_resolve(bundle_dir, inp, fixture, cutoff, repo_root, understat_db, confirmation_dir,
                                engineering_history, engineering_xg_labels, engineering_source, engineering_identity)

    rt.resolve_state_for_cutoff = resolved_runtime_state
    return {
        "schema_version": SCHEMA,
        "installed": True,
        "strict_adapter_retained_for_historical_cutoffs": True,
        "live_bypass_scope": "only a prevalidated bundle sealed exactly at target cutoff with empty VERIFIED_COMPLETE delta",
        "result_substitution": False,
        "time_mutation": False,
        "model_parameters_or_weights_changed": False,
    }
