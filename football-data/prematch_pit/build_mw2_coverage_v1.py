#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "batches" / "2026_27_mw2_manifest.json"
LEDGER = HERE / "ledger" / "prematch_events_v2.jsonl"
OUT = HERE / "results" / "mw2_coverage_receipt_v1.json"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def main():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    fixtures = manifest["fixtures"]
    keys = {(x["home_team"], x["away_team"], x["kickoff_at_utc"]): x["fixture_key"] for x in fixtures}
    coverage = {x["fixture_key"]: {"home_team": x["home_team"], "away_team": x["away_team"], "kickoff_at_utc": x["kickoff_at_utc"], "eligible_records": 0, "event_types": {}, "availability_statuses": {}, "record_ids": []} for x in fixtures}
    unmatched = []
    total_eligible = 0
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if not (rec.get("gate") or {}).get("eligible"):
            continue
        ev = rec.get("event") or {}
        key = keys.get((ev.get("home_team"), ev.get("away_team"), ev.get("kickoff_at_utc")))
        if key is None:
            unmatched.append(rec.get("record_id"))
            continue
        c = coverage[key]
        c["eligible_records"] += 1
        c["record_ids"].append(rec["record_id"])
        typ = ev.get("event_type") or "unknown"
        c["event_types"][typ] = c["event_types"].get(typ, 0) + 1
        if typ == "player_availability":
            st = ev.get("availability_status") or "unknown"
            c["availability_statuses"][st] = c["availability_statuses"].get(st, 0) + 1
        total_eligible += 1
    for c in coverage.values():
        c["record_ids"].sort()
    fixture_counts = Counter(c["eligible_records"] for c in coverage.values())
    result = {
        "schema_version": "football3-prematch-pit-mw2-coverage-v1",
        "status": "COMPLETE",
        "generated_at_utc": utc_now(),
        "batch_id": manifest["batch_id"],
        "formal_weight": 0,
        "result_labels_accessed": False,
        "fixture_count": len(fixtures),
        "batch_eligible_records": total_eligible,
        "fixtures_with_any_eligible_record": sum(1 for c in coverage.values() if c["eligible_records"] > 0),
        "fixtures_without_eligible_record": sum(1 for c in coverage.values() if c["eligible_records"] == 0),
        "eligible_record_count_distribution": {str(k): int(v) for k, v in sorted(fixture_counts.items())},
        "coverage": coverage,
        "ledger_eligible_records_outside_mw2_batch": len(unmatched),
        "rule": "Coverage counts only gate-eligible append-only PIT records whose home/away/kickoff identity exactly matches the frozen MW2 fixture manifest. Zero means no eligible record has been frozen yet, not no real-world issue exists."
    }
    assert result["fixture_count"] == 10
    assert coverage["ENG_PL_MW2_CRY_MCI"]["eligible_records"] == 5
    assert coverage["ENG_PL_MW2_AVL_ARS"]["eligible_records"] >= 1
    assert coverage["ENG_PL_MW2_TOT_NEW"]["eligible_records"] >= 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
