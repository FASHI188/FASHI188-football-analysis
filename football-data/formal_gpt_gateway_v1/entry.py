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
import gateway
import live_gateway_patch_v1

LIVE_GATEWAY = live_gateway_patch_v1.install(gateway)


def _direct_complete_fixture(history):
    # Deterministic single historical FULL probe strictly before the first quarantined
    # source-contract boundary. Identity/time only; no result/xG selection and no
    # 300-match scoring/sample surrogate.
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
        (out / "source_contract_audit.json").write_bytes(gateway.canon(audit))
        (out / "source_contract_adapter.json").write_bytes(gateway.canon(COMPAT))
        (out / "source_contract_resolution.json").write_bytes(gateway.canon(SOURCE_RESOLUTION))
        (out / "live_delta_adapter.json").write_bytes(gateway.canon(LIVE_DELTA))
        (out / "live_extra_schema_adapter.json").write_bytes(gateway.canon(LIVE_EXTRA_SCHEMA))
        (out / "live_gateway_adapter.json").write_bytes(gateway.canon(LIVE_GATEWAY))
        p = out / "summary.json"
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            d["source_contract_adapter"] = COMPAT
            d["source_contract_audit"] = audit
            d["source_contract_resolution"] = SOURCE_RESOLUTION
            d["live_delta_adapter"] = LIVE_DELTA
            d["live_extra_schema_adapter"] = LIVE_EXTRA_SCHEMA
            d["live_gateway_adapter"] = LIVE_GATEWAY
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
