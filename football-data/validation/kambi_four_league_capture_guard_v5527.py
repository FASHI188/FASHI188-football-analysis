#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "manifests" / "kambi_four_league_capture_v5525_status.json"
OUT = ROOT / "manifests" / "kambi_four_league_capture_guard_v5527_status.json"


def main() -> int:
    if not SOURCE.exists():
        raise SystemExit("V5.5.25 Kambi capture receipt missing")
    d = json.loads(SOURCE.read_text(encoding="utf-8"))
    statuses = Counter()
    unresolved = []
    market_fail = []
    by_comp = defaultdict(Counter)
    written_validation_failures = []

    for row in d.get("events", []):
        status = str(row.get("status") or "UNKNOWN")
        cid = str(row.get("competition_id") or "UNKNOWN")
        statuses[status] += 1
        by_comp[cid][status] += 1
        if status == "CURRENT_SEASON_IDENTITY_UNRESOLVED":
            unresolved.append({
                "competition_id": cid,
                "source_home": row.get("source_home"),
                "source_away": row.get("source_away"),
                "provider_start": row.get("provider_start"),
            })
        if status == "DETAIL_OR_MARKET_FAIL_CLOSED":
            market_fail.append({
                "competition_id": cid,
                "source_home": row.get("source_home"),
                "source_away": row.get("source_away"),
                "canonical_home": row.get("canonical_home"),
                "canonical_away": row.get("canonical_away"),
                "error": row.get("error"),
            })
        if status in {"VALID_KAMBI_PIT_SNAPSHOT_WRITTEN", "ALREADY_PRESENT_IDENTICAL"}:
            v = row.get("v523_validation") or {}
            if v.get("passed") is not True or v.get("formal_pit_eligible") is not True:
                written_validation_failures.append({
                    "competition_id": cid,
                    "source_home": row.get("source_home"),
                    "source_away": row.get("source_away"),
                    "validation": v,
                })

    hard_errors = []
    if d.get("provider_group") != "kambi":
        hard_errors.append("PROVIDER_GROUP_NOT_KAMBI")
    if d.get("identity_crosscheck_only_no_market_splicing") is not True:
        hard_errors.append("IDENTITY_CROSSCHECK_MARKET_SPLICING_GUARD_MISSING")
    if int(d.get("promotion_sample_count_change", -1)) != 0:
        hard_errors.append("SINGLE_PROVIDER_CAPTURE_CHANGED_PROMOTION_SAMPLE_COUNT")
    if d.get("formal_weight_change") is not False or d.get("probability_change") is not False:
        hard_errors.append("SINGLE_PROVIDER_CAPTURE_CHANGED_FORMAL_MODEL")
    if unresolved or int(d.get("identity_unresolved_count", 0)) != len(unresolved):
        hard_errors.append("CURRENT_SEASON_IDENTITY_UNRESOLVED")
    if written_validation_failures:
        hard_errors.append("WRITTEN_KAMBI_PIT_WITHOUT_V523_PASS")

    receipt = {
        "schema_version": "V5.5.27-kambi-four-league-capture-guard-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if not hard_errors else "FAIL",
        "source_receipt": str(SOURCE.relative_to(ROOT)),
        "source_status": d.get("status"),
        "target_group_event_count": d.get("target_group_event_count", 0),
        "crosschecked_event_count": d.get("crosschecked_event_count", 0),
        "formal_snapshot_count_written": d.get("formal_snapshot_count_written", 0),
        "identity_unresolved_count": len(unresolved),
        "crosscheck_missing_count": d.get("crosscheck_missing_count", 0),
        "detail_or_market_fail_count": len(market_fail),
        "event_status_counts": dict(statuses),
        "by_competition_status_counts": {k: dict(v) for k, v in by_comp.items()},
        "unresolved_identities": unresolved,
        "detail_or_market_failures": market_fail[:50],
        "written_validation_failures": written_validation_failures,
        "hard_errors": hard_errors,
        "formal_weight_change": False,
        "probability_change": False,
        "policy": "Identity misses are hard governance failures and must be fixed by exact current-season aliases only. Missing fresh second-provider crosschecks and incomplete Kambi market surfaces remain fail-closed data availability conditions, not reasons to fabricate identity or splice prices.",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0 if not hard_errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
