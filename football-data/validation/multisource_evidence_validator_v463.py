#!/usr/bin/env python3
"""Validate multi-source lineup and market evidence for the football project.

This validator does not fetch or invent data. It consumes normalized JSONL evidence
written by source-specific collectors and decides whether a record is suitable for:
  * historical probable-lineup validation,
  * historical synchronized market benchmarking,
  * KL market-coordination research,
  * circularity-safe LOMO value validation.

The central rule is source independence: two aggregators carrying the same underlying
bookmaker quote are not two independent market sources, and copied lineup records do
not become independent merely because they appear on two websites.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "multisource_evidence_v463.json"
LINEUP_ROOT = ROOT / "evidence" / "lineups"
MARKET_ROOT = ROOT / "evidence" / "markets"
REPORT_ROOT = ROOT / "validation" / "reports" / "multisource_evidence_v463"
MANIFEST = ROOT / "manifests" / "multisource_evidence_v463_status.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL {path}:{n}: {exc}") from exc
        if not isinstance(item, dict):
            raise ValueError(f"JSONL row must be object {path}:{n}")
        rows.append(item)
    return rows


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp must be a non-empty ISO8601 string")
    text = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _lineup_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("competition_id") or ""),
        str(row.get("season") or ""),
        str(row.get("kickoff_utc") or ""),
        str(row.get("team") or ""),
    )


def validate_lineups(rows: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    invalid: list[dict[str, Any]] = []
    for row in rows:
        starters = row.get("starters")
        try:
            _parse_time(row.get("kickoff_utc"))
        except Exception as exc:
            invalid.append({"row": row, "reason": f"invalid kickoff: {exc}"})
            continue
        if not isinstance(starters, list) or len(starters) != 11 or len({str(x) for x in starters}) != 11:
            invalid.append({"row": row, "reason": "lineup must contain exactly 11 unique starters"})
            continue
        if not row.get("source_group") or not (row.get("source_url") or row.get("provider_record_id")):
            invalid.append({"row": row, "reason": "missing source provenance"})
            continue
        grouped[_lineup_key(row)].append(row)

    verified = 0
    single_source = 0
    conflicted = 0
    details = []
    for key, items in sorted(grouped.items()):
        source_groups = {str(item["source_group"]) for item in items}
        starter_sets = {tuple(sorted(map(str, item["starters"]))) for item in items}
        official_present = "official_competition_or_club" in source_groups
        if len(starter_sets) > 1:
            status = "CONFLICT"
            conflicted += 1
        elif len(source_groups) >= 2 or (official_present and len(items) >= 2):
            status = "VERIFIED"
            verified += 1
        else:
            status = "SINGLE_SOURCE_WARNING"
            single_source += 1
        details.append({
            "key": key,
            "status": status,
            "source_groups": sorted(source_groups),
            "record_count": len(items),
        })

    return {
        "rows": len(rows),
        "valid_rows": sum(len(v) for v in grouped.values()),
        "invalid_rows": len(invalid),
        "verified_lineup_labels": verified,
        "single_source_lineup_labels": single_source,
        "conflicted_lineup_labels": conflicted,
        "a_grade_route_eligible": verified >= int(config.get("minimum_verified_lineup_labels", 200)),
        "details": details,
        "invalid": invalid,
    }


def _market_key(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(row.get("competition_id") or ""),
        str(row.get("fixture_key") or ""),
        str(row.get("freeze_time_utc") or ""),
        str(row.get("bookmaker_group") or row.get("bookmaker") or ""),
        str(row.get("provider_group") or row.get("source_group") or ""),
    )


def _market_complete(row: dict[str, Any]) -> bool:
    one = row.get("one_x_two")
    ah = row.get("asian_handicap")
    ou = row.get("over_under") or row.get("total_goals")
    if not isinstance(one, dict) or not all(k in one for k in ("home", "draw", "away")):
        return False
    if not isinstance(ah, dict) or not all(k in ah for k in ("line", "home", "away")):
        return False
    if not isinstance(ou, dict) or not all(k in ou for k in ("line", "over", "under")):
        return False
    return True


def validate_markets(rows: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    valid: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    for row in rows:
        try:
            freeze = _parse_time(row.get("freeze_time_utc"))
            observed = _parse_time(row.get("observed_at_utc"))
        except Exception as exc:
            invalid.append({"row": row, "reason": f"invalid timestamp: {exc}"})
            continue
        if observed > freeze:
            invalid.append({"row": row, "reason": "market observed after freeze"})
            continue
        if not _market_complete(row):
            invalid.append({"row": row, "reason": "incomplete 1X2/AH/OU surface"})
            continue
        if not row.get("provider_group") or not (row.get("bookmaker_group") or row.get("bookmaker")):
            invalid.append({"row": row, "reason": "missing provider/bookmaker provenance"})
            continue
        source_times = row.get("market_observed_at_utc") or {}
        if isinstance(source_times, dict) and source_times:
            try:
                times = [_parse_time(v) for v in source_times.values()]
            except Exception as exc:
                invalid.append({"row": row, "reason": f"invalid market timestamps: {exc}"})
                continue
            skew = (max(times) - min(times)).total_seconds()
            if skew > int(config.get("max_market_skew_seconds", 900)):
                invalid.append({"row": row, "reason": f"market skew {skew:.0f}s exceeds limit"})
                continue
        valid.append(row)

    by_fixture: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in valid:
        by_fixture[(str(row.get("competition_id")), str(row.get("fixture_key")), str(row.get("freeze_time_utc")))].append(row)

    crosschecked = 0
    single_provider = 0
    duplicate_economic_sources = 0
    details = []
    for key, items in sorted(by_fixture.items()):
        providers = {str(item.get("provider_group")) for item in items}
        bookmaker_groups = {str(item.get("bookmaker_group") or item.get("bookmaker")) for item in items}
        if len(items) > len(bookmaker_groups):
            duplicate_economic_sources += len(items) - len(bookmaker_groups)
        if len(providers) >= 2:
            status = "CROSSCHECKED"
            crosschecked += 1
        else:
            status = "SINGLE_PROVIDER_WARNING"
            single_provider += 1
        details.append({
            "key": key,
            "status": status,
            "provider_groups": sorted(providers),
            "bookmaker_groups": sorted(bookmaker_groups),
            "record_count": len(items),
        })

    return {
        "rows": len(rows),
        "valid_synchronized_snapshots": len(valid),
        "invalid_rows": len(invalid),
        "crosschecked_fixture_freezes": crosschecked,
        "single_provider_fixture_freezes": single_provider,
        "duplicate_economic_source_records": duplicate_economic_sources,
        "a_grade_market_evidence_available": crosschecked >= int(config.get("minimum_crosschecked_market_freezes", 200)),
        "details": details,
        "invalid": invalid,
    }


def run() -> dict[str, Any]:
    source_config = _load_json(CONFIG)
    policy = source_config["verification_policy"]
    thresholds = {
        "minimum_verified_lineup_labels": 200,
        "minimum_crosschecked_market_freezes": 200,
        "max_market_skew_seconds": int(policy["market_snapshot"]["max_market_skew_seconds"]),
    }
    lineup_rows: list[dict[str, Any]] = []
    market_rows: list[dict[str, Any]] = []
    for path in sorted(LINEUP_ROOT.glob("**/*.jsonl")):
        lineup_rows.extend(_read_jsonl(path))
    for path in sorted(MARKET_ROOT.glob("**/*.jsonl")):
        market_rows.extend(_read_jsonl(path))

    lineup = validate_lineups(lineup_rows, thresholds)
    market = validate_markets(market_rows, thresholds)
    result = {
        "schema_version": "V4.6.3",
        "generated_at_utc": _now(),
        "status": "EVIDENCE_VALIDATED" if lineup["rows"] or market["rows"] else "PIPELINE_CONFIGURED_DATA_BACKFILL_REQUIRED",
        "lineup": lineup,
        "market": market,
        "promotion_note": "No A-grade, KL, or LOMO promotion is automatic. Missing independent evidence remains fail-closed.",
    }
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    (REPORT_ROOT / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    result = run()
    if args.print_summary:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
