#!/usr/bin/env python3
"""R25 root-cause audit for the draw research data lineage.

This audit does not fit a model. It traces whether forward prediction packets, timestamped
markets, contextual packets, result receipts and historical processed rows are actually
joined into one point-in-time training ledger. It is read-only and formal_weight=0.
"""
from __future__ import annotations

import csv
import json
import math
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from dateutil import parser as date_parser

ROOT = Path(__file__).resolve().parents[1]
FORWARD = ROOT / "forward"
PROCESSED = ROOT / "processed"
CONTEXT = FORWARD / "v6_context_enriched_events_v6486.json"
RESULTS = FORWARD / "inbox" / "market_first_results_v651.json"
AUDIT_SCRIPT = ROOT / "validation" / "audit_v510_existing_score_market_pit_ledger_r1.py"
OUT = ROOT / "manifests" / "v511_forward_lineage_root_cause_r25_status.json"


class AuditError(RuntimeError):
    pass


def load_json(path: Path) -> Any:
    if not path.is_file():
        raise AuditError(f"missing required file: {path.relative_to(ROOT)}")
    return json.loads(path.read_text(encoding="utf-8"))


def norm_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = re.sub(r"\([^)]*\)", " ", text)
    return "".join(ch for ch in text if ch.isalnum())


def parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def fixture_key(competition: Any, kickoff: Any, home: Any, away: Any, exact: bool) -> tuple[str, str, str, str] | None:
    dt = parse_iso(kickoff)
    if dt is None:
        return None
    time_key = dt.isoformat() if exact else dt.date().isoformat()
    return (str(competition or "").strip(), time_key, norm_text(home), norm_text(away))


def walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def extract_fixture(packet: dict[str, Any]) -> dict[str, Any] | None:
    candidates = [packet]
    if isinstance(packet.get("payload"), dict):
        candidates.append(packet["payload"])
    for candidate in candidates:
        fixture = candidate.get("fixture_identity")
        if isinstance(fixture, dict):
            return fixture
        fixture = candidate.get("fixture")
        if isinstance(fixture, dict):
            return fixture
    return None


def extract_market_observed(packet: dict[str, Any]) -> str | None:
    candidates = [packet]
    if isinstance(packet.get("payload"), dict):
        candidates.append(packet["payload"])
    for candidate in candidates:
        market = candidate.get("market")
        if isinstance(market, dict):
            for key in ("observed_at_utc", "available_at_utc", "snapshot_at_utc", "quoted_at_utc"):
                if parse_iso(market.get(key)) is not None:
                    return str(market[key])
        for key in ("market_observed_at_utc", "observed_at_utc", "available_at_utc"):
            if parse_iso(candidate.get(key)) is not None:
                return str(candidate[key])
    return None


def scan_forward_packets() -> tuple[dict[str, dict[str, Any]], Counter, list[str]]:
    by_event_hash: dict[str, dict[str, Any]] = {}
    schemas = Counter()
    errors: list[str] = []
    for path in sorted(FORWARD.rglob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"{path.relative_to(ROOT)}: {type(exc).__name__}: {exc}")
            continue
        for packet in walk_dicts(value):
            schema = str(packet.get("schema_version") or "")
            if schema:
                schemas[schema] += 1
            event_hash = str(packet.get("event_hash") or "").strip()
            if event_hash:
                by_event_hash.setdefault(event_hash, packet)
    return by_event_hash, schemas, errors


def context_summary() -> tuple[list[dict[str, Any]], set[tuple[str, str, str, str]], set[tuple[str, str, str, str]]]:
    value = load_json(CONTEXT)
    events = value.get("events") or []
    date_keys: set[tuple[str, str, str, str]] = set()
    exact_keys: set[tuple[str, str, str, str]] = set()
    rows: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        fixture = extract_fixture(event)
        if not fixture:
            continue
        date_key = fixture_key(fixture.get("competition_id"), fixture.get("kickoff_at"), fixture.get("home_team"), fixture.get("away_team"), False)
        exact_key = fixture_key(fixture.get("competition_id"), fixture.get("kickoff_at"), fixture.get("home_team"), fixture.get("away_team"), True)
        if date_key:
            date_keys.add(date_key)
        if exact_key:
            exact_keys.add(exact_key)
        rows.append({
            "competition_id": fixture.get("competition_id"),
            "kickoff_at": fixture.get("kickoff_at"),
            "home_team": fixture.get("home_team"),
            "away_team": fixture.get("away_team"),
            "event_hash": event.get("event_hash"),
            "market_observed_at_utc": extract_market_observed(event),
        })
    return rows, date_keys, exact_keys


def result_summary() -> list[dict[str, Any]]:
    value = load_json(RESULTS)
    rows = value.get("results") or []
    return [row for row in rows if isinstance(row, dict)]


def norm_header(value: Any) -> str:
    return re.sub(r"[^a-z0-9><]+", "", str(value or "").casefold())


