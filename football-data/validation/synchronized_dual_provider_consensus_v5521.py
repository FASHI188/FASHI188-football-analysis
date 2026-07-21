#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
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
MANIFEST = ROOT / "manifests" / "synchronized_dual_provider_consensus_v5521_status.json"
REQUIRED_PROVIDER_GROUPS = {"kambi", "marathonbet"}

TARGETS = [
    ("POR_PrimeiraLiga", "2026/27", "Estoril Praia", "FC Famalicão", "2026-08-07T19:15:00+00:00"),
    ("ESP_LaLiga", "2026/27", "Deportivo Alavés", "Getafe CF", "2026-08-15T17:30:00+00:00"),
    ("FRA_Ligue1", "2026/27", "Olympique de Marseille", "RC Strasbourg Alsace", "2026-08-21T18:45:00+00:00"),
    ("GER_Bundesliga", "2026/27", "FC Bayern München", "VfB Stuttgart", "2026-08-28T18:30:00+00:00"),
]


def dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timezone missing: {value}")
    return parsed.astimezone(timezone.utc)


def safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_") or "unknown"


def same_target(row: dict[str, Any], target: tuple[str, str, str, str, str]) -> bool:
    cid, season, home, away, kickoff = target
    return (
        row.get("competition_id") == cid
        and row.get("season") == season
        and row.get("home_team") == home
        and row.get("away_team") == away
        and row.get("kickoff_utc") == kickoff
        and row.get("settlement_scope") == "90m_including_stoppage"
    )


def fresh_snapshots(target: tuple[str, str, str, str, str], batch_start: datetime) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    newest: dict[str, tuple[datetime, dict[str, Any], Path]] = {}
    rejected: list[dict[str, Any]] = []
    for path in SNAPSHOT_ROOT.glob("*.json"):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            if not same_target(row, target):
                continue
            group = str(row.get("provider_group") or "")
            if group not in REQUIRED_PROVIDER_GROUPS:
                continue
            validation = validate_snapshot(row)
            if not validation.get("passed") or not validation.get("formal_pit_eligible"):
                rejected.append({"path": str(path.relative_to(ROOT)), "reason": "V523_INVALID", "errors": validation.get("errors")})
                continue
            observed = dt(str(row.get("freeze_utc")))
            if observed < batch_start:
                continue
            current = newest.get(group)
            if current is None or observed > current[0]:
                newest[group] = (observed, row, path)
        except Exception as exc:
            rejected.append({"path": str(path.relative_to(ROOT)), "reason": f"READ_OR_VALIDATE_ERROR:{type(exc).__name__}:{exc}"})
    return {group: item[1] for group, item in newest.items()}, rejected


def write_consensus(payload: dict[str, Any]) -> tuple[str, bool]:
    token = str(payload["consensus_observed_at_utc"]).replace(":", "").replace("+00:00", "Z")
    out = CONSENSUS_ROOT / (
        f"{safe(payload['competition_id'])}__{safe(payload['home_team'])}__{safe(payload['away_team'])}__"
        f"{token}__n{payload['provider_count']}__strict.json"
    )
    if out.exists():
        existing = json.loads(out.read_text(encoding="utf-8"))
        if existing.get("consensus_sha256") != payload.get("consensus_sha256"):
            raise FileExistsError(f"immutable strict consensus path collision: {out}")
        return str(out.relative_to(ROOT)), False
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(out.relative_to(ROOT)), True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-start-utc", required=True)
    args = parser.parse_args()
    batch_start = dt(args.batch_start_utc)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    receipt: dict[str, Any] = {
        "schema_version": "V5.5.21-synchronized-dual-provider-consensus-status-r1",
        "generated_at_utc": now.isoformat(),
        "batch_start_utc": batch_start.replace(microsecond=0).isoformat(),
        "required_provider_groups": sorted(REQUIRED_PROVIDER_GROUPS),
        "max_cross_provider_skew_seconds": 300,
        "strict_three_surface_gate": "V5.5.19",
        "status": "NO_SYNCHRONIZED_DUAL_PROVIDER_CONSENSUS",
        "targets": [],
        "strict_consensus_count_written": 0,
        "promotion_evidence_eligible_count": 0,
        "research_only_line_mismatch_count": 0,
        "formal_weight_change": False,
        "probability_change": False,
        "formal_model_promotion": False,
    }
    for target in TARGETS:
        cid, season, home, away, kickoff = target
        rows_by_group, rejected = fresh_snapshots(target, batch_start)
        target_row: dict[str, Any] = {
            "competition_id": cid,
            "season": season,
            "home_team": home,
            "away_team": away,
            "kickoff_utc": kickoff,
            "fresh_provider_groups": sorted(rows_by_group),
            "fresh_snapshot_count": len(rows_by_group),
            "rejected_snapshot_count": len(rejected),
            "rejected_snapshots": rejected[:20],
            "status": "INSUFFICIENT_SYNCHRONIZED_PROVIDER_GROUPS",
        }
        if set(rows_by_group) != REQUIRED_PROVIDER_GROUPS:
            receipt["targets"].append(target_row)
            continue
        rows = [rows_by_group[group] for group in sorted(REQUIRED_PROVIDER_GROUPS)]
        try:
            payload = build_strict(rows)
            validation = validate_consensus(payload)
            target_row["cross_provider_timestamp_spread_seconds"] = payload.get("cross_provider_timestamp_spread_seconds")
            target_row["provider_groups"] = payload.get("provider_groups")
            target_row["required_surface_consensus_eligibility"] = payload.get("required_surface_consensus_eligibility")
            target_row["promotion_evidence_eligible"] = payload.get("promotion_evidence_eligible")
            target_row["promotion_ineligibility_reasons"] = payload.get("promotion_ineligibility_reasons")
            target_row["strict_validation"] = validation
            target_row["constituent_snapshot_sha256"] = payload.get("constituent_snapshot_sha256")
            if not validation.get("passed"):
                target_row["status"] = "STRICT_CONSENSUS_VALIDATION_FAILED"
            elif not payload.get("promotion_evidence_eligible"):
                target_row["status"] = "SYNCHRONIZED_RESEARCH_ONLY_LINE_MISMATCH"
                receipt["research_only_line_mismatch_count"] += 1
            else:
                path, created = write_consensus(payload)
                target_row["status"] = "STRICT_THREE_SURFACE_CONSENSUS_WRITTEN" if created else "STRICT_THREE_SURFACE_CONSENSUS_ALREADY_PRESENT"
                target_row["consensus_path"] = path
                target_row["consensus_sha256"] = payload.get("consensus_sha256")
                if created:
                    receipt["strict_consensus_count_written"] += 1
                receipt["promotion_evidence_eligible_count"] += 1
        except Exception as exc:
            target_row["status"] = "STRICT_CONSENSUS_FAIL_CLOSED"
            target_row["error"] = f"{type(exc).__name__}: {exc}"
        receipt["targets"].append(target_row)

    if receipt["promotion_evidence_eligible_count"]:
        receipt["status"] = "PASS_STRICT_SYNCHRONIZED_CONSENSUS_AVAILABLE"
    elif receipt["research_only_line_mismatch_count"]:
        receipt["status"] = "SYNCHRONIZED_DATA_AVAILABLE_BUT_STRICT_LINES_MISMATCH"
    receipt["policy"] = (
        "A written strict consensus is promotion-evidence eligible only when Kambi and Marathonbet both provide valid V5.2.3 snapshots in the same batch, cross-provider spread is <=300s, provider groups are unique, and 1X2/AH/OU are all comparable. This creates evidence only; it does not promote a model or change formal weights/probabilities."
    )
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
