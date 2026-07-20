#!/usr/bin/env python3
"""Aggregate isolated remaining-domain V4.7 training receipts without retraining."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STATUS_ROOT = ROOT / "manifests" / "remaining_competition_challengers_v470"
OUT = ROOT / "manifests" / "remaining_competition_challenger_training_v470_status.json"
REMAINING_COMPETITIONS = (
    "ENG_PremierLeague", "GER_Bundesliga", "ITA_SerieA", "FRA_Ligue1",
    "ESP_LaLiga", "POR_PrimeiraLiga", "NED_Eredivisie", "SUI_SuperLeague",
    "SCO_Premiership", "JPN_J1", "BRA_SerieA", "ARG_Primera", "UEFA_ChampionsLeague",
)


def main() -> int:
    reports: dict[str, Any] = {}
    failures: dict[str, str] = {}
    missing: list[str] = []
    for cid in REMAINING_COMPETITIONS:
        path = STATUS_ROOT / f"{cid}.json"
        if not path.exists():
            missing.append(cid)
            reports[cid] = {"competition_id": cid, "status": "MISSING", "formal_weight": 0}
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        reports[cid] = data
        if data.get("status") != "PASS":
            failures[cid] = str(data.get("reason") or data.get("status") or "unknown failure")
    built = sum(1 for item in reports.values() if item.get("status") == "PASS")
    payload = {
        "schema_version": "V4.7.0-remaining-competition-challenger-training-r2",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if built == len(REMAINING_COMPETITIONS) else "PARTIAL",
        "competition_count_requested": len(REMAINING_COMPETITIONS),
        "competition_count_built": built,
        "competition_count_failed": len(failures),
        "competition_count_missing": len(missing),
        "formal_weight_change": False,
        "automatic_promotion": False,
        "training_policy": "independent per-competition nested chronological OOF workers; no shared rows, parameters, calibrators or weights",
        "formal_screening_target": "conditional_allocation_v470 only",
        "total_tail_policy": "research_only_formal_weight_0_until_future_complete_CURRENT_upgrade",
        "reports": reports,
        "failures": failures,
        "missing": missing,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "built": built,
        "failed": failures,
        "missing": missing,
        "conditional_summary": {
            cid: (((rep.get("conditional_allocation") or {}).get("status")) if rep.get("status") == "PASS" else rep.get("status"))
            for cid, rep in reports.items()
        },
    }, ensure_ascii=False, indent=2))
    return 0 if not missing and not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
