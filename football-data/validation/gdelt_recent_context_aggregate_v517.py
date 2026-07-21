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

    domain_gate = {}
    for cid, report in reports.items():
        kickoff = report.get("kickoff_time_audit") or {}
        domain_gate[cid] = {
            "report_status": report.get("status"),
            "query_failure_count": int(report.get("query_failure_count") or 0),
            "either_team_fixture_coverage_rate": report.get("either_team_fixture_coverage_rate"),
            "both_team_fixture_coverage_rate": report.get("both_team_fixture_coverage_rate"),
            "article_record_count": report.get("article_record_count"),
            "exact_kickoff_count": int(kickoff.get("exact_kickoff_count") or 0),
            "fallback_midnight_count": int(kickoff.get("fallback_midnight_count") or 0),
            "kickoff_parse_failure_count": int(kickoff.get("parse_failure_count") or 0),
            "kickoff_timezone": kickoff.get("time_zone_interpretation"),
            "domain_pass": (
                report.get("status") == "PASS"
                and int(kickoff.get("fallback_midnight_count") or 0) == 0
                and int(kickoff.get("parse_failure_count") or 0) == 0
                and kickoff.get("time_zone_interpretation") == "Europe/London"
            ),
        }

    passed_domains = [cid for cid, gate in domain_gate.items() if gate["domain_pass"]]
    payload = {
        "schema_version": "V5.1.7-gdelt-recent-context-coverage-aggregate-r2",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "season": "2025/26",
        "completed_domains": list(reports),
        "missing_domains": missing,
        "passed_domains": passed_domains,
        "domain_gate_summary": domain_gate,
        "reports": reports,
        "status": "PASS" if len(passed_domains) == len(DOMAINS) and not missing else "PARTIAL",
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "governance": (
            "GDELT seendate is independent observation time, not publisher publication time. "
            "This receipt measures discovery coverage only; article metadata cannot directly mutate probabilities. "
            "Aggregate PASS requires every domain report itself to pass the discovery gate and every 2025/26 match "
            "to use audited Europe/London Football-Data kickoff time with zero midnight fallbacks/parse failures."
        ),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "passed_domains": passed_domains,
        "coverage": {cid: {
            "either": gate.get("either_team_fixture_coverage_rate"),
            "both": gate.get("both_team_fixture_coverage_rate"),
            "articles": gate.get("article_record_count"),
            "failures": gate.get("query_failure_count"),
            "exact_kickoffs": gate.get("exact_kickoff_count"),
            "midnight_fallbacks": gate.get("fallback_midnight_count"),
            "kickoff_parse_failures": gate.get("kickoff_parse_failure_count"),
            "domain_pass": gate.get("domain_pass"),
        } for cid, gate in domain_gate.items()},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
