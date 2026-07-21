#!/usr/bin/env python3
from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

import retrospective_market_matrix_projection_v530 as base
from platform_core import canonical_team_name, load_aliases

DOMAINS = ["GER_Bundesliga", "FRA_Ligue1", "POR_PrimeiraLiga"]
base.DOMAINS = DOMAINS
base.OU_COORDINATION_DOMAINS = set(DOMAINS)
base.OUT = ROOT / "manifests" / "retrospective_market_matrix_opening_v549_status.json"
base.SEED = 5492026


def _opening_lookup(cid: str):
    path = ROOT / "processed" / cid / "2025-26.csv"
    aliases = load_aliases()
    output = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("season") or row.get("Season") or "") != "2025/26":
                continue
            try:
                date = datetime.strptime(str(row["Date"]), "%d/%m/%Y").date().isoformat()
            except Exception:
                continue
            home = canonical_team_name(cid, str(row.get("HomeTeam") or ""), aliases)
            away = canonical_team_name(cid, str(row.get("AwayTeam") or ""), aliases)
            x12_raw = {key: base._odds(row.get(field)) for key, field in (
                ("home", "AvgH"), ("draw", "AvgD"), ("away", "AvgA")
            )}
            ou_raw = {key: base._odds(row.get(field)) for key, field in (
                ("over", "Avg>2.5"), ("under", "Avg<2.5")
            )}
            output[(date, home, away)] = {
                "one_x_two": base._devig(x12_raw) if all(v is not None for v in x12_raw.values()) else None,
                "ou25": base._devig(ou_raw) if all(v is not None for v in ou_raw.values()) else None,
                "source_path": str(path.relative_to(ROOT)),
                "market_timing": "opening_average_reference"
            }
    return output


base._market_lookup = _opening_lookup

if __name__ == "__main__":
    raise SystemExit(base.main())
