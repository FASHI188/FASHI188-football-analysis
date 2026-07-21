#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
if str(VALIDATION) not in sys.path:
    sys.path.insert(0, str(VALIDATION))

from prospective_market_consensus_strict_v5519 import build as build_strict, validate_consensus
from prospective_market_snapshot_v523 import validate as validate_snapshot

SNAPSHOT_ROOT = ROOT / "evidence" / "markets_prospective"
CONSENSUS_ROOT = ROOT / "evidence" / "market_consensus_prospective"
MANIFEST = ROOT / "manifests" / "synchronized_four_league_consensus_v5526_status.json"
ALLOWED_COMPETITIONS = {"POR_PrimeiraLiga", "ESP_LaLiga", "FRA_Ligue1", "GER_Bundesliga"}
REQUIRED_PROVIDER_GROUPS = {"kambi", "marathonbet"}
MAX_SKEW_SECONDS = 300.0


def dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timezone missing: {value}")
    return parsed.astimezone(timezone.utc)


def safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_") or "unknown"


def key(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(row.get("competition_id") or ""),
        str(row.get("season") or ""),
        str(row.get("home_team") or ""),
        str(row.get("away_team") or ""),
        str(row.get("kickoff_utc") or ""),
    )


def load_fresh(batch_start: datetime) -> tuple[dict[tuple[str, str, str, str, str], dict[str, tuple[datetime, dict[str, Any], Path]]], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str, str, str, str], dict[str, tuple[datetime, dict[str, Any], Path]]] = defaultdict(dict)
    rejected: list[dict[str, Any]] = []
    for path in SNAPSHOT_ROOT.glob("*.json"):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            cid = str(row.get("competition_id") or "")
            provider = str(row.get("provider_group") or "")
            if cid not in ALLOWED_COMPETITIONS or provider not in REQUIRED_PROVIDER_GROUPS:
                continue
            observed = dt(str(row.get("freeze_utc")))
            if observed < batch_start:
                continue
            validation = validate_snapshot(row)
            if not validation.get("passed") or not validation.get("formal_pit_eligible"):
                rejected.append({
                    "path": str(path.relative_to(ROOT)),
                    "reason": "V523_INVALID",
                    "errors": validation.get("errors"),
                })
                continue
            k = key(row)
            if not all(k):
                rejected.append({"path": str(path.relative_to(ROOT)), "reason": "IDENTITY_KEY_INCOMPLETE"})
                continue
            current = grouped[k].get(provider)
            if current is None or observed > current[0]:
                grouped[k][provider] = (observed, row, path)
        except Exception as exc:
            rejected.append({"path": str(path.relative_to(ROOT)), "reason": f"READ_ERROR:{type(exc).__name__}:{exc}"})
    return grouped, rejected


def consensus_path(payload: dict[str, Any]) -> Path:
    token = str(payload["consensus_observed_at_utc"]).replace(":", "").replace("+00:00", "Z")
    return CONSENSUS_ROOT / (
        f"{safe(payload['competition_id'])}__{safe(payload['home_team'])}__{safe(payload['away_team'])}__"
        f"{token}__n{payload['provider_count']}__strict.json"
    )


