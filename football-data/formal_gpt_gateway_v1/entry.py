#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

# Install the explicit authoritative result-semantic adjudication before any
# gateway/source wrappers capture runtime functions. V2 is the governed successor
# to formal_result_adjudication_v1: it preserves that contract while adding the
# bounded identity bridge and delayed-settlement V1 release-order handling.
import formal_result_adjudication_v2
FORMAL_RESULT_ADJUDICATION = formal_result_adjudication_v2.install()

import formal_source_contract_v1

COMPAT = formal_source_contract_v1.install()
import source_contract_resolution_v1
SOURCE_RESOLUTION = source_contract_resolution_v1.install()
import live_delta_semantics_v2
LIVE_DELTA = live_delta_semantics_v2.install()
import live_extra_schema_patch_v1
LIVE_EXTRA_SCHEMA = live_extra_schema_patch_v1.install()
import live_xg_identity_patch_v1
LIVE_XG_IDENTITY = live_xg_identity_patch_v1.install()
import live_source_contract_resolution_v1
LIVE_SOURCE_CONTRACT = live_source_contract_resolution_v1.install()
import live_xg_quarantine_patch_v1
LIVE_XG_QUARANTINE = live_xg_quarantine_patch_v1.install()
import live_xg_quarantine_fast_reuse_v2
LIVE_XG_QUARANTINE_FAST_REUSE = live_xg_quarantine_fast_reuse_v2.install()
import live_fast_reuse_audit_v1
LIVE_FAST_AUDIT = live_fast_reuse_audit_v1.install()
import gateway

# An exact-cutoff verified empty delta is a real no-op. Install this before any
# gateway wrappers capture the runtime path so repeat sealed requests remain byte-stable.
import formal_runtime_exact_noop_v1
FORMAL_RUNTIME_EXACT_NOOP = formal_runtime_exact_noop_v1.install()

import live_source_failure_probe_v1
LIVE_SOURCE_FAILURE_PROBE = live_source_failure_probe_v1.install(gateway)
import live_gateway_patch_v1

LIVE_GATEWAY = live_gateway_patch_v1.install(gateway)

# Canonical future/absent fixture identity must be available inside the gateway
# before the integrity guard captures the production normal-request chain. This
# bridge is generic and identity-only; it does not install target replay logic.
import formal_future_fixture_identity_bridge_v1
FORMAL_FUTURE_FIXTURE_IDENTITY_BRIDGE = formal_future_fixture_identity_bridge_v1.install(gateway)

# Preserve the historical missing-data probe contract when stricter live source
# guards fail closed with a newer error class. This affects probe mode only.
import formal_missing_data_probe_compat_v1
FORMAL_MISSING_DATA_PROBE_COMPAT = formal_missing_data_probe_compat_v1.install(gateway)

# If a validated state is already sealed exactly at the requested cutoff, execute
# a source-silent replay rather than trying to re-observe a historical cutoff.
import formal_exact_cutoff_sealed_replay_v1
FORMAL_EXACT_CUTOFF_SEALED_REPLAY = formal_exact_cutoff_sealed_replay_v1.install(gateway)

# Install the isolated 2025/26 Ligue 1 historical xG completeness repair before
# the generic integrity guard. It only supplies two source-provenance rows to the
# already-frozen linked-history loader and does not alter model parameters/weights.
import formal_ligue1_2025_26_xg_repair_v1
FORMAL_LIGUE1_2025_26_XG_REPAIR = formal_ligue1_2025_26_xg_repair_v1.install()

# Install generic integrity controls after the existing production gateway stack.
# Target-specific Stuttgart/xG replay and diagnostic patches are intentionally not
# installed here; those remain confined to diagnostic workflows.
import formal_state_integrity_guard_v1
FORMAL_STATE_INTEGRITY_GUARD = formal_state_integrity_guard_v1.install(gateway)
import formal_cache_reuse_binding_v1
FORMAL_CACHE_REUSE_BINDING = formal_cache_reuse_binding_v1.install(gateway)
import formal_state_integrity_xg_history_count_fix_v1
FORMAL_STATE_INTEGRITY_XG_HISTORY_COUNT_FIX = formal_state_integrity_xg_history_count_fix_v1.install()
import formal_state_integrity_coverage_patch_v1
FORMAL_STATE_INTEGRITY_COVERAGE_PATCH = formal_state_integrity_coverage_patch_v1.install()

# Durable state governance is outermost: a verified cutoff-aware state selection
# must be bound before prediction, and transition/cache/fallback semantics are
# checked after the unchanged formal model call.
import formal_durable_state_governance_v1
FORMAL_DURABLE_STATE_GOVERNANCE = formal_durable_state_governance_v1.install(gateway)


def _direct_complete_fixture(history):
    lower = datetime(2023, 3, 1, tzinfo=timezone.utc)
    upper = datetime(2023, 4, 1, tzinfo=timezone.utc)
    rows = [
        r for r in history
        if r.competition_id == "ENG_PremierLeague"
        and r.season == "2022/23"
        and lower <= r.kickoff < upper
    ]
    if not rows:
        raise gateway.rt.RuntimeGateError("direct frozen safe-prefix fixture probe unavailable")
    rows.sort(key=lambda r: (r.kickoff, r.home_team_name, r.away_team_name, r.fixture_id))
    return rows[0]


gateway.first_fixture = _direct_complete_fixture


