#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONSENSUS_ROOT = ROOT / "evidence" / "market_consensus_prospective"
OUT = ROOT / "manifests" / "promotion_consensus_registry_v5530_status.json"
ALLOWED = {"POR_PrimeiraLiga", "ESP_LaLiga", "FRA_Ligue1", "GER_Bundesliga"}


def source_pair_key(row: dict[str, Any]) -> tuple[str, str] | None:
    schema = str(row.get("schema_version") or "")
    if schema.startswith("V5.5.29-exact-line-aligned-independent-consensus"):
        ev = row.get("alignment_evidence") or {}
        a = str(ev.get("kambi_primary_snapshot_sha256") or "")
        b = str(ev.get("marathon_snapshot_sha256") or "")
        return tuple(sorted((a, b))) if a and b else None
    hashes = [str(x) for x in (row.get("constituent_snapshot_sha256") or []) if x]
    if len(hashes) == 2:
        return tuple(sorted(hashes))
    return None


def evidence_kind(row: dict[str, Any]) -> str:
    schema = str(row.get("schema_version") or "")
    if schema.startswith("V5.5.29-exact-line-aligned-independent-consensus"):
        return "EXACT_LINE_ALIGNED"
    if schema.startswith("V5.5.19-strict-three-surface-market-consensus"):
        return "PRIMARY_LINE_STRICT"
    return "OTHER"


def structurally_eligible(row: dict[str, Any]) -> tuple[bool, list[str]]:
    errors = []
    if row.get("competition_id") not in ALLOWED:
        errors.append("COMPETITION_NOT_ALLOWED")
    if row.get("promotion_evidence_eligible") is not True:
        errors.append("PROMOTION_ELIGIBILITY_FALSE")
    groups = sorted(str(x) for x in (row.get("provider_groups") or []))
    if groups != ["kambi", "marathonbet"]:
        errors.append("PROVIDER_GROUPS_NOT_KAMBI_MARATHONBET")
    try:
        if float(row.get("cross_provider_timestamp_spread_seconds")) > 300.0:
            errors.append("TIMESTAMP_SPREAD_GT_300S")
    except Exception:
        errors.append("TIMESTAMP_SPREAD_INVALID")
    required = row.get("required_surface_consensus_eligibility") or row.get("surface_consensus_eligibility") or {}
    for key in ("one_x_two", "asian_handicap", "over_under"):
        if required.get(key) is not True:
            errors.append(f"SURFACE_NOT_ELIGIBLE:{key}")
    pair = source_pair_key(row)
    if pair is None:
        errors.append("SOURCE_PAIR_HASHES_MISSING")
    if evidence_kind(row) == "EXACT_LINE_ALIGNED":
        ev = row.get("alignment_evidence") or {}
        for key in ("interpolation_used", "synthetic_odds_used", "cross_provider_market_value_splicing"):
            if ev.get(key) is not False:
                errors.append(f"EXACT_LINE_GUARD_FAIL:{key}")
    return not errors, errors


def sample_key(row: dict[str, Any], pair: tuple[str, str]) -> tuple[str, ...]:
    return (
        str(row.get("competition_id") or ""),
        str(row.get("season") or ""),
        str(row.get("home_team") or ""),
        str(row.get("away_team") or ""),
        str(row.get("kickoff_utc") or ""),
        pair[0],
        pair[1],
    )


def priority(row: dict[str, Any]) -> tuple[int, str]:
    kind = evidence_kind(row)
    rank = {"PRIMARY_LINE_STRICT": 0, "EXACT_LINE_ALIGNED": 1}.get(kind, 9)
    return rank, str(row.get("consensus_sha256") or "")


def main() -> int:
    candidates = []
    rejected = []
    kinds = Counter()
    for path in CONSENSUS_ROOT.glob("*.json"):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            kind = evidence_kind(row)
            if kind not in {"PRIMARY_LINE_STRICT", "EXACT_LINE_ALIGNED"}:
                continue
            ok, errors = structurally_eligible(row)
            if not ok:
                rejected.append({"path": str(path.relative_to(ROOT)), "kind": kind, "errors": errors})
                continue
            pair = source_pair_key(row)
            assert pair is not None
            candidates.append((sample_key(row, pair), row, path, kind, pair))
            kinds[kind] += 1
        except Exception as exc:
            rejected.append({"path": str(path.relative_to(ROOT)), "kind": "READ_ERROR", "errors": [f"{type(exc).__name__}:{exc}"]})

    grouped: dict[tuple[str, ...], list[tuple[dict, Path, str, tuple[str, str]]]] = {}
    for skey, row, path, kind, pair in candidates:
        grouped.setdefault(skey, []).append((row, path, kind, pair))

    samples = []
    duplicate_candidates_suppressed = 0
    for skey in sorted(grouped):
        pool = grouped[skey]
        pool.sort(key=lambda item: priority(item[0]))
        chosen = pool[0]
        duplicate_candidates_suppressed += max(0, len(pool) - 1)
        row, path, kind, pair = chosen
        samples.append({
            "competition_id": row.get("competition_id"),
            "season": row.get("season"),
            "home_team": row.get("home_team"),
            "away_team": row.get("away_team"),
            "kickoff_utc": row.get("kickoff_utc"),
            "consensus_observed_at_utc": row.get("consensus_observed_at_utc"),
            "cross_provider_timestamp_spread_seconds": row.get("cross_provider_timestamp_spread_seconds"),
            "provider_groups": row.get("provider_groups"),
            "source_snapshot_pair_sha256": list(pair),
            "selected_evidence_kind": kind,
            "selected_consensus_path": str(path.relative_to(ROOT)),
            "selected_consensus_sha256": row.get("consensus_sha256"),
            "duplicate_candidate_count_for_same_observation_pair": len(pool),
            "suppressed_alternatives": [
                {
                    "kind": item[2],
                    "path": str(item[1].relative_to(ROOT)),
                    "consensus_sha256": item[0].get("consensus_sha256"),
                }
                for item in pool[1:]
            ],
        })

    receipt = {
        "schema_version": "V5.5.30-deduplicated-promotion-consensus-registry-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "candidate_evidence_count_before_dedup": len(candidates),
        "candidate_kind_counts_before_dedup": dict(kinds),
        "unique_observation_pair_count": len(samples),
        "duplicate_candidates_suppressed": duplicate_candidates_suppressed,
        "rejected_candidate_count": len(rejected),
        "rejected_candidates": rejected[:100],
        "samples": samples,
        "selection_priority": ["PRIMARY_LINE_STRICT", "EXACT_LINE_ALIGNED"],
        "formal_model_promotion": False,
        "formal_weight_change": False,
        "probability_change": False,
        "policy": "One underlying Kambi+Marathonbet observation pair may contribute at most one promotion evidence sample. Prefer native primary-line strict consensus; use exact-line aligned consensus only when no primary-line strict evidence exists for that same source snapshot pair. This registry is evidence bookkeeping only and cannot promote a model.",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