def write_consensus(payload: dict[str, Any]) -> tuple[Path, bool]:
    out = consensus_path(payload)
    if out.exists():
        existing = json.loads(out.read_text(encoding="utf-8"))
        if existing.get("consensus_sha256") != payload.get("consensus_sha256"):
            raise FileExistsError(f"immutable strict consensus path collision: {out}")
        return out, False
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out, True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-start-utc", required=True)
    args = parser.parse_args()
    batch_start = dt(args.batch_start_utc)
    grouped, rejected = load_fresh(batch_start)
    receipt: dict[str, Any] = {
        "schema_version": "V5.5.26-four-league-synchronized-consensus-status-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "batch_start_utc": batch_start.replace(microsecond=0).isoformat(),
        "allowed_competitions": sorted(ALLOWED_COMPETITIONS),
        "required_provider_groups": sorted(REQUIRED_PROVIDER_GROUPS),
        "max_cross_provider_skew_seconds": MAX_SKEW_SECONDS,
        "strict_three_surface_gate": "V5.5.19",
        "status": "NO_SYNCHRONIZED_FOUR_LEAGUE_CONSENSUS",
        "fresh_fixture_key_count": len(grouped),
        "fresh_dual_provider_fixture_count": 0,
        "strict_consensus_count_written": 0,
        "strict_consensus_count_available": 0,
        "promotion_evidence_eligible_count": 0,
        "research_only_line_mismatch_count": 0,
        "timestamp_or_validation_fail_count": 0,
        "fixtures": [],
        "rejected_snapshot_count": len(rejected),
        "rejected_snapshots": rejected[:100],
        "formal_model_promotion": False,
        "formal_weight_change": False,
        "probability_change": False,
    }

    for identity_key in sorted(grouped):
        by_provider = grouped[identity_key]
        cid, season, home, away, kickoff = identity_key
        row: dict[str, Any] = {
            "competition_id": cid,
            "season": season,
            "home_team": home,
            "away_team": away,
            "kickoff_utc": kickoff,
            "fresh_provider_groups": sorted(by_provider),
            "status": "INSUFFICIENT_PROVIDER_GROUPS",
        }
        if set(by_provider) != REQUIRED_PROVIDER_GROUPS:
            receipt["fixtures"].append(row)
            continue
        receipt["fresh_dual_provider_fixture_count"] += 1
        ordered = [by_provider[group] for group in sorted(REQUIRED_PROVIDER_GROUPS)]
        rows = [item[1] for item in ordered]
        paths = [item[2] for item in ordered]
        observed = [item[0] for item in ordered]
        skew = (max(observed) - min(observed)).total_seconds()
        row["cross_provider_timestamp_spread_seconds"] = skew
        row["constituent_snapshots"] = [
            {
                "provider_group": rows[i].get("provider_group"),
                "path": str(paths[i].relative_to(ROOT)),
                "freeze_utc": rows[i].get("freeze_utc"),
                "raw_snapshot_sha256": rows[i].get("raw_snapshot_sha256"),
            }
            for i in range(len(rows))
        ]
        if skew > MAX_SKEW_SECONDS:
            row["status"] = "TIMESTAMP_SKEW_FAIL_CLOSED"
            receipt["timestamp_or_validation_fail_count"] += 1
            receipt["fixtures"].append(row)
            continue
        try:
            payload = build_strict(rows)
            validation = validate_consensus(payload)
            row["strict_validation"] = validation
            row["required_surface_consensus_eligibility"] = payload.get("required_surface_consensus_eligibility")
            row["promotion_evidence_eligible"] = bool(payload.get("promotion_evidence_eligible"))
            row["promotion_ineligibility_reasons"] = payload.get("promotion_ineligibility_reasons")
            row["consensus_sha256"] = payload.get("consensus_sha256")
            if not validation.get("passed"):
                row["status"] = "STRICT_VALIDATION_FAIL_CLOSED"
                receipt["timestamp_or_validation_fail_count"] += 1
            elif not payload.get("promotion_evidence_eligible"):
                row["status"] = "SYNCHRONIZED_RESEARCH_ONLY_LINE_MISMATCH"
                receipt["research_only_line_mismatch_count"] += 1
            else:
                out, created = write_consensus(payload)
                row["status"] = "STRICT_THREE_SURFACE_CONSENSUS_WRITTEN" if created else "STRICT_THREE_SURFACE_CONSENSUS_ALREADY_PRESENT"
                row["consensus_path"] = str(out.relative_to(ROOT))
                receipt["strict_consensus_count_available"] += 1
                receipt["promotion_evidence_eligible_count"] += 1
                if created:
                    receipt["strict_consensus_count_written"] += 1
        except Exception as exc:
            row["status"] = "STRICT_CONSENSUS_FAIL_CLOSED"
            row["error"] = f"{type(exc).__name__}: {exc}"
            receipt["timestamp_or_validation_fail_count"] += 1
        receipt["fixtures"].append(row)

    if receipt["promotion_evidence_eligible_count"]:
        receipt["status"] = "PASS_STRICT_FOUR_LEAGUE_CONSENSUS_AVAILABLE"
    elif receipt["fresh_dual_provider_fixture_count"]:
        receipt["status"] = "DUAL_PROVIDER_DATA_AVAILABLE_NO_PROMOTION_ELIGIBLE_CONSENSUS"
    receipt["policy"] = (
        "Every fixture is keyed by exact competition, season, canonical home, canonical away and kickoff UTC. Only fresh current-batch Kambi and Marathonbet V5.2.3 snapshots are eligible. Cross-provider skew must be <=300 seconds and V5.5.19 must find comparable 1X2, AH and OU. Line mismatch is research-only. A consensus file is evidence for later OOF research only and never self-promotes a model or changes formal probabilities/weights."
    )
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
