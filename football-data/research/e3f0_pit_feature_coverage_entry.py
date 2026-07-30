#!/usr/bin/env python3
"""E3f-0 audited entrypoint.

Remove the ambiguous short token `xa` before schema scanning so bookmaker fields
such as MaxA cannot be misclassified as expected-assist data.
"""
from __future__ import annotations

import e3f0_pit_feature_coverage as audit


audit.FAMILIES["xg_chance_quality"] = tuple(
    token for token in audit.FAMILIES["xg_chance_quality"] if token != "xa"
)


if __name__ == "__main__":
    raise SystemExit(audit.main())
