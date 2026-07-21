#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

from prospective_market_snapshot_v523 import validate

EVIDENCE = ROOT / "evidence" / "markets_prospective"
OUT = ROOT / "manifests" / "prospective_market_snapshot_v523_status.json"


def _dedup_key(snapshot: dict) -> str:
    return "|".join(str(snapshot.get(key) or "") for key in (
        "competition_id", "kickoff_utc", "home_team", "away_team", "provider_group", "freeze_utc"
    ))


def main() -> int:
    files = sorted(EVIDENCE.rglob("*.json")) if EVIDENCE.exists() else []
    results = []
    key_counts = Counter()
    competition_counts = Counter()
    valid_count = 0

    for path in files:
        try:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            result = validate(snapshot)
            key = _dedup_key(snapshot)
            key_counts[key] += 1
            competition_counts[str(snapshot.get("competition_id") or "UNKNOWN")] += 1
            if result["passed"]:
                valid_count += 1
            results.append({
                "path": str(path.relative_to(ROOT)),
                "dedup_key": key,
                "passed": result["passed"],
                "errors": result["errors"],
                "computed_raw_snapshot_sha256": result["computed_raw_snapshot_sha256"],
                "surface_timestamp_spread_seconds": result["surface_timestamp_spread_seconds"],
            })
        except Exception as exc:
            results.append({
                "path": str(path.relative_to(ROOT)),
                "passed": False,
                "errors": [f"{type(exc).__name__}: {exc}"],
            })

    duplicate_keys = {key: count for key, count in key_counts.items() if key and count > 1}
    invalid = [row for row in results if not row.get("passed")]
    status = (
        "NO_SNAPSHOTS_YET" if not files
        else "PASS" if not invalid and not duplicate_keys
        else "FAIL"
    )
    payload = {
        "schema_version": "V5.2.3-prospective-market-snapshot-audit-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": status,
        "snapshot_count": len(files),
        "valid_snapshot_count": valid_count,
        "invalid_snapshot_count": len(invalid),
        "duplicate_key_count": len(duplicate_keys),
        "duplicate_keys": duplicate_keys,
        "competition_counts": dict(competition_counts),
        "results": results,
        "formal_pit_eligible_snapshot_count": valid_count if not duplicate_keys else 0,
        "formal_weight_change": False,
        "probability_change": False,
        "governance": "A passing snapshot is eligible as point-in-time market evidence, not automatically as a formal model input. Per-match CURRENT synchronization, independence and unified-matrix gates still apply."
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": status,
        "snapshot_count": len(files),
        "valid_snapshot_count": valid_count,
        "invalid_snapshot_count": len(invalid),
        "duplicate_key_count": len(duplicate_keys),
    }, ensure_ascii=False, indent=2))
    return 0 if status in {"PASS", "NO_SNAPSHOTS_YET"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
