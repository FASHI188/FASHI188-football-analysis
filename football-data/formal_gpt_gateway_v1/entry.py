#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

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
            "live_source_failure_probe_adapter.json": LIVE_SOURCE_FAILURE_PROBE,
            "live_gateway_adapter.json": LIVE_GATEWAY,
            "formal_future_fixture_identity_bridge_adapter.json": FORMAL_FUTURE_FIXTURE_IDENTITY_BRIDGE,
            "formal_missing_data_probe_compat_adapter.json": FORMAL_MISSING_DATA_PROBE_COMPAT,
            "formal_state_integrity_guard_adapter.json": FORMAL_STATE_INTEGRITY_GUARD,
            "formal_cache_reuse_binding_adapter.json": FORMAL_CACHE_REUSE_BINDING,
            "formal_state_integrity_xg_history_count_fix_adapter.json": FORMAL_STATE_INTEGRITY_XG_HISTORY_COUNT_FIX,
            "formal_state_integrity_coverage_patch_adapter.json": FORMAL_STATE_INTEGRITY_COVERAGE_PATCH,
        }
        (out / "source_contract_audit.json").write_bytes(gateway.canon(audit))
        for name, obj in adapters.items():
            (out / name).write_bytes(gateway.canon(obj))
        p = out / "summary.json"
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
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
            d["live_source_failure_probe_adapter"] = LIVE_SOURCE_FAILURE_PROBE
            d["live_gateway_adapter"] = LIVE_GATEWAY
            d["formal_future_fixture_identity_bridge_adapter"] = FORMAL_FUTURE_FIXTURE_IDENTITY_BRIDGE
            d["formal_missing_data_probe_compat_adapter"] = FORMAL_MISSING_DATA_PROBE_COMPAT
            d["formal_state_integrity_guard_adapter"] = FORMAL_STATE_INTEGRITY_GUARD
            d["formal_cache_reuse_binding_adapter"] = FORMAL_CACHE_REUSE_BINDING
            d["formal_state_integrity_xg_history_count_fix_adapter"] = FORMAL_STATE_INTEGRITY_XG_HISTORY_COUNT_FIX
            d["formal_state_integrity_coverage_patch_adapter"] = FORMAL_STATE_INTEGRITY_COVERAGE_PATCH
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
