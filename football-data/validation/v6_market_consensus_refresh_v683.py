#!/usr/bin/env python3
"""V6.8.3 rebuild the independent-provider market consensus inventory.

The old V5.5.4 receipt could become stale because later exact-line consensus files were created
without retriggering that audit.  This script scans the evidence directory itself every run,
validates the invariant fields needed by downstream matrix research, de-duplicates identical
consensus identities, and emits a fresh receipt.  It does not score outcomes or change any
formal probability.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence" / "market_consensus_prospective"
OUT = ROOT / "manifests" / "v6_market_consensus_refresh_v683_status.json"
LEGACY_OUT = ROOT / "manifests" / "prospective_market_consensus_v554_status.json"
MAX_SKEW = 300.0


def key(row: dict[str, Any]) -> tuple[str, str, str, str, str, str]:
    return (
        str(row.get("competition_id") or ""), str(row.get("season") or ""),
        str(row.get("home_team") or ""), str(row.get("away_team") or ""),
        str(row.get("kickoff_utc") or ""), str(row.get("consensus_observed_at_utc") or ""),
    )


def validate(row: dict[str, Any]) -> list[str]:
    errors = []
    required_identity = ["competition_id", "home_team", "away_team", "kickoff_utc", "consensus_observed_at_utc"]
    for field in required_identity:
        if not row.get(field): errors.append(f"missing_{field}")
    groups = [str(x) for x in row.get("provider_groups") or [] if x]
    if len(set(groups)) < 2: errors.append("fewer_than_two_unique_provider_groups")
    if int(row.get("provider_count") or 0) < 2: errors.append("provider_count_below_2")
    try:
        if float(row.get("cross_provider_timestamp_spread_seconds")) > MAX_SKEW: errors.append("timestamp_skew_gt_300s")
    except Exception:
        errors.append("invalid_timestamp_spread")
    if str(row.get("consensus_type")) != "INDEPENDENT_PROVIDER_CONSENSUS": errors.append("wrong_consensus_type")
    if not bool(row.get("promotion_evidence_eligible")): errors.append("not_promotion_evidence_eligible")
    eligibility = row.get("surface_consensus_eligibility") or {}
    for surface in ("one_x_two", "asian_handicap", "over_under"):
        if not bool(eligibility.get(surface)): errors.append(f"surface_not_eligible_{surface}")
    for surface in ("one_x_two", "asian_handicap", "over_under"):
        if not isinstance(row.get(surface), dict): errors.append(f"missing_surface_{surface}")
    return errors


def main() -> int:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    paths = sorted(EVIDENCE.glob("*.json")) if EVIDENCE.exists() else []
    seen = {}
    duplicates = defaultdict(list)
    invalid = []
    valid_rows = []
    for path in paths:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            invalid.append({"path": str(path.relative_to(ROOT)), "errors": [f"{type(exc).__name__}: {exc}"]})
            continue
        errors = validate(row)
        if errors:
            invalid.append({"path": str(path.relative_to(ROOT)), "errors": errors})
            continue
        k = key(row)
        if k in seen:
            duplicates["|".join(k)].append(str(path.relative_to(ROOT)))
            continue
        seen[k] = str(path.relative_to(ROOT))
        valid_rows.append((row, path))

    competition_counts = Counter()
    ou25_counts = Counter()
    provider_distribution = Counter()
    pair_distribution = Counter()
    for row, _path in valid_rows:
        cid = str(row["competition_id"])
        competition_counts[cid] += 1
        provider_distribution[str(int(row.get("provider_count") or 0))] += 1
        pair_distribution["+".join(sorted(set(str(x) for x in row.get("provider_groups") or [])))] += 1
        ou = row.get("over_under_2_5") or row.get("over_under") or {}
        if abs(float(ou.get("line", 999.0)) - 2.5) <= 1e-9:
            ou25_counts[cid] += 1

    status = "PASS_CONSENSUS_AVAILABLE" if valid_rows else "NO_CONSENSUS_YET"
    payload = {
        "schema_version": "V6.8.3-independent-market-consensus-refresh-r1",
        "generated_at_utc": now,
        "status": status,
        "consensus_file_count": len(paths),
        "valid_consensus_count": len(valid_rows),
        "invalid_consensus_count": len(invalid),
        "duplicate_consensus_key_count": len(duplicates),
        "duplicate_consensus_keys": dict(duplicates),
        "competition_counts": dict(sorted(competition_counts.items())),
        "ou25_eligible_competition_counts": dict(sorted(ou25_counts.items())),
        "provider_count_distribution": dict(provider_distribution),
        "provider_pair_distribution": dict(pair_distribution),
        "invalid_files": invalid,
        "minimum_independent_provider_groups": 2,
        "maximum_cross_provider_skew_seconds": MAX_SKEW,
        "formal_weight_change": False,
        "probability_change": False,
        "governance": "Fresh inventory of current independent-provider PIT consensus evidence. Consensus availability does not equal outcome validation or authorize formal probability mutation."
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    # Refresh the legacy path so every existing downstream consumer stops reading a stale zero-count receipt.
    legacy = dict(payload)
    legacy["schema_version"] = "V5.5.4-prospective-market-consensus-audit-r2-v683-refresh"
    LEGACY_OUT.write_text(json.dumps(legacy, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
