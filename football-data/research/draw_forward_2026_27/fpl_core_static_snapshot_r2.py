#!/usr/bin/env python3
from __future__ import annotations

import fpl_core_static_snapshot_r1 as engine

# GitHub raw URLs require percent-encoding for the historical directory name.
# This changes only transport syntax; the source commit, file identity and hashes
# remain governed by the R2 collector and its source ledger.
engine.REQUIRED["prior_playerstats"] = "data/2025-2026/By%20Gameweek/GW38/playerstats.csv"

if __name__ == "__main__":
    raise SystemExit(engine.main())
