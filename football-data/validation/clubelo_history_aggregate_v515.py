#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "clubelo_residual_challenger_v515.json"
OUT_ROOT = ROOT / "evidence" / "clubelo_v515"
MANIFEST = ROOT / "manifests" / "clubelo_history_ingest_v515_status.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    args = parser.parse_args()
    input_root = Path(args.input)
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    domains = list(cfg["domains"])

    reports = {}
    history_rows = {}
    missing = []
    for cid in domains:
        report_path = input_root / f"{cid}.json"
        history_path = input_root / f"{cid}.jsonl"
        if not report_path.exists() or not history_path.exists():
            missing.append(cid)
            continue
        report = json.loads(report_path.read_text(encoding="utf-8"))
        reports[cid] = report
        for line in history_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            key = (
                str(row.get("clubelo_name") or ""),
                str(row.get("from") or ""),
                str(row.get("to") or ""),
            )
            history_rows[key] = row

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    for cid, report in reports.items():
        (OUT_ROOT / f"{cid}_team_map.json").write_text(
            json.dumps({"competition_id": cid, "mappings": report.get("mappings", {})}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    history_path = OUT_ROOT / "club_histories.jsonl"
    with history_path.open("w", encoding="utf-8") as handle:
        for key in sorted(history_rows):
            handle.write(json.dumps(history_rows[key], ensure_ascii=False, sort_keys=True) + "\n")

    passed_domains = [cid for cid, report in reports.items() if report.get("status") == "PASS"]
    execution_failures = [cid for cid, report in reports.items() if report.get("status") == "EXECUTION_FAILURE"]
    payload = {
        "schema_version": "V5.1.5-clubelo-history-ingest-r2",
        "generated_at_utc": utc_now(),
        "requested_domains": domains,
        "completed_domains": list(reports),
        "missing_domains": missing,
        "execution_failure_domains": execution_failures,
        "passed_domains": passed_domains,
        "domain_reports": reports,
        "unique_club_history_count": len({key[0] for key in history_rows}),
        "history_interval_row_count": len(history_rows),
        "history_output": str(history_path.relative_to(ROOT)),
        "status": "PASS" if len(passed_domains) == len(domains) and not missing and not execution_failures else "PARTIAL",
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "pit_rule": "For every target match, downstream code must select a rating interval containing target_date_minus_one_day.",
        "identity_policy": "Audited explicit aliases first, normalized exact second, high-threshold fuzzy only as last resort. The invalid historical Ath Madrid -> Real Madrid fuzzy mapping is forbidden.",
        "history_url_policy": "History endpoint slugs transliterate accents, remove spaces/apostrophes and keep ASCII alphanumerics/hyphens, matching the audited ClubElo client contract.",
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "passed_domains": passed_domains,
        "missing_domains": missing,
        "execution_failure_domains": execution_failures,
        "unique_club_history_count": payload["unique_club_history_count"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
