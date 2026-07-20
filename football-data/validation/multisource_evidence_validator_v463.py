#!/usr/bin/env python3
"""Validate multi-source lineup and market evidence for the football project.

V4.7.5 hardening:
- observed starting-XI labels are separated from point-in-time pre-match evidence;
- date-only/surrogate public lineup records can train future probable-XI models but
  can never be counted as PIT injury/availability evidence;
- reports are compact summaries rather than million-line record dumps;
- no A-grade, KL, LOMO or formal-weight promotion is automatic.
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
SAMPLE_LIMIT = 200
INVALID_SAMPLE_LIMIT = 50


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for n, line in enumerate(handle, 1):
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


def _pit_eligible_lineup_record(row: dict[str, Any]) -> bool:
    if row.get("pit_eligible") is not True:
        return False
    observed = row.get("source_observed_at_utc")
    kickoff = row.get("kickoff_utc")
    if not observed or not kickoff:
        return False
    try:
        return _parse_time(observed) <= _parse_time(kickoff)
    except Exception:
        return False


def validate_lineups(rows: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    invalid_samples: list[dict[str, Any]] = []
    invalid_count = 0

    for row in rows:
        starters = row.get("starters")
        try:
            _parse_time(row.get("kickoff_utc"))
        except Exception as exc:
            invalid_count += 1
            if len(invalid_samples) < INVALID_SAMPLE_LIMIT:
                invalid_samples.append({"reason": f"invalid kickoff: {exc}", "competition_id": row.get("competition_id")})
            continue
        if not isinstance(starters, list) or len(starters) != 11 or len({str(x) for x in starters}) != 11:
            invalid_count += 1
            if len(invalid_samples) < INVALID_SAMPLE_LIMIT:
                invalid_samples.append({"reason": "lineup must contain exactly 11 unique starters", "competition_id": row.get("competition_id")})
            continue
        if not row.get("source_group") or not (row.get("source_url") or row.get("provider_record_id")):
            invalid_count += 1
            if len(invalid_samples) < INVALID_SAMPLE_LIMIT:
                invalid_samples.append({"reason": "missing source provenance", "competition_id": row.get("competition_id")})
            continue
        grouped[_lineup_key(row)].append(row)

    verified_observed = 0
    pit_verified = 0
    single_source = 0
    conflicted = 0
    detail_samples: list[dict[str, Any]] = []
    per_competition: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for key, items in sorted(grouped.items()):
        cid = key[0]
        source_groups = {str(item["source_group"]) for item in items}
        starter_sets = {tuple(sorted(map(str, item["starters"]))) for item in items}
        official_present = "official_competition_or_club" in source_groups
        any_pit = any(_pit_eligible_lineup_record(item) for item in items)

        if len(starter_sets) > 1:
            status = "CONFLICT"
            conflicted += 1
            per_competition[cid]["conflicted"] += 1
        elif len(source_groups) >= 2 or (official_present and len(items) >= 2):
            status = "VERIFIED_OBSERVED_LABEL"
            verified_observed += 1
            per_competition[cid]["verified_observed"] += 1
            if any_pit:
                pit_verified += 1
                per_competition[cid]["pit_verified"] += 1
        else:
            status = "SINGLE_SOURCE_OBSERVED_LABEL"
            single_source += 1
            per_competition[cid]["single_source"] += 1

        if len(detail_samples) < SAMPLE_LIMIT:
            detail_samples.append({
                "key": key,
                "status": status,
                "source_groups": sorted(source_groups),
                "record_count": len(items),
                "pit_eligible_record_present": any_pit,
            })

    valid_rows = sum(len(v) for v in grouped.values())
    return {
        "rows": len(rows),
        "valid_rows": valid_rows,
        "invalid_rows": invalid_count,
        "verified_observed_lineup_labels": verified_observed,
        "pit_verified_lineup_labels": pit_verified,
        "single_source_lineup_labels": single_source,
        "conflicted_lineup_labels": conflicted,
        "probable_lineup_training_route_available": verified_observed + single_source > 0,
        "a_grade_route_eligible": pit_verified >= int(config.get("minimum_verified_lineup_labels", 200)),
        "per_competition": {cid: dict(counts) for cid, counts in sorted(per_competition.items())},
        "detail_samples": detail_samples,
        "invalid_samples": invalid_samples,
        "report_compaction": {"detail_sample_limit": SAMPLE_LIMIT, "invalid_sample_limit": INVALID_SAMPLE_LIMIT},
    }


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
    invalid_count = 0
    invalid_samples: list[dict[str, Any]] = []

    for row in rows:
        reason = None
        try:
            freeze = _parse_time(row.get("freeze_time_utc"))
            observed = _parse_time(row.get("observed_at_utc"))
            if observed > freeze:
                reason = "market observed after freeze"
        except Exception as exc:
            reason = f"invalid timestamp: {exc}"
        if reason is None and not _market_complete(row):
            reason = "incomplete 1X2/AH/OU surface"
        if reason is None and (not row.get("provider_group") or not (row.get("bookmaker_group") or row.get("bookmaker"))):
            reason = "missing provider/bookmaker provenance"
        source_times = row.get("market_observed_at_utc") or {}
        if reason is None and isinstance(source_times, dict) and source_times:
            try:
                times = [_parse_time(v) for v in source_times.values()]
                skew = (max(times) - min(times)).total_seconds()
                if skew > int(config.get("max_market_skew_seconds", 900)):
                    reason = f"market skew {skew:.0f}s exceeds limit"
            except Exception as exc:
                reason = f"invalid market timestamps: {exc}"
        if reason is not None:
            invalid_count += 1
            if len(invalid_samples) < INVALID_SAMPLE_LIMIT:
                invalid_samples.append({"competition_id": row.get("competition_id"), "fixture_key": row.get("fixture_key"), "reason": reason})
            continue
        valid.append(row)

    by_fixture: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in valid:
        by_fixture[(str(row.get("competition_id")), str(row.get("fixture_key")), str(row.get("freeze_time_utc")))].append(row)

    crosschecked = 0
    single_provider = 0
    duplicate_economic_sources = 0
    detail_samples: list[dict[str, Any]] = []
    per_competition: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for key, items in sorted(by_fixture.items()):
        cid = key[0]
        providers = {str(item.get("provider_group")) for item in items}
        bookmaker_groups = {str(item.get("bookmaker_group") or item.get("bookmaker")) for item in items}
        if len(items) > len(bookmaker_groups):
            duplicate_economic_sources += len(items) - len(bookmaker_groups)
        if len(providers) >= 2:
            status = "CROSSCHECKED"
            crosschecked += 1
            per_competition[cid]["crosschecked_freezes"] += 1
        else:
            status = "SINGLE_PROVIDER_WARNING"
            single_provider += 1
            per_competition[cid]["single_provider_freezes"] += 1
        if len(detail_samples) < SAMPLE_LIMIT:
            detail_samples.append({
                "key": key,
                "status": status,
                "provider_groups": sorted(providers),
                "bookmaker_groups": sorted(bookmaker_groups),
                "record_count": len(items),
            })

    return {
        "rows": len(rows),
        "valid_synchronized_snapshots": len(valid),
        "invalid_rows": invalid_count,
        "crosschecked_fixture_freezes": crosschecked,
        "single_provider_fixture_freezes": single_provider,
        "duplicate_economic_source_records": duplicate_economic_sources,
        "a_grade_market_evidence_available": crosschecked >= int(config.get("minimum_crosschecked_market_freezes", 200)),
        "per_competition": {cid: dict(counts) for cid, counts in sorted(per_competition.items())},
        "detail_samples": detail_samples,
        "invalid_samples": invalid_samples,
        "report_compaction": {"detail_sample_limit": SAMPLE_LIMIT, "invalid_sample_limit": INVALID_SAMPLE_LIMIT},
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
        "schema_version": "V4.7.5-compact-pit-separated",
        "generated_at_utc": _now(),
        "status": "EVIDENCE_VALIDATED" if lineup["rows"] or market["rows"] else "PIPELINE_CONFIGURED_DATA_BACKFILL_REQUIRED",
        "lineup": lineup,
        "market": market,
        "promotion_note": "Observed XI labels may support probable-lineup training. Only timestamp-proven PIT records can support A-grade pre-match availability evidence. No A-grade, KL or LOMO promotion is automatic.",
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
        print(json.dumps({
            "status": result["status"],
            "lineup": {k: result["lineup"][k] for k in (
                "rows", "valid_rows", "verified_observed_lineup_labels", "pit_verified_lineup_labels",
                "single_source_lineup_labels", "conflicted_lineup_labels", "a_grade_route_eligible"
            )},
            "market": {k: result["market"][k] for k in (
                "rows", "valid_synchronized_snapshots", "crosschecked_fixture_freezes", "a_grade_market_evidence_available"
            )},
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
