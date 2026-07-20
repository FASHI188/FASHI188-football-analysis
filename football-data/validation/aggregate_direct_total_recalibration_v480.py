#!/usr/bin/env python3
"""Aggregate second-stage V4.8 direct-total recalibration evidence."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REVIEW_ROOT = ROOT / "manifests" / "direct_total_distribution_v480_recalibration"
PROMOTION_ROOT = ROOT / "manifests" / "promotions"
OUT = ROOT / "manifests" / "direct_total_distribution_v480_recalibration_status.json"
COMPETITIONS = (
    "ENG_PremierLeague", "GER_Bundesliga", "ITA_SerieA", "FRA_Ligue1",
    "ESP_LaLiga", "POR_PrimeiraLiga", "NED_Eredivisie", "SUI_SuperLeague",
    "SCO_Premiership", "SWE_Allsvenskan", "NOR_Eliteserien", "JPN_J1",
    "KOR_KLeague1", "BRA_SerieA", "ARG_Primera", "USA_MLS", "UEFA_ChampionsLeague",
)


def _has_promoted_d(cid: str) -> bool:
    path = PROMOTION_ROOT / f"{cid}_d_conditional_v470.json"
    if not path.exists():
        return False
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("promotion_status") == "PROMOTED"
    except Exception:
        return True


def main() -> int:
    reports: dict[str, Any] = {}
    failures: dict[str, str] = {}
    missing: list[str] = []
    raw_ready: list[str] = []
    draft_candidates: list[str] = []
    interaction_replay_required: list[str] = []
    for cid in COMPETITIONS:
        path = REVIEW_ROOT / f"{cid}.json"
        if not path.exists():
            missing.append(cid)
            reports[cid] = {"competition_id": cid, "status": "MISSING", "formal_weight": 0}
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        reports[cid] = data
        status = str(data.get("status") or "")
        if status == "FAILED":
            failures[cid] = str(data.get("reason") or "unknown failure")
        if status == "V480_CURRENT_UPGRADE_EVIDENCE_READY":
            raw_ready.append(cid)
            if _has_promoted_d(cid):
                interaction_replay_required.append(cid)
            else:
                draft_candidates.append(cid)
    built = sum(1 for item in reports.values() if item.get("status") not in {"MISSING", "FAILED"})
    payload = {
        "schema_version": "V4.8.0-direct-total-recalibration-aggregate-r1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if built == len(COMPETITIONS) and not failures else "PARTIAL",
        "competition_count_requested": len(COMPETITIONS),
        "competition_count_built": built,
        "competition_count_failed": len(failures),
        "competition_count_missing": len(missing),
        "raw_recalibration_passes": raw_ready,
        "current_upgrade_draft_candidates": draft_candidates,
        "promoted_module_interaction_replay_required": interaction_replay_required,
        "formal_weight_change": False,
        "automatic_promotion": False,
        "formal_rule_version_unchanged": "V4.7.0",
        "policy": (
            "No result changes V4.7. A domain with an already-promoted D|T module must pass an additional nested interaction replay "
            "before it can even be considered for a future V4.8 CURRENT draft. Other passing domains remain draft candidates only."
        ),
        "reports": reports,
        "failures": failures,
        "missing": missing,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "draft_candidates": draft_candidates,
        "interaction_replay_required": interaction_replay_required,
        "failed": failures,
        "missing": missing,
    }, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
