#!/usr/bin/env python3
"""V6.18.6 r2 execution wrapper.

The original V6.18.6 audit was created with JSON-style lowercase `true`/`false`
identifiers in the final governance payload. Python accepts those tokens as names at
compile time but raises NameError when main() builds the receipt.

No V6.18.6 scored/data-quality receipt existed when this repair was authored.
This wrapper only binds those names to the intended Python booleans, runs the unchanged
audit, and rewrites the schema marker so downstream readers can distinguish the repaired
execution. Fixture matching, date tolerance, fuzzy-diagnostic-only policy, coverage
thresholds, data sources, and all governance rules are unchanged.
"""
from __future__ import annotations

import json

import v6_understat_fixture_alignment_audit_v6186 as audit


def main() -> int:
    # Runtime reference repair only; no audit-design change.
    audit.true = True
    audit.false = False
    code = audit.main()
    path = audit.OUT
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["schema_version"] = "V6.18.6-understat-fixture-alignment-audit-r2"
        payload["pre_score_repair"] = {
            "python_boolean_reference_repaired": True,
            "audit_design_changed": False,
            "fixture_identity_changed": False,
            "date_tolerance_changed": False,
            "fuzzy_training_rows_allowed": False,
            "pre_repair_receipt_existed": False,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