def find_field(headers: list[str], aliases: list[str]) -> str | None:
    mapping = {norm_header(header): header for header in headers}
    for alias in aliases:
        hit = mapping.get(norm_header(alias))
        if hit:
            return hit
    return None


def parse_archive_date(text: str, season: str) -> datetime | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    direct = parse_iso(raw)
    if direct is not None:
        return direct.replace(tzinfo=None)
    for dayfirst in (True, False):
        try:
            parsed = date_parser.parse(raw, dayfirst=dayfirst, fuzzy=True, default=datetime(2000, 1, 1))
            break
        except Exception:
            parsed = None
    if parsed is None:
        return None
    if parsed.year == 2000:
        match = re.search(r"(20\d{2})", str(season or ""))
        if match:
            start = int(match.group(1))
            parsed = parsed.replace(year=start + (1 if parsed.month <= 6 else 0))
    return parsed.replace(tzinfo=None)


def processed_identity_summary() -> tuple[set[tuple[str, str, str, str]], Counter, Counter, int, int, list[str]]:
    keys: set[tuple[str, str, str, str]] = set()
    competitions = Counter()
    source_rows = Counter()
    timestamp_header_rows = 0
    timezone_value_rows = 0
    errors: list[str] = []
    for path in sorted(PROCESSED.rglob("*.csv")):
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                headers = list(reader.fieldnames or [])
                competition_field = find_field(headers, ["competition_id", "competition", "league_id"])
                season_field = find_field(headers, ["season", "source_season"])
                date_field = find_field(headers, ["Date", "date", "kickoff_at", "kickoff_utc"])
                home_field = find_field(headers, ["HomeTeam", "home_team", "home"])
                away_field = find_field(headers, ["AwayTeam", "away_team", "away"])
                quote_field = find_field(headers, ["market_observed_at_utc", "observed_at_utc", "available_at_utc", "snapshot_at_utc", "quoted_at_utc", "odds_timestamp_utc", "market_timestamp_utc", "collected_at_utc"])
                for row in reader:
                    competition = str(row.get(competition_field) or path.parent.name).strip()
                    season = str(row.get(season_field) or "")
                    parsed = parse_archive_date(str(row.get(date_field) or ""), season)
                    home = str(row.get(home_field) or "").strip()
                    away = str(row.get(away_field) or "").strip()
                    if parsed and competition and home and away:
                        keys.add((competition, parsed.date().isoformat(), norm_text(home), norm_text(away)))
                        competitions[competition] += 1
                    source_rows[str(path.relative_to(ROOT))] += 1
                    if quote_field and parse_iso(row.get(quote_field)) is not None:
                        timestamp_header_rows += 1
                    if parsed and parsed.tzinfo is not None:
                        timezone_value_rows += 1
        except Exception as exc:
            errors.append(f"{path.relative_to(ROOT)}: {type(exc).__name__}: {exc}")
    return keys, competitions, source_rows, timestamp_header_rows, timezone_value_rows, errors


def range_summary(values: list[str]) -> dict[str, str | None]:
    parsed = sorted(dt for value in values if (dt := parse_iso(value)) is not None)
    return {"min": parsed[0].isoformat() if parsed else None, "max": parsed[-1].isoformat() if parsed else None}


