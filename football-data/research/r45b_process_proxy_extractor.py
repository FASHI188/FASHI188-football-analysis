#!/usr/bin/env python3
"""Deterministic R45B SHOTS_SOT_PROXY extractor.

Zero-label research utility. For one target team, it extracts exactly the last
10 eligible historical league matches and emits only shots / shots-on-target
process proxies under the exact R45B forward-capture contract fields.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

EXTRACTOR_VERSION = "R45B-PROCESS-PROXY-EXTRACTOR-R1"
PREREG_SCHEMA = "R45B-PROCESS-PROXY-PREREG-R1"
RECORD_SCHEMA = "R45B-FORWARD-CAPTURE-RECORD-R1"
ALLOWED = {"Date", "Time", "HomeTeam", "AwayTeam", "HS", "HST", "AS", "AST"}
FORBIDDEN = {"FTHG", "FTAG", "FTR", "HTHG", "HTAG", "HTR"}


def parse_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timezone_required")
    return dt.astimezone(timezone.utc)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_match_dt(date_text: str, time_text: str) -> datetime:
    t = (time_text or "00:00").strip() or "00:00"
    return datetime.strptime(f"{date_text.strip()} {t}", "%d/%m/%Y %H:%M")


def as_float(value: Any, field: str) -> float:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"missing:{field}")
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f"non_numeric:{field}:{text}") from exc


def mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 6)


def load_prereg(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if obj.get("schema_version") != PREREG_SCHEMA:
        raise ValueError("prereg_schema_mismatch")
    scope = obj.get("scope") or {}
    if scope.get("metric_semantics") != "SHOTS_SOT_PROXY":
        raise ValueError("metric_semantics_mismatch")
    if int(scope.get("lookback_match_count") or 0) != 10:
        raise ValueError("lookback_not_10")
    if scope.get("record_granularity") != "one process_capability record per target team":
        raise ValueError("record_granularity_mismatch")
    if set(obj.get("required_contract_payload_fields") or []) != {"team", "metric_semantics", "strict_prior_history_cutoff", "features"}:
        raise ValueError("contract_payload_field_mismatch")
    if set(obj.get("allowed_source_columns") or []) != ALLOWED:
        raise ValueError("allowed_column_contract_mismatch")
    if set(obj.get("forbidden_source_columns") or []) != FORBIDDEN:
        raise ValueError("forbidden_column_contract_mismatch")
    return obj


def collect_team_rows(csv_path: Path, source_team: str, freeze: datetime) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        header = set(reader.fieldnames or [])
        missing = ALLOWED - header
        if missing:
            raise ValueError(f"missing_required_columns:{sorted(missing)}")
        for raw in reader:
            # Immediately project only the preregistered columns. No score/result
            # field is referenced by name or copied into the process record.
            row = {k: raw.get(k) for k in ALLOWED}
            home = str(row["HomeTeam"] or "").strip()
            away = str(row["AwayTeam"] or "").strip()
            if source_team not in {home, away}:
                continue
            match_dt = parse_match_dt(str(row["Date"] or ""), str(row["Time"] or ""))
            if match_dt.date() >= freeze.date():
                continue
            if home == source_team:
                sf = as_float(row["HS"], "HS")
                sotf = as_float(row["HST"], "HST")
                sa = as_float(row["AS"], "AS")
                sota = as_float(row["AST"], "AST")
            else:
                sf = as_float(row["AS"], "AS")
                sotf = as_float(row["AST"], "AST")
                sa = as_float(row["HS"], "HS")
                sota = as_float(row["HST"], "HST")
            rows.append({
                "match_dt": match_dt,
                "date": str(row["Date"] or "").strip(),
                "time": str(row["Time"] or "").strip(),
                "home_team": home,
                "away_team": away,
                "shots_for": sf,
                "shots_on_target_for": sotf,
                "shots_against": sa,
                "shots_on_target_against": sota,
            })
    rows.sort(key=lambda x: x["match_dt"])
    if len(rows) < 10:
        raise ValueError(f"insufficient_eligible_matches:{source_team}:{len(rows)}")
    return rows[-10:]


def extract_features(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "lookback_match_count": len(rows),
        "shots_for_mean": mean([x["shots_for"] for x in rows]),
        "shots_on_target_for_mean": mean([x["shots_on_target_for"] for x in rows]),
        "shots_against_mean": mean([x["shots_against"] for x in rows]),
        "shots_on_target_against_mean": mean([x["shots_on_target_against"] for x in rows]),
        "lookback_first_match_date": rows[0]["date"],
        "lookback_last_match_date": rows[-1]["date"],
    }


def payload_hash(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def build_record(args: argparse.Namespace) -> dict[str, Any]:
    prereg = load_prereg(args.prereg)
    freeze = parse_utc(args.freeze_at_utc)
    kickoff = parse_utc(args.kickoff_at_utc)
    if not freeze < kickoff:
        raise ValueError("freeze_must_precede_kickoff")
    if args.team not in {args.home_team, args.away_team}:
        raise ValueError("team_not_in_fixture")
    source_team = str((prereg.get("team_aliases") or {}).get(args.team) or "").strip()
    if not source_team:
        raise ValueError("missing_team_alias")

    rows = collect_team_rows(args.csv, source_team, freeze)
    cutoff = freeze - timedelta(seconds=1)
    payload = {
        "team": args.team,
        "metric_semantics": "SHOTS_SOT_PROXY",
        "strict_prior_history_cutoff": iso_z(cutoff),
        "features": extract_features(rows),
        "target_match_excluded": True,
        "extractor_version": EXTRACTOR_VERSION,
        "preregistration_schema": PREREG_SCHEMA,
        "source_team_name": source_team,
        "source_dataset": str(args.csv).replace("\\", "/"),
        "allowed_source_columns": sorted(ALLOWED),
        "forbidden_source_columns_used": [],
        "target_result_labels_used": 0,
    }
    return {
        "schema_version": RECORD_SCHEMA,
        "evidence_id": args.evidence_id,
        "competition_id": args.competition_id,
        "fixture_key": args.fixture_key,
        "home_team": args.home_team,
        "away_team": args.away_team,
        "kickoff_at_utc": args.kickoff_at_utc,
        "freeze_at_utc": args.freeze_at_utc,
        "evidence_type": "process_capability",
        "source_name": args.source_name,
        "source_url": args.source_url,
        "source_domain": args.source_domain,
        "source_tier": "TIER_1_REPOSITORY_SNAPSHOT",
        "source_published_at_utc": "",
        "collector_first_observed_at_utc": args.freeze_at_utc,
        "accessed_at_utc": args.freeze_at_utc,
        "payload_sha256": payload_hash(payload),
        "provenance_class": "PROSPECTIVE_QUERY_TIME",
        "payload": payload,
    }


def self_test() -> None:
    assert ALLOWED.isdisjoint(FORBIDDEN)
    assert "FTHG" not in ALLOWED and "FTR" not in ALLOWED
    assert mean([1.0, 2.0, 3.0]) == 2.0
    freeze = parse_utc("2026-08-13T08:56:29Z")
    assert iso_z(freeze - timedelta(seconds=1)) == "2026-08-13T08:56:28Z"
    print("R45B_PROCESS_PROXY_EXTRACTOR_SELF_TEST_PASS")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--csv", type=Path)
    p.add_argument("--prereg", type=Path)
    p.add_argument("--output", type=Path)
    p.add_argument("--competition-id")
    p.add_argument("--fixture-key")
    p.add_argument("--home-team")
    p.add_argument("--away-team")
    p.add_argument("--team")
    p.add_argument("--kickoff-at-utc")
    p.add_argument("--freeze-at-utc")
    p.add_argument("--evidence-id")
    p.add_argument("--source-name", default="Repository processed ESP_LaLiga 2025-26 historical match-stat snapshot")
    p.add_argument("--source-url", default="https://github.com/FASHI188/FASHI188-football-analysis/blob/main/football-data/processed/ESP_LaLiga/2025-26.csv")
    p.add_argument("--source-domain", default="github.com")
    args = p.parse_args()
    if args.self_test:
        self_test()
        return 0
    required = ["csv", "prereg", "output", "competition_id", "fixture_key", "home_team", "away_team", "team", "kickoff_at_utc", "freeze_at_utc", "evidence_id"]
    missing = [name for name in required if getattr(args, name) in (None, "")]
    if missing:
        p.error("missing required arguments: " + ",".join(missing))
    record = build_record(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
