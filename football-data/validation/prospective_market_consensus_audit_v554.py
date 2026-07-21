#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence" / "market_consensus_prospective"
OUT = ROOT / "manifests" / "prospective_market_consensus_v554_status.json"
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in __import__('sys').path:
    __import__('sys').path.insert(0, str(VALIDATION))

from prospective_market_consensus_v554 import validate_consensus


def main() -> int:
    valid = []
    invalid = []
    duplicates = defaultdict(list)
    key_seen = {}
    for path in sorted(EVIDENCE.glob("*.json")) if EVIDENCE.exists() else []:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            result = validate_consensus(payload)
        except Exception as exc:
            invalid.append({"path": str(path.relative_to(ROOT)), "errors": [f"{type(exc).__name__}: {exc}"]})
            continue
        if not result.get("passed"):
            invalid.append({"path": str(path.relative_to(ROOT)), "errors": result.get("errors") or []})
            continue
        key = "|".join([
            str(payload.get("competition_id")), str(payload.get("kickoff_utc")),
            str(payload.get("home_team")), str(payload.get("away_team")),
            str(payload.get("consensus_observed_at_utc")),
        ])
        if key in key_seen:
            duplicates[key].extend([key_seen[key], str(path.relative_to(ROOT))])
        else:
            key_seen[key] = str(path.relative_to(ROOT))
        valid.append(payload)

    competition_counts = Counter(str(row.get("competition_id")) for row in valid)
    ou25_counts = Counter(
        str(row.get("competition_id")) for row in valid
        if bool((row.get("surface_consensus_eligibility") or {}).get("over_under_2_5"))
    )
    provider_count_distribution = Counter(int(row.get("provider_count") or 0) for row in valid)
    payload = {
        "schema_version": "V5.5.4-prospective-market-consensus-audit-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "NO_CONSENSUS_YET" if not valid and not invalid else ("FAIL" if invalid or duplicates else "PASS"),
        "consensus_file_count": len(valid) + len(invalid),
        "valid_consensus_count": len(valid),
        "invalid_consensus_count": len(invalid),
        "duplicate_consensus_key_count": len(duplicates),
        "duplicate_consensus_keys": dict(duplicates),
        "competition_counts": dict(sorted(competition_counts.items())),
        "ou25_eligible_competition_counts": dict(sorted(ou25_counts.items())),
        "provider_count_distribution": {str(k): v for k, v in sorted(provider_count_distribution.items())},
        "invalid_files": invalid,
        "minimum_independent_provider_groups": 2,
        "maximum_cross_provider_skew_seconds": 300,
        "formal_weight_change": False,
        "probability_change": False,
        "governance": "A valid consensus is prospective research evidence derived only from individually valid V5.2.3 source snapshots with unique provider groups. It does not itself authorize formal probability mutation."
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] != "FAIL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
