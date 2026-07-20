#!/usr/bin/env python3
"""Aggregate competition-local V4.7 dynamic-strength research screens."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_ROOT = ROOT / "manifests" / "dynamic_strength_oof_screen_v470"
STATUS_PATH = ROOT / "manifests" / "dynamic_strength_oof_screen_v470_status.json"
COMPETITIONS = [
    "ENG_PremierLeague", "GER_Bundesliga", "ITA_SerieA", "FRA_Ligue1", "ESP_LaLiga",
    "POR_PrimeiraLiga", "NED_Eredivisie", "SWE_Allsvenskan", "NOR_Eliteserien", "BRA_SerieA",
]


def main() -> int:
    reports = {}; missing = []; failures = []; candidates = []
    for competition_id in COMPETITIONS:
        path = REPORT_ROOT / f"{competition_id}.json"
        if not path.exists():
            missing.append(competition_id); reports[competition_id] = {"competition_id": competition_id, "status": "MISSING", "formal_weight": 0}; continue
        report = json.loads(path.read_text(encoding="utf-8")); reports[competition_id] = report
        if report.get("status") == "FAILED": failures.append(competition_id)
        if report.get("status") == "DYNAMIC_STRENGTH_REVIEW_CANDIDATE": candidates.append(competition_id)
    status = {
        "schema_version": "V4.7.0-dynamic-strength-oof-screen-aggregate-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if not missing and not failures else "PARTIAL",
        "competition_count_requested": len(COMPETITIONS),
        "competition_count_built": len(COMPETITIONS) - len(missing) - len(failures),
        "competition_count_failed": len(failures),
        "competition_count_missing": len(missing),
        "second_stage_review_candidates": candidates,
        "candidate_count": len(candidates),
        "formal_weight_change": False,
        "automatic_promotion": False,
        "probability_change": False,
        "formal_rule_version_unchanged": "V4.7.0",
        "reports": reports,
        "failures": failures,
        "missing": missing,
        "policy": "Stage-1 rolling OOF research only. A candidate must pass an independent second-stage chronological review before any CURRENT-compliant promotion can be considered. No automatic activation."
    }
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": status["status"], "candidates": candidates, "failed": failures, "missing": missing}, ensure_ascii=False, indent=2))
    return 0 if status["status"] == "PASS" else 1


if __name__ == "__main__": raise SystemExit(main())