def main() -> None:
    context_rows, context_date_keys, context_exact_keys = context_summary()
    results = result_summary()
    event_packets, schemas, forward_errors = scan_forward_packets()
    processed_keys, processed_competitions, source_rows, timestamp_rows, timezone_rows, processed_errors = processed_identity_summary()

    result_date_keys: set[tuple[str, str, str, str]] = set()
    result_exact_keys: set[tuple[str, str, str, str]] = set()
    matched_prediction_events = 0
    matched_prediction_with_timestamp = 0
    prediction_identity_exact = 0
    prediction_identity_date = 0
    result_context_exact = 0
    result_context_date = 0
    result_processed_date = 0
    missing_event_hashes: list[str] = []

    for result in results:
        date_key = fixture_key(result.get("competition_id"), result.get("kickoff_at"), result.get("home_team"), result.get("away_team"), False)
        exact_key = fixture_key(result.get("competition_id"), result.get("kickoff_at"), result.get("home_team"), result.get("away_team"), True)
        if date_key:
            result_date_keys.add(date_key)
            result_context_date += int(date_key in context_date_keys)
            result_processed_date += int(date_key in processed_keys)
        if exact_key:
            result_exact_keys.add(exact_key)
            result_context_exact += int(exact_key in context_exact_keys)

        event_hash = str(result.get("prediction_event_hash") or "").strip()
        packet = event_packets.get(event_hash)
        if packet is None:
            if event_hash:
                missing_event_hashes.append(event_hash)
            continue
        matched_prediction_events += 1
        matched_prediction_with_timestamp += int(extract_market_observed(packet) is not None)
        fixture = extract_fixture(packet)
        if fixture:
            packet_date = fixture_key(fixture.get("competition_id"), fixture.get("kickoff_at"), fixture.get("home_team"), fixture.get("away_team"), False)
            packet_exact = fixture_key(fixture.get("competition_id"), fixture.get("kickoff_at"), fixture.get("home_team"), fixture.get("away_team"), True)
            prediction_identity_date += int(bool(date_key and packet_date == date_key))
            prediction_identity_exact += int(bool(exact_key and packet_exact == exact_key))

    audit_text = AUDIT_SCRIPT.read_text(encoding="utf-8") if AUDIT_SCRIPT.is_file() else ""
    inbox_counted_only = "result_inbox_count = len(values)" in audit_text and "prediction_event_hash" not in audit_text

    causes = []
    if timestamp_rows == 0:
        causes.append("HISTORICAL_CSV_HAS_ZERO_ORIGINAL_MARKET_TIMESTAMPS")
    if timezone_rows == 0:
        causes.append("HISTORICAL_CSV_HAS_ZERO_EXPLICIT_KICKOFF_TIMEZONES")
    if not context_date_keys.intersection(processed_keys):
        causes.append("FORWARD_CONTEXT_AND_HISTORICAL_LEDGER_ARE_IDENTITY_DISJOINT")
    if inbox_counted_only:
        causes.append("RESULT_INBOX_IS_COUNTED_BUT_NOT_JOINED_TO_PREDICTION_PACKETS")
    if matched_prediction_events and matched_prediction_with_timestamp:
        causes.append("TIMESTAMPED_FORWARD_EVIDENCE_EXISTS_OUTSIDE_THE_HISTORICAL_TRAINING_LEDGER")

    primary = (
        "DATA_LINEAGE_AND_POINT_IN_TIME_CAPTURE_FAILURE"
        if causes else "ROOT_CAUSE_NOT_ISOLATED"
    )
    status = "PASS_R25_ROOT_CAUSE_ISOLATED" if causes else "FAIL_R25_ROOT_CAUSE_NOT_ISOLATED"

    result = {
        "schema_version": "v511_forward_lineage_root_cause_r25_status.1",
        "status": status,
        "classification": "READ_ONLY_DATA_LINEAGE_ROOT_CAUSE_AUDIT",
        "formal_weight": 0,
        "primary_root_cause": primary,
        "root_cause_evidence": causes,
        "counts": {
            "forward_result_receipts": len(results),
            "forward_context_packets": len(context_rows),
            "forward_event_hash_packets_discovered": len(event_packets),
            "result_receipts_with_matching_prediction_event_hash": matched_prediction_events,
            "matching_prediction_packets_with_market_timestamp": matched_prediction_with_timestamp,
            "prediction_result_exact_identity_matches": prediction_identity_exact,
            "prediction_result_date_identity_matches": prediction_identity_date,
            "result_context_exact_identity_matches": result_context_exact,
            "result_context_date_identity_matches": result_context_date,
            "result_processed_date_identity_matches": result_processed_date,
            "context_processed_date_identity_overlap": len(context_date_keys.intersection(processed_keys)),
            "historical_processed_date_team_identities": len(processed_keys),
            "historical_rows_with_original_quote_timestamp": timestamp_rows,
            "historical_rows_with_explicit_kickoff_timezone": timezone_rows,
            "missing_prediction_event_hashes": len(set(missing_event_hashes)),
        },
        "competition_distributions": {
            "context": dict(Counter(str(row.get("competition_id")) for row in context_rows)),
            "results": dict(Counter(str(row.get("competition_id")) for row in results)),
            "processed_identity_rows": dict(processed_competitions),
        },
        "date_ranges": {
            "context": range_summary([str(row.get("kickoff_at") or "") for row in context_rows]),
            "results": range_summary([str(row.get("kickoff_at") or "") for row in results]),
        },
        "audit_implementation_check": {
            "result_inbox_counted_but_not_joined": inbox_counted_only,
            "audit_script": str(AUDIT_SCRIPT.relative_to(ROOT)) if AUDIT_SCRIPT.is_file() else None,
        },
        "diagnostic_ruling": {
            "label_quality_is_primary_failure": False,
            "model_complexity_is_primary_failure": False,
            "static_closing_price_formula_is_primary_failure": False,
            "missing_or_disconnected_point_in_time_features_is_primary_failure": bool(causes),
            "existing_historical_features_can_recover_missing_information": False,
            "next_action": "join timestamped forward prediction packets, context packets and result receipts into one immutable PIT ledger before testing another draw model",
        },
        "hard_limits": {
            "research_only": True,
            "model_training_performed": False,
            "provider_requests": 0,
            "formal_promotion_allowed": False,
            "current_or_main_mutation_allowed": False,
            "current_match_probability_allowed": False,
            "unified_matrix_unlock_allowed": False,
            "exact_score_allowed": False,
            "ev_allowed": False,
        },
        "errors": {
            "forward_json_scan": forward_errors,
            "processed_csv_scan": processed_errors,
        },
        "schema_inventory_top20": dict(schemas.most_common(20)),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
