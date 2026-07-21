#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "market_surface_readiness_all17_v535_status.json"

SEASONS = {
    "ENG_PremierLeague": "2025/26",
    "GER_Bundesliga": "2025/26",
    "ITA_SerieA": "2025/26",
    "FRA_Ligue1": "2025/26",
    "ESP_LaLiga": "2025/26",
    "POR_PrimeiraLiga": "2025/26",
    "NED_Eredivisie": "2025/26",
    "SUI_SuperLeague": "2025/26",
    "SCO_Premiership": "2025/26",
    "SWE_Allsvenskan": "2025",
    "NOR_Eliteserien": "2025",
    "JPN_J1": "2025",
    "KOR_KLeague1": "2025",
    "BRA_SerieA": "2025",
    "ARG_Primera": "2025",
    "USA_MLS": "2025",
    "UEFA_ChampionsLeague": "2025/26",
}


def _number(value: Any, *, odds: bool = False) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(n):
        return None
    if odds and n <= 1.0:
        return None
    return n


def _season(row: dict[str, str]) -> str:
    return str(row.get("season") or row.get("Season") or "").strip()


def _scan(cid: str, season: str) -> dict[str, Any]:
    directory = ROOT / "processed" / cid
    files = sorted(directory.glob("*.csv")) if directory.exists() else []
    total_rows = 0
    one_rows = 0
    ah_rows = 0
    ou_rows = 0
    all_rows = 0
    source_paths = set()
    header_union = set()

    for path in files:
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                header_union.update(reader.fieldnames or [])
                for row in reader:
                    if _season(row) != season:
                        continue
                    if not str(row.get("HomeTeam") or "").strip() or not str(row.get("AwayTeam") or "").strip():
                        continue
                    total_rows += 1
                    source_paths.add(str(path.relative_to(ROOT)))
                    one = all(_number(row.get(key), odds=True) is not None for key in ("AvgCH", "AvgCD", "AvgCA"))
                    ah = (
                        _number(row.get("AHCh")) is not None
                        and _number(row.get("AvgCAHH"), odds=True) is not None
                        and _number(row.get("AvgCAHA"), odds=True) is not None
                    )
                    ou = (
                        _number(row.get("AvgC>2.5"), odds=True) is not None
                        and _number(row.get("AvgC<2.5"), odds=True) is not None
                    )
                    one_rows += int(one)
                    ah_rows += int(ah)
                    ou_rows += int(ou)
                    all_rows += int(one and ah and ou)
        except Exception:
            continue

    def rate(n: int) -> float:
        return n / total_rows if total_rows else 0.0

    return {
        "competition_id": cid,
        "season": season,
        "target_rows": total_rows,
        "source_paths": sorted(source_paths),
        "complete_closing_1x2_rows": one_rows,
        "complete_closing_1x2_rate": rate(one_rows),
        "complete_closing_ah_rows": ah_rows,
        "complete_closing_ah_rate": rate(ah_rows),
        "complete_closing_ou25_rows": ou_rows,
        "complete_closing_ou25_rate": rate(ou_rows),
        "complete_all_three_rows": all_rows,
        "complete_all_three_rate": rate(all_rows),
        "has_expected_1x2_columns": all(key in header_union for key in ("AvgCH", "AvgCD", "AvgCA")),
        "has_expected_ah_columns": all(key in header_union for key in ("AHCh", "AvgCAHH", "AvgCAHA")),
        "has_expected_ou25_columns": all(key in header_union for key in ("AvgC>2.5", "AvgC<2.5")),
        "status": (
            "THREE_SURFACE_READY_RETROSPECTIVE"
            if total_rows >= 100 and rate(all_rows) >= 0.80
            else "PARTIAL_SURFACE_READY"
            if total_rows >= 50 and max(rate(one_rows), rate(ah_rows), rate(ou_rows)) >= 0.50
            else "MARKET_SURFACE_UNAVAILABLE"
        ),
    }


def main() -> int:
    reports = {cid: _scan(cid, season) for cid, season in SEASONS.items()}
    three = [cid for cid, r in reports.items() if r["status"] == "THREE_SURFACE_READY_RETROSPECTIVE"]
    partial = [cid for cid, r in reports.items() if r["status"] == "PARTIAL_SURFACE_READY"]
    unavailable = [cid for cid, r in reports.items() if r["status"] == "MARKET_SURFACE_UNAVAILABLE"]
    payload = {
        "schema_version": "V5.3.5-market-surface-readiness-all17-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "reports": reports,
        "three_surface_ready_domains": three,
        "partial_surface_domains": partial,
        "market_surface_unavailable_domains": unavailable,
        "status": "PASS",
        "formal_weight_change": False,
        "probability_change": False,
        "formal_pit_market_eligible": False,
        "governance": "This scans retrospective latest-complete-season Football-Data-compatible fields only. Readiness means enough rows exist for architecture research, not that original quote timestamps exist or that a surface may be used formally."
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "three_surface_ready_domains": three,
        "partial_surface_domains": partial,
        "market_surface_unavailable_domains": unavailable,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
