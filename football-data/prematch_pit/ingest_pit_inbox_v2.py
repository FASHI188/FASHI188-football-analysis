#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
INBOX = HERE / "inbox"
OUT = HERE / "results"
sys.path.insert(0, str(HERE))
import pit_event_ledger_v2 as pit  # noqa: E402


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    INBOX.mkdir(parents=True, exist_ok=True)
    files = sorted(INBOX.glob("*.json"))
    receipt = {
        "schema_version": "football3-prematch-pit-inbox-import-v2",
        "status": "COMPLETE",
        "input_files": [],
        "observations_seen": 0,
        "eligible_records": 0,
        "ineligible_records": 0,
        "appended_records": 0,
        "duplicate_records": 0,
        "records": [],
    }
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        observations = payload.get("observations") or []
        receipt["input_files"].append(path.name)
        for index, event in enumerate(observations):
            receipt["observations_seen"] += 1
            record = pit.freeze_record(event)
            result = pit.append_record(record)
            eligible = bool(record["gate"]["eligible"])
            receipt["eligible_records" if eligible else "ineligible_records"] += 1
            receipt["appended_records" if result["appended"] else "duplicate_records"] += 1
            receipt["records"].append({
                "input_file": path.name,
                "index": index,
                "record_id": record["record_id"],
                "event_type": event.get("event_type"),
                "source_name": event.get("source_name"),
                "observed_at_utc": event.get("observed_at_utc"),
                "kickoff_at_utc": event.get("kickoff_at_utc"),
                "gate": record["gate"],
                "append": result,
            })
    (OUT / "pit_inbox_import_receipt_v2.json").write_text(
        json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt, indent=2, ensure_ascii=False))


def verify():
    r = json.loads((OUT / "pit_inbox_import_receipt_v2.json").read_text(encoding="utf-8"))
    assert r["status"] == "COMPLETE"
    assert r["observations_seen"] >= 1
    assert r["eligible_records"] + r["ineligible_records"] == r["observations_seen"]
    assert r["appended_records"] + r["duplicate_records"] == r["observations_seen"]
    ledger_rows = [x for x in pit.LEDGER.read_text(encoding="utf-8").splitlines() if x.strip()]
    ids = [json.loads(x)["record_id"] for x in ledger_rows]
    assert len(ids) == len(set(ids))
    receipt_ids = {x["record_id"] for x in r["records"]}
    assert receipt_ids.issubset(set(ids))
    print("PIT_INBOX_IMPORT_V2_VERIFY_PASS")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify"}:
        raise SystemExit("usage: ingest_pit_inbox_v2.py {run|verify}")
    {"run": run, "verify": verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
