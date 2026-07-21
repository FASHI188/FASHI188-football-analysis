#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DETAIL = ROOT / "manifests" / "gdelt_recent_context_coverage_v517"
OUT = ROOT / "manifests" / "gdelt_recent_context_coverage_v517_status.json"
DOMAINS = ["ENG_PremierLeague", "ESP_LaLiga", "GER_Bundesliga", "ITA_SerieA", "FRA_Ligue1"]


def main() -> int:
    reports = {}
    missing = []
    for cid in DOMAINS:
        path = DETAIL / f"{cid}.json"
        if not path.exists():
            missing.append(cid)
            continue
        reports[cid] = json.loads(path.read_text(encoding="utf-8"))
    payload = {
        "schema_version": "V5.1.7-gdelt-recent-context-coverage-aggregate-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "season": "2025/26",
        "completed_domains": list(reports),
        "missing_domains": missing,
        "reports": reports,
        "status": "PASS" if len(reports) == len(DOMAINS) and not missing else "PARTIAL",
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "governance": "GDELT seendate is independent observation time, not publisher publication time. This receipt measures discovery coverage only; article metadata cannot directly mutate probabilities."
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "coverage": {cid: {
            "either": r.get("either_team_fixture_coverage_rate"),
            "both": r.get("both_team_fixture_coverage_rate"),
            "articles": r.get("article_record_count"),
            "failures": r.get("query_failure_count")
        } for cid, r in reports.items()}
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
