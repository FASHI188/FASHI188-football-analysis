#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import source_compat_v3

COMPAT = source_compat_v3.install()
import gateway


def _direct_complete_fixture(history):
    lower = datetime(2025, 4, 1, tzinfo=timezone.utc)
    upper = datetime(2025, 5, 1, tzinfo=timezone.utc)
    rows = [r for r in history if r.competition_id == "ENG_PremierLeague" and r.season == "2024/25" and lower <= r.kickoff < upper]
    if not rows:
        raise gateway.rt.RuntimeGateError("direct frozen fixture probe unavailable")
    rows.sort(key=lambda r: (r.kickoff, r.home_team_name, r.away_team_name, r.fixture_id))
    return rows[0]


gateway.first_fixture = _direct_complete_fixture


def main() -> int:
    code = gateway.main()
    import sys
    try:
        out_arg = sys.argv[sys.argv.index("--out") + 1]
        p = Path(out_arg) / "summary.json"
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            d["source_compat"] = COMPAT
            d["bootstrap_fixture_selection"] = "direct first frozen ENG_PremierLeague 2024/25 fixture in 2025-04; no 300-match scoring/sample surrogate"
            p.write_bytes(gateway.canon(d))
            (Path(out_arg) / "source_compat.json").write_bytes(gateway.canon(COMPAT))
    except Exception:
        pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())
