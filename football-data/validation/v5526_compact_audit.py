#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "v5526_compact_audit_status.json"
FILES = {
    "marathon": ROOT / "manifests" / "marathonbet_four_league_capture_v5522_status.json",
    "kambi": ROOT / "manifests" / "kambi_four_league_capture_v5525_status.json",
    "consensus": ROOT / "manifests" / "synchronized_four_league_consensus_v5526_status.json",
    "discovery": ROOT / "manifests" / "kambi_four_league_discovery_v5523_status.json",
}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def compact_marathon(d):
    if not d:
        return {"present": False}
    league_counts = {}
    for row in d.get("leagues", []):
        cid = row.get("competition_id")
        statuses = Counter(x.get("formal_status") or x.get("parse_status") for x in row.get("fixtures", []))
        league_counts[cid] = {
            "status": row.get("status"),
            "complete_surface_fixture_count": row.get("complete_surface_fixture_count", 0),
            "formal_snapshot_count_written": row.get("formal_snapshot_count_written", 0),
            "fixture_status_counts": dict(statuses),
        }
    return {
        "present": True,
        "status": d.get("status"),
        "formal_snapshot_count_written": d.get("formal_snapshot_count_written", 0),
        "complete_surface_fixture_count": d.get("complete_surface_fixture_count", 0),
        "unresolved_identity_count": d.get("unresolved_identity_count", 0),
        "leagues": league_counts,
    }


def compact_kambi(d):
    if not d:
        return {"present": False}
    statuses = Counter()
    by_comp = defaultdict(Counter)
    examples = defaultdict(list)
    for row in d.get("events", []):
        st = str(row.get("status") or "UNKNOWN")
        cid = str(row.get("competition_id") or "UNKNOWN")
        statuses[st] += 1
        by_comp[cid][st] += 1
        if st not in {"VALID_KAMBI_PIT_SNAPSHOT_WRITTEN", "ALREADY_PRESENT_IDENTICAL"} and len(examples[st]) < 5:
            examples[st].append({
                "competition_id": cid,
                "source_home": row.get("source_home"),
                "source_away": row.get("source_away"),
                "canonical_home": row.get("canonical_home"),
                "canonical_away": row.get("canonical_away"),
                "provider_start": row.get("provider_start"),
                "error": row.get("error"),
            })
    return {
        "present": True,
        "status": d.get("status"),
        "target_group_event_count": d.get("target_group_event_count", 0),
        "crosschecked_event_count": d.get("crosschecked_event_count", 0),
        "formal_snapshot_count_written": d.get("formal_snapshot_count_written", 0),
        "identity_unresolved_count": d.get("identity_unresolved_count", 0),
        "crosscheck_missing_count": d.get("crosscheck_missing_count", 0),
        "detail_or_market_fail_count": d.get("detail_or_market_fail_count", 0),
        "event_status_counts": dict(statuses),
        "by_competition_status_counts": {k: dict(v) for k, v in by_comp.items()},
        "nonpass_examples": dict(examples),
        "identity_crosscheck_only_no_market_splicing": d.get("identity_crosscheck_only_no_market_splicing"),
    }


def compact_consensus(d):
    if not d:
        return {"present": False}
    statuses = Counter()
    by_comp = defaultdict(Counter)
    examples = defaultdict(list)
    for row in d.get("fixtures", []):
        st = str(row.get("status") or "UNKNOWN")
        cid = str(row.get("competition_id") or "UNKNOWN")
        statuses[st] += 1
        by_comp[cid][st] += 1
        if st != "INSUFFICIENT_PROVIDER_GROUPS" and len(examples[st]) < 5:
            examples[st].append({
                "competition_id": cid,
                "home_team": row.get("home_team"),
                "away_team": row.get("away_team"),
                "cross_provider_timestamp_spread_seconds": row.get("cross_provider_timestamp_spread_seconds"),
                "required_surface_consensus_eligibility": row.get("required_surface_consensus_eligibility"),
                "promotion_evidence_eligible": row.get("promotion_evidence_eligible"),
                "promotion_ineligibility_reasons": row.get("promotion_ineligibility_reasons"),
                "error": row.get("error"),
            })
    return {
        "present": True,
        "status": d.get("status"),
        "fresh_fixture_key_count": d.get("fresh_fixture_key_count", 0),
        "fresh_dual_provider_fixture_count": d.get("fresh_dual_provider_fixture_count", 0),
        "strict_consensus_count_written": d.get("strict_consensus_count_written", 0),
        "strict_consensus_count_available": d.get("strict_consensus_count_available", 0),
        "promotion_evidence_eligible_count": d.get("promotion_evidence_eligible_count", 0),
        "research_only_line_mismatch_count": d.get("research_only_line_mismatch_count", 0),
        "timestamp_or_validation_fail_count": d.get("timestamp_or_validation_fail_count", 0),
        "fixture_status_counts": dict(statuses),
        "by_competition_status_counts": {k: dict(v) for k, v in by_comp.items()},
        "interesting_examples": dict(examples),
    }


def compact_discovery(d):
    if not d:
        return {"present": False}
    return {
        "present": True,
        "status": d.get("status"),
        "event_count": d.get("event_count", 0),
        "four_league_matched_event_count": d.get("four_league_matched_event_count", 0),
        "target_counts": {k: v.get("matched_event_count", 0) for k, v in (d.get("target_leagues") or {}).items()},
    }


def main():
    data = {k: load(v) for k, v in FILES.items()}
    out = {
        "schema_version": "V5.5.26-compact-audit-r1",
        "marathon": compact_marathon(data["marathon"]),
        "kambi": compact_kambi(data["kambi"]),
        "consensus": compact_consensus(data["consensus"]),
        "discovery": compact_discovery(data["discovery"]),
        "formal_weight_change": False,
        "probability_change": False,
        "audit_only": True,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
