#!/usr/bin/env python3
"""Aggregate per-competition V4.8 direct categorical total-goals research receipts."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STATUS_ROOT = ROOT / "manifests" / "direct_total_distribution_v480"
OUT = ROOT / "manifests" / "direct_total_distribution_v480_status.json"
COMPETITIONS = (
    "ENG_PremierLeague", "GER_Bundesliga", "ITA_SerieA", "FRA_Ligue1",
    "ESP_LaLiga", "POR_PrimeiraLiga", "NED_Eredivisie", "SUI_SuperLeague",
    "SCO_Premiership", "SWE_Allsvenskan", "NOR_Eliteserien", "JPN_J1",
    "KOR_KLeague1", "BRA_SerieA", "ARG_Primera", "USA_MLS", "UEFA_ChampionsLeague",
)


def main() -> int:
    reports: dict[str, Any] = {}
    failures: dict[str, str] = {}
    missing: list[str] = []
    candidates: list[str] = []
    for cid in COMPETITIONS:
        path = STATUS_ROOT / f"{cid}.json"
        if not path.exists():
            missing.append(cid)
            reports[cid] = {"competition_id": cid, "status": "MISSING", "formal_weight": 0}
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        reports[cid] = data
        status = str(data.get("status") or "")
        if status == "FAILED":
            failures[cid] = str(data.get("reason") or "unknown failure")
        if status == "RECALIBRATION_REVIEW_CANDIDATE":
            candidates.append(cid)
    built = sum(1 for item in reports.values() if item.get("status") not in {"MISSING", "FAILED"})
    payload = {
        "schema_version": "V4.8.0-direct-categorical-total-research-aggregate-r1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if built == len(COMPETITIONS) and not failures else "PARTIAL",
        "competition_count_requested": len(COMPETITIONS),
        "competition_count_built": built,
        "competition_count_failed": len(failures),
        "competition_count_missing": len(missing),
        "recalibration_review_candidates": candidates,
        "candidate_count": len(candidates),
        "formal_weight_change": False,
        "automatic_promotion": False,
        "formal_status": "RESEARCH_ONLY_NOT_REGISTERED_IN_V4_7_CURRENT",
        "selection_policy": "strict competition-local nested chronological OOS against CURRENT Champion; mode-3 frequency is diagnostic only",
        "reports": reports,
        "failures": failures,
        "missing": missing,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "built": built,
        "candidates": candidates,
        "failed": failures,
        "missing": missing,
    }, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