def main() -> int:
    code = gateway.main()
    import sys
    try:
        out_arg = sys.argv[sys.argv.index("--out") + 1]
        out = Path(out_arg)
        audit = formal_source_contract_v1.audit_snapshot()
        fast_audit = live_fast_reuse_audit_v1.snapshot()
        adapters = {
            "formal_result_adjudication_adapter.json": FORMAL_RESULT_ADJUDICATION,
            "source_contract_adapter.json": COMPAT,
            "source_contract_resolution.json": SOURCE_RESOLUTION,
            "live_delta_adapter.json": LIVE_DELTA,
            "live_extra_schema_adapter.json": LIVE_EXTRA_SCHEMA,
            "live_xg_identity_adapter.json": LIVE_XG_IDENTITY,
            "live_source_contract_adapter.json": LIVE_SOURCE_CONTRACT,
            "live_xg_quarantine_adapter.json": LIVE_XG_QUARANTINE,
            "live_xg_quarantine_fast_reuse_adapter.json": LIVE_XG_QUARANTINE_FAST_REUSE,
            "live_fast_reuse_adapter.json": LIVE_FAST_AUDIT,
            "live_fast_reuse_audit.json": fast_audit,
            "formal_runtime_exact_noop_adapter.json": FORMAL_RUNTIME_EXACT_NOOP,
            "live_source_failure_probe_adapter.json": LIVE_SOURCE_FAILURE_PROBE,
            "live_gateway_adapter.json": LIVE_GATEWAY,
            "formal_future_fixture_identity_bridge_adapter.json": FORMAL_FUTURE_FIXTURE_IDENTITY_BRIDGE,
            "formal_missing_data_probe_compat_adapter.json": FORMAL_MISSING_DATA_PROBE_COMPAT,
            "formal_exact_cutoff_sealed_replay_adapter.json": FORMAL_EXACT_CUTOFF_SEALED_REPLAY,
            "formal_ligue1_2025_26_xg_repair_adapter.json": FORMAL_LIGUE1_2025_26_XG_REPAIR,
            "formal_state_integrity_guard_adapter.json": FORMAL_STATE_INTEGRITY_GUARD,
            "formal_cache_reuse_binding_adapter.json": FORMAL_CACHE_REUSE_BINDING,
            "formal_state_integrity_xg_history_count_fix_adapter.json": FORMAL_STATE_INTEGRITY_XG_HISTORY_COUNT_FIX,
            "formal_state_integrity_coverage_patch_adapter.json": FORMAL_STATE_INTEGRITY_COVERAGE_PATCH,
            "formal_durable_state_governance_adapter.json": FORMAL_DURABLE_STATE_GOVERNANCE,
        }
        (out / "source_contract_audit.json").write_bytes(gateway.canon(audit))
        for name, obj in adapters.items():
            (out / name).write_bytes(gateway.canon(obj))
        p = out / "summary.json"
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            d["formal_result_adjudication_adapter"] = FORMAL_RESULT_ADJUDICATION
            d["source_contract_adapter"] = COMPAT
            d["source_contract_audit"] = audit
            d["source_contract_resolution"] = SOURCE_RESOLUTION
            d["live_delta_adapter"] = LIVE_DELTA
            d["live_extra_schema_adapter"] = LIVE_EXTRA_SCHEMA
            d["live_xg_identity_adapter"] = LIVE_XG_IDENTITY
            d["live_source_contract_adapter"] = LIVE_SOURCE_CONTRACT
            d["live_xg_quarantine_adapter"] = LIVE_XG_QUARANTINE
            d["live_xg_quarantine_fast_reuse_adapter"] = LIVE_XG_QUARANTINE_FAST_REUSE
            d["live_fast_reuse_adapter"] = LIVE_FAST_AUDIT
            d["live_fast_reuse_audit"] = fast_audit
            d["formal_runtime_exact_noop_adapter"] = FORMAL_RUNTIME_EXACT_NOOP
            d["live_source_failure_probe_adapter"] = LIVE_SOURCE_FAILURE_PROBE
            d["live_gateway_adapter"] = LIVE_GATEWAY
            d["formal_future_fixture_identity_bridge_adapter"] = FORMAL_FUTURE_FIXTURE_IDENTITY_BRIDGE
            d["formal_missing_data_probe_compat_adapter"] = FORMAL_MISSING_DATA_PROBE_COMPAT
            d["formal_exact_cutoff_sealed_replay_adapter"] = FORMAL_EXACT_CUTOFF_SEALED_REPLAY
            d["formal_ligue1_2025_26_xg_repair_adapter"] = FORMAL_LIGUE1_2025_26_XG_REPAIR
            d["formal_state_integrity_guard_adapter"] = FORMAL_STATE_INTEGRITY_GUARD
            d["formal_cache_reuse_binding_adapter"] = FORMAL_CACHE_REUSE_BINDING
            d["formal_state_integrity_xg_history_count_fix_adapter"] = FORMAL_STATE_INTEGRITY_XG_HISTORY_COUNT_FIX
            d["formal_state_integrity_coverage_patch_adapter"] = FORMAL_STATE_INTEGRITY_COVERAGE_PATCH
            d["formal_durable_state_governance_adapter"] = FORMAL_DURABLE_STATE_GOVERNANCE
            d["bootstrap_fixture_selection"] = (
                "direct first frozen ENG_PremierLeague 2022/23 fixture in 2023-03, "
                "strictly before first quarantined source-contract boundary; "
                "identity/time-only selection; no 300-match scoring/sample surrogate"
            )
            p.write_bytes(gateway.canon(d))
    except Exception:
        pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())
