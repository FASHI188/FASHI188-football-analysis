#!/usr/bin/env python3
"""R26 immutable forward point-in-time linkage ledger.

Builds a deterministic, hash-chained ledger joining every market-first result receipt to
its original prediction event by prediction_event_hash, confirms exact fixture identity,
recovers freeze/market/context provenance from the packet and its containing JSON scopes,
and reports readiness for the user's fixed ten-draw screening gate.

Research-only. No model fitting, provider calls, probability generation, CURRENT changes,
score matrix, exact-score output, or EV calculation.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
FORWARD = ROOT / "forward"
DEFAULT_CONFIG = ROOT / "config" / "v511_forward_pit_linkage_r26.json"
DEFAULT_RESULTS = FORWARD / "inbox" / "market_first_results_v651.json"
DEFAULT_CONTEXT = FORWARD / "v6_context_enriched_events_v6486.json"
DEFAULT_STATUS = ROOT / "manifests" / "v511_forward_pit_linkage_r26_status.json"
DEFAULT_JSONL = ROOT / "manifests" / "v511_forward_pit_linkage_r26_ledger.jsonl"
DEFAULT_CSV = ROOT / "manifests" / "v511_forward_pit_linkage_r26_ledger.csv"
DEFAULT_GAPS = ROOT / "manifests" / "v511_forward_pit_linkage_r26_gaps.csv"


class LedgerError(RuntimeError):
    pass


def load_json(path: Path) -> Any:
    if not path.is_file():
        raise LedgerError(f"missing required file: {path.relative_to(ROOT)}")
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def norm_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = re.sub(r"\([^)]*\)", " ", text)
    return "".join(ch for ch in text if ch.isalnum())


def parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def iso_or_none(value: Any) -> str | None:
    parsed = parse_iso(value)
    return parsed.isoformat() if parsed is not None else None


def numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def valid_price(value: Any) -> bool:
    number = numeric(value)
    return number is not None and number > 1.0


def fixture_key(fixture: dict[str, Any] | None) -> tuple[str, str, str, str] | None:
    if not fixture:
        return None
    kickoff = parse_iso(fixture.get("kickoff_at") or fixture.get("kickoff_at_utc") or fixture.get("kickoff_utc"))
    competition = str(fixture.get("competition_id") or fixture.get("competition") or "").strip()
    home = fixture.get("home_team") or fixture.get("home")
    away = fixture.get("away_team") or fixture.get("away")
    if not (kickoff and competition and home and away):
        return None
    return (competition, kickoff.isoformat(), norm_text(home), norm_text(away))


def result_fixture(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "competition_id": result.get("competition_id"),
        "kickoff_at": result.get("kickoff_at"),
        "home_team": result.get("home_team"),
        "away_team": result.get("away_team"),
    }


def walk_nodes(value: Any, ancestors: tuple[dict[str, Any], ...] = (), path: tuple[str, ...] = ()) -> Iterator[tuple[dict[str, Any], tuple[dict[str, Any], ...], tuple[str, ...]]]:
    if isinstance(value, dict):
        yield value, ancestors, path
        next_ancestors = ancestors + (value,)
        for key, child in value.items():
            yield from walk_nodes(child, next_ancestors, path + (str(key),))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk_nodes(child, ancestors, path + (f"[{index}]",))


def walk_values(value: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = path + (str(key),)
            yield child_path, str(key), child
            yield from walk_values(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk_values(child, path + (f"[{index}]",))


def scope_candidates(packet: dict[str, Any], ancestors: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    scopes: list[dict[str, Any]] = [packet]
    scopes.extend(reversed(ancestors))
    seen: set[int] = set()
    unique: list[dict[str, Any]] = []
    for scope in scopes:
        marker = id(scope)
        if marker not in seen:
            seen.add(marker)
            unique.append(scope)
    return unique


def extract_fixture_from_scope(scope: dict[str, Any]) -> dict[str, Any] | None:
    direct_names = ("fixture_identity", "fixture", "match_identity", "game_identity")
    for candidate in (scope, scope.get("payload") if isinstance(scope.get("payload"), dict) else None):
        if not isinstance(candidate, dict):
            continue
        for name in direct_names:
            fixture = candidate.get(name)
            if isinstance(fixture, dict) and fixture_key(fixture):
                return fixture
        if fixture_key(candidate):
            return candidate
    for path, _, child in walk_values(scope):
        if len(path) > 5 or not isinstance(child, dict):
            continue
        if path[-1].casefold() in direct_names and fixture_key(child):
            return child
    return None


def extract_fixture(scopes: list[dict[str, Any]]) -> dict[str, Any] | None:
    for scope in scopes:
        fixture = extract_fixture_from_scope(scope)
        if fixture:
            return fixture
    return None


FREEZE_KEYS = (
    "decision_freeze_at_utc",
    "prediction_freeze_at_utc",
    "freeze_at_utc",
    "frozen_at_utc",
    "decision_frozen_at_utc",
    "as_of_utc",
)
MARKET_TIMESTAMP_KEYS = (
    "market_observed_at_utc",
    "market_available_at_utc",
    "market_snapshot_at_utc",
    "market_quoted_at_utc",
    "odds_timestamp_utc",
    "quoted_at_utc",
    "snapshot_at_utc",
    "collected_at_utc",
    "available_at_utc",
    "observed_at_utc",
)
CONTEXT_TIMESTAMP_KEYS = (
    "context_observed_at_utc",
    "source_observed_at_utc",
    "observed_at_utc",
    "available_at_utc",
    "article_published",
    "article_last_updated",
)


def find_timestamp(scopes: list[dict[str, Any]], mode: str, event_type: str = "") -> tuple[str | None, str | None]:
    keys = FREEZE_KEYS if mode == "freeze" else MARKET_TIMESTAMP_KEYS if mode == "market" else CONTEXT_TIMESTAMP_KEYS
    candidates: list[tuple[int, datetime, str]] = []
    market_words = ("market", "odds", "price", "quote", "snapshot", "h2h", "one_x_two", "asian", "handicap", "total")
    context_words = ("context", "lineup", "availability", "injur", "suspens", "task", "motivation", "source", "preview")
    for scope_index, scope in enumerate(scopes):
        for path, key, value in walk_values(scope):
            key_lower = key.casefold()
            if key_lower not in keys:
                continue
            parsed = parse_iso(value)
            if parsed is None:
                continue
            path_text = "/".join(path).casefold()
            if mode == "market" and key_lower in ("observed_at_utc", "available_at_utc", "snapshot_at_utc", "collected_at_utc"):
                if not any(word in path_text for word in market_words):
                    continue
            if mode == "context" and key_lower in ("observed_at_utc", "available_at_utc"):
                if not any(word in path_text for word in context_words):
                    continue
            key_priority = keys.index(key_lower) if key_lower in keys else len(keys)
            candidates.append((scope_index * 100 + key_priority, parsed, f"scope[{scope_index}]/{'/'.join(path)}"))
    if mode == "freeze" and "FROZEN" in event_type.upper():
        for scope_index, scope in enumerate(scopes):
            parsed = parse_iso(scope.get("event_timestamp_utc"))
            if parsed is not None:
                candidates.append((900 + scope_index, parsed, f"scope[{scope_index}]/event_timestamp_utc"))
    if not candidates:
        return None, None
    candidates.sort(key=lambda item: (item[0], item[1]))
    _, parsed, source = candidates[0]
    return parsed.isoformat(), source


def normalized_key_map(value: dict[str, Any]) -> dict[str, str]:
    return {re.sub(r"[^a-z0-9]+", "", str(key).casefold()): str(key) for key in value}


def find_market(scopes: list[dict[str, Any]], market_type: str) -> tuple[dict[str, float] | None, str | None]:
    candidates: list[tuple[int, dict[str, float], str]] = []
    for scope_index, scope in enumerate(scopes):
        for path, _, child in walk_values(scope):
            if not isinstance(child, dict) or len(path) > 8:
                continue
            mapping = normalized_key_map(child)
            path_text = "/".join(path).casefold()
            if market_type == "1x2":
                sets = [("home", "draw", "away"), ("homeodds", "drawodds", "awayodds"), ("h", "d", "a")]
                keyword_score = 0 if any(word in path_text for word in ("onextwo", "1x2", "h2h", "market", "odds")) else 10
                for names in sets:
                    if all(name in mapping for name in names):
                        values = [child[mapping[name]] for name in names]
                        if all(valid_price(value) for value in values):
                            candidates.append((scope_index * 100 + keyword_score + len(path), {
                                "home": float(values[0]), "draw": float(values[1]), "away": float(values[2])
                            }, f"scope[{scope_index}]/{'/'.join(path)}"))
                            break
            elif market_type == "asian":
                line_name = next((name for name in ("line", "handicap", "spread", "asianline") if name in mapping), None)
                home_name = next((name for name in ("home", "homeodds", "homeprice") if name in mapping), None)
                away_name = next((name for name in ("away", "awayodds", "awayprice") if name in mapping), None)
                if line_name and home_name and away_name and any(word in path_text for word in ("asian", "handicap", "spread", "ah")):
                    line = numeric(child[mapping[line_name]])
                    home = child[mapping[home_name]]
                    away = child[mapping[away_name]]
                    if line is not None and valid_price(home) and valid_price(away):
                        candidates.append((scope_index * 100 + len(path), {
                            "line": float(line), "home": float(home), "away": float(away)
                        }, f"scope[{scope_index}]/{'/'.join(path)}"))
            elif market_type == "totals":
                line_name = next((name for name in ("line", "total", "totalline", "overunderline") if name in mapping), None)
                over_name = next((name for name in ("over", "overodds", "overprice") if name in mapping), None)
                under_name = next((name for name in ("under", "underodds", "underprice") if name in mapping), None)
                if line_name and over_name and under_name and any(word in path_text for word in ("total", "overunder", "ou", "goals")):
                    line = numeric(child[mapping[line_name]])
                    over = child[mapping[over_name]]
                    under = child[mapping[under_name]]
                    if line is not None and valid_price(over) and valid_price(under):
                        candidates.append((scope_index * 100 + len(path), {
                            "line": float(line), "over": float(over), "under": float(under)
                        }, f"scope[{scope_index}]/{'/'.join(path)}"))
    if not candidates:
        return None, None
    candidates.sort(key=lambda item: item[0])
    _, market, source = candidates[0]
    return market, source


CONTEXT_KEYWORDS = (
    "predicted_xi", "starting_xi", "lineup", "availability", "injuries", "injury",
    "suspensions", "suspension", "task", "motivation", "standings", "aggregate",
    "two_leg", "competition_state", "role", "formation", "context_features",
)


def find_context_fields(scopes: list[dict[str, Any]]) -> list[str]:
    found: set[str] = set()
    for scope_index, scope in enumerate(scopes):
        for path, key, value in walk_values(scope):
            key_lower = key.casefold()
            if any(keyword in key_lower for keyword in CONTEXT_KEYWORDS) and value not in (None, "", [], {}):
                found.add(f"scope[{scope_index}]/{'/'.join(path)}")
    return sorted(found)


def extract_event_type(packet: dict[str, Any], ancestors: tuple[dict[str, Any], ...]) -> str:
    for scope in [packet, *reversed(ancestors)]:
        value = str(scope.get("event_type") or "").strip()
        if value:
            return value
    return ""


def extract_schema(packet: dict[str, Any], ancestors: tuple[dict[str, Any], ...]) -> str:
    for scope in [packet, *reversed(ancestors)]:
        value = str(scope.get("schema_version") or "").strip()
        if value:
            return value
    return ""


def scan_prediction_occurrences() -> tuple[dict[str, list[dict[str, Any]]], Counter, list[str]]:
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    schemas: Counter = Counter()
    errors: list[str] = []
    for path in sorted(FORWARD.rglob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"{path.relative_to(ROOT)}: {type(exc).__name__}: {exc}")
            continue
        for packet, ancestors, node_path in walk_nodes(value):
            schema = str(packet.get("schema_version") or "").strip()
            if schema:
                schemas[schema] += 1
            event_hash = str(packet.get("event_hash") or "").strip()
            if event_hash:
                index[event_hash].append({
                    "packet": packet,
                    "ancestors": ancestors,
                    "source_path": str(path.relative_to(ROOT)).replace("\\", "/"),
                    "node_path": "/".join(node_path),
                })
    return index, schemas, errors


def load_context_index(path: Path) -> tuple[dict[tuple[str, str, str, str], list[dict[str, Any]]], int]:
    value = load_json(path)
    events = value.get("events") if isinstance(value, dict) else None
    if not isinstance(events, list):
        raise LedgerError("context file does not contain an events list")
    index: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if not isinstance(event, dict):
            continue
        scopes = [event]
        payload = event.get("payload")
        if isinstance(payload, dict):
            scopes.append(payload)
        fixture = extract_fixture(scopes)
        key = fixture_key(fixture)
        if not key:
            continue
        observed_at, observed_source = find_timestamp(scopes, "context", str(event.get("event_type") or ""))
        index[key].append({
            "event_hash": event.get("event_hash"),
            "observed_at_utc": observed_at,
            "observed_at_source": observed_source,
            "context_fields": find_context_fields(scopes),
            "source_path": str(path.relative_to(ROOT)).replace("\\", "/"),
        })
    return index, len(events)


def occurrence_analysis(occurrence: dict[str, Any], expected_key: tuple[str, str, str, str] | None) -> dict[str, Any]:
    packet = occurrence["packet"]
    ancestors = occurrence["ancestors"]
    scopes = scope_candidates(packet, ancestors)
    fixture = extract_fixture(scopes)
    key = fixture_key(fixture)
    event_type = extract_event_type(packet, ancestors)
    schema = extract_schema(packet, ancestors)
    freeze_at, freeze_source = find_timestamp(scopes, "freeze", event_type)
    market_at, market_at_source = find_timestamp(scopes, "market", event_type)
    one_x_two, one_x_two_source = find_market(scopes, "1x2")
    asian, asian_source = find_market(scopes, "asian")
    totals, totals_source = find_market(scopes, "totals")
    context_fields = find_context_fields(scopes)
    score = 1000 if expected_key and key == expected_key else 0
    score += 100 if key else 0
    score += 40 if freeze_at else 0
    score += 40 if market_at else 0
    score += 30 if one_x_two else 0
    score += 10 if asian else 0
    score += 10 if totals else 0
    score += min(len(context_fields), 10)
    return {
        "selection_score": score,
        "fixture": fixture,
        "fixture_key": key,
        "event_type": event_type,
        "schema_version": schema,
        "freeze_at_utc": freeze_at,
        "freeze_timestamp_source": freeze_source,
        "market_observed_at_utc": market_at,
        "market_timestamp_source": market_at_source,
        "one_x_two": one_x_two,
        "one_x_two_source": one_x_two_source,
        "asian_handicap": asian,
        "asian_handicap_source": asian_source,
        "over_under": totals,
        "over_under_source": totals_source,
        "embedded_context_fields": context_fields,
        "source_path": occurrence["source_path"],
        "node_path": occurrence["node_path"],
    }


def choose_occurrence(occurrences: list[dict[str, Any]], expected_key: tuple[str, str, str, str] | None) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    analyses = [occurrence_analysis(occurrence, expected_key) for occurrence in occurrences]
    analyses.sort(key=lambda row: (-int(row["selection_score"]), row["source_path"], row["node_path"]))
    return (analyses[0] if analyses else None), analyses


def select_context(candidates: list[dict[str, Any]], freeze_at: str | None) -> dict[str, Any] | None:
    if not candidates:
        return None
    freeze = parse_iso(freeze_at)
    ranked: list[tuple[int, datetime, dict[str, Any]]] = []
    for candidate in candidates:
        observed = parse_iso(candidate.get("observed_at_utc"))
        valid_before = bool(observed and freeze and observed <= freeze)
        ranked.append((1 if valid_before else 0, observed or datetime.min.replace(tzinfo=timezone.utc), candidate))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return ranked[0][2]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "sequence", "competition_id", "kickoff_at", "home_team", "away_team",
        "home_goals_90", "away_goals_90", "actual_result", "prediction_event_hash",
        "prediction_source_path", "prediction_node_path", "prediction_schema_version",
        "prediction_event_type", "identity_exact_match", "freeze_at_utc",
        "market_observed_at_utc", "market_observed_no_later_than_freeze",
        "freeze_before_kickoff", "has_complete_1x2", "has_complete_asian_handicap",
        "has_complete_over_under", "context_exact_match", "context_observed_at_utc",
        "context_observed_no_later_than_freeze", "embedded_context_field_count",
        "external_context_field_count", "core_pit_complete", "three_market_pit_complete",
        "context_pit_complete", "missing_reasons", "previous_row_hash", "row_hash",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            flat = {field: row.get(field) for field in fields}
            flat["missing_reasons"] = "|".join(row.get("missing_reasons") or [])
            writer.writerow(flat)


def write_gaps(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = ["prediction_event_hash", "competition_id", "kickoff_at", "home_team", "away_team", "actual_result", "gap"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            for gap in row.get("missing_reasons") or []:
                writer.writerow({
                    "prediction_event_hash": row.get("prediction_event_hash"),
                    "competition_id": row.get("competition_id"),
                    "kickoff_at": row.get("kickoff_at"),
                    "home_team": row.get("home_team"),
                    "away_team": row.get("away_team"),
                    "actual_result": row.get("actual_result"),
                    "gap": gap,
                })


def verify_chain(rows: list[dict[str, Any]], genesis: str) -> bool:
    previous = genesis
    for row in rows:
        if row.get("previous_row_hash") != previous:
            return False
        payload = {key: value for key, value in row.items() if key not in ("previous_row_hash", "row_hash")}
        expected = sha256_text(previous + "\n" + canonical_json(payload))
        if row.get("row_hash") != expected:
            return False
        previous = expected
    return True


def build(config: dict[str, Any], results_path: Path, context_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    results_value = load_json(results_path)
    results = results_value.get("results") if isinstance(results_value, dict) else None
    if not isinstance(results, list):
        raise LedgerError("result inbox does not contain a results list")
    results = [row for row in results if isinstance(row, dict)]
    expected_rows = int(config["ledger_contract"]["expected_result_rows"])
    if len(results) != expected_rows:
        raise LedgerError(f"expected {expected_rows} result receipts, found {len(results)}")

    prediction_index, schemas, scan_errors = scan_prediction_occurrences()
    context_index, context_event_count = load_context_index(context_path)
    result_hash_counts = Counter(str(row.get("prediction_event_hash") or "").strip() for row in results)

    raw_rows: list[dict[str, Any]] = []
    occurrence_duplicates = 0
    for result in results:
        expected_fixture = result_fixture(result)
        expected_key = fixture_key(expected_fixture)
        event_hash = str(result.get("prediction_event_hash") or "").strip()
        occurrences = prediction_index.get(event_hash, [])
        occurrence_duplicates += max(0, len(occurrences) - 1)
        selected, all_analyses = choose_occurrence(occurrences, expected_key)
        selected = selected or {}

        freeze_at = selected.get("freeze_at_utc")
        market_at = selected.get("market_observed_at_utc")
        kickoff_at = iso_or_none(result.get("kickoff_at"))
        freeze_dt = parse_iso(freeze_at)
        market_dt = parse_iso(market_at)
        kickoff_dt = parse_iso(kickoff_at)
        market_before_freeze = bool(market_dt and freeze_dt and market_dt <= freeze_dt)
        freeze_before_kickoff = bool(freeze_dt and kickoff_dt and freeze_dt < kickoff_dt)
        identity_exact = bool(expected_key and selected.get("fixture_key") == expected_key)

        context_candidates = context_index.get(expected_key, []) if expected_key else []
        external_context = select_context(context_candidates, freeze_at)
        context_observed = external_context.get("observed_at_utc") if external_context else None
        context_dt = parse_iso(context_observed)
        context_before_freeze = bool(context_dt and freeze_dt and context_dt <= freeze_dt)
        embedded_fields = selected.get("embedded_context_fields") or []
        external_fields = external_context.get("context_fields") if external_context else []
        any_context = bool(embedded_fields or external_fields)
        timestamped_context = bool(context_observed and context_before_freeze and external_fields)

        one_x_two = selected.get("one_x_two")
        asian = selected.get("asian_handicap")
        totals = selected.get("over_under")
        has_result = (
            isinstance(result.get("home_goals_90"), int)
            and isinstance(result.get("away_goals_90"), int)
            and str(result.get("actual_result") or "") in ("home", "draw", "away")
        )

        missing: list[str] = []
        if not event_hash:
            missing.append("MISSING_PREDICTION_EVENT_HASH")
        if not occurrences:
            missing.append("PREDICTION_EVENT_NOT_FOUND")
        if not identity_exact:
            missing.append("PREDICTION_RESULT_IDENTITY_MISMATCH")
        if kickoff_dt is None:
            missing.append("KICKOFF_NOT_TIMEZONE_AWARE")
        if freeze_dt is None:
            missing.append("FREEZE_TIMESTAMP_MISSING")
        if market_dt is None:
            missing.append("MARKET_TIMESTAMP_MISSING")
        elif freeze_dt and not market_before_freeze:
            missing.append("MARKET_TIMESTAMP_AFTER_FREEZE")
        if freeze_dt and kickoff_dt and not freeze_before_kickoff:
            missing.append("FREEZE_NOT_BEFORE_KICKOFF")
        if not one_x_two:
            missing.append("COMPLETE_1X2_MISSING")
        if not asian:
            missing.append("COMPLETE_ASIAN_HANDICAP_MISSING")
        if not totals:
            missing.append("COMPLETE_OVER_UNDER_MISSING")
        if not any_context:
            missing.append("CONTEXT_EVIDENCE_MISSING")
        elif not timestamped_context:
            missing.append("TIMESTAMPED_CONTEXT_BEFORE_FREEZE_MISSING")
        if not has_result:
            missing.append("REGULATION_RESULT_INVALID")

        core_complete = bool(
            occurrences and identity_exact and kickoff_dt and freeze_dt and market_dt
            and market_before_freeze and freeze_before_kickoff and one_x_two and has_result
        )
        three_market_complete = bool(core_complete and asian and totals)
        context_complete = bool(core_complete and timestamped_context)

        raw_rows.append({
            "competition_id": result.get("competition_id"),
            "kickoff_at": kickoff_at or result.get("kickoff_at"),
            "home_team": result.get("home_team"),
            "away_team": result.get("away_team"),
            "normalized_home_team": norm_text(result.get("home_team")),
            "normalized_away_team": norm_text(result.get("away_team")),
            "home_goals_90": result.get("home_goals_90"),
            "away_goals_90": result.get("away_goals_90"),
            "actual_result": result.get("actual_result"),
            "settlement_scope": result.get("settlement_scope"),
            "result_receipt_sha256": sha256_text(canonical_json(result)),
            "result_source": result.get("source"),
            "prediction_event_hash": event_hash,
            "prediction_event_occurrences": len(occurrences),
            "prediction_source_path": selected.get("source_path"),
            "prediction_node_path": selected.get("node_path"),
            "prediction_schema_version": selected.get("schema_version"),
            "prediction_event_type": selected.get("event_type"),
            "prediction_candidate_count": len(all_analyses),
            "prediction_selection_score": selected.get("selection_score"),
            "prediction_fixture_identity": selected.get("fixture"),
            "identity_exact_match": identity_exact,
            "freeze_at_utc": freeze_at,
            "freeze_timestamp_source": selected.get("freeze_timestamp_source"),
            "market_observed_at_utc": market_at,
            "market_timestamp_source": selected.get("market_timestamp_source"),
            "market_observed_no_later_than_freeze": market_before_freeze,
            "freeze_before_kickoff": freeze_before_kickoff,
            "one_x_two": one_x_two,
            "one_x_two_source": selected.get("one_x_two_source"),
            "asian_handicap": asian,
            "asian_handicap_source": selected.get("asian_handicap_source"),
            "over_under": totals,
            "over_under_source": selected.get("over_under_source"),
            "has_complete_1x2": bool(one_x_two),
            "has_complete_asian_handicap": bool(asian),
            "has_complete_over_under": bool(totals),
            "embedded_context_fields": embedded_fields,
            "embedded_context_field_count": len(embedded_fields),
            "context_exact_match": bool(external_context),
            "context_event_hash": external_context.get("event_hash") if external_context else None,
            "context_source_path": external_context.get("source_path") if external_context else None,
            "context_observed_at_utc": context_observed,
            "context_observed_at_source": external_context.get("observed_at_source") if external_context else None,
            "external_context_fields": external_fields,
            "external_context_field_count": len(external_fields),
            "context_observed_no_later_than_freeze": context_before_freeze,
            "core_pit_complete": core_complete,
            "three_market_pit_complete": three_market_complete,
            "context_pit_complete": context_complete,
            "missing_reasons": missing,
        })

    raw_rows.sort(key=lambda row: (
        str(row.get("kickoff_at") or ""), str(row.get("competition_id") or ""),
        str(row.get("normalized_home_team") or ""), str(row.get("normalized_away_team") or ""),
        str(row.get("prediction_event_hash") or ""),
    ))

    genesis = str(config["ledger_contract"]["genesis_hash"])
    rows: list[dict[str, Any]] = []
    previous = genesis
    for sequence, raw in enumerate(raw_rows, start=1):
        payload = {"sequence": sequence, **raw}
        row_hash = sha256_text(previous + "\n" + canonical_json(payload))
        rows.append({**payload, "previous_row_hash": previous, "row_hash": row_hash})
        previous = row_hash

    chain_valid = verify_chain(rows, genesis)
    gap_counts = Counter(gap for row in rows for gap in row.get("missing_reasons") or [])
    draw_rows = [row for row in rows if row.get("actual_result") == "draw"]
    zero_zero_rows = [row for row in draw_rows if row.get("home_goals_90") == 0 and row.get("away_goals_90") == 0]
    strict_rows = [row for row in rows if row.get("core_pit_complete")]
    strict_draws = [row for row in draw_rows if row.get("core_pit_complete")]
    three_market_rows = [row for row in rows if row.get("three_market_pit_complete")]
    three_market_draws = [row for row in draw_rows if row.get("three_market_pit_complete")]
    context_rows = [row for row in rows if row.get("context_pit_complete")]
    context_draws = [row for row in draw_rows if row.get("context_pit_complete")]

    joined_all = all(row.get("prediction_event_occurrences", 0) >= 1 for row in rows)
    exact_all = all(row.get("identity_exact_match") for row in rows)
    unique_result_hashes = all(count == 1 for key, count in result_hash_counts.items() if key)
    build_pass = bool(len(rows) == expected_rows and joined_all and exact_all and unique_result_hashes and chain_valid)
    minimum_draws = int(config["screen10_readiness"]["minimum_strict_pit_draws"])
    market_ready = len(strict_draws) >= minimum_draws
    three_market_ready = len(three_market_draws) >= minimum_draws
    context_ready = len(context_draws) >= minimum_draws

    if not build_pass:
        status = "FAIL_R26_LEDGER_BUILD_OR_IDENTITY_AUDIT"
    elif context_ready:
        status = "PASS_R26_LEDGER_BUILT_CONTEXT_SCREEN10_READY"
    elif three_market_ready:
        status = "PASS_R26_LEDGER_BUILT_THREE_MARKET_SCREEN10_READY_CONTEXT_NOT_READY"
    elif market_ready:
        status = "PASS_R26_LEDGER_BUILT_1X2_SCREEN10_READY_CONTEXT_NOT_READY"
    else:
        status = "PASS_R26_LEDGER_BUILT_NOT_READY_FOR_SCREEN10"

    summary = {
        "schema_version": "v511_forward_pit_linkage_r26_status.1",
        "status": status,
        "classification": config["classification"],
        "formal_weight": 0,
        "build_gate": {
            "passed": build_pass,
            "expected_rows": expected_rows,
            "actual_rows": len(rows),
            "all_results_joined_by_prediction_event_hash": joined_all,
            "all_joined_fixtures_exact_identity_match": exact_all,
            "prediction_event_hash_unique_in_result_inbox": unique_result_hashes,
            "hash_chain_valid": chain_valid,
        },
        "counts": {
            "result_receipts": len(rows),
            "result_draws": len(draw_rows),
            "result_zero_zero_draws": len(zero_zero_rows),
            "prediction_event_hashes_indexed": len(prediction_index),
            "prediction_occurrence_duplicates_for_results": occurrence_duplicates,
            "rows_with_freeze_timestamp": sum(bool(row.get("freeze_at_utc")) for row in rows),
            "rows_with_market_timestamp": sum(bool(row.get("market_observed_at_utc")) for row in rows),
            "rows_with_complete_1x2": sum(bool(row.get("has_complete_1x2")) for row in rows),
            "rows_with_complete_asian_handicap": sum(bool(row.get("has_complete_asian_handicap")) for row in rows),
            "rows_with_complete_over_under": sum(bool(row.get("has_complete_over_under")) for row in rows),
            "rows_with_exact_external_context": sum(bool(row.get("context_exact_match")) for row in rows),
            "rows_with_any_embedded_context": sum(int(row.get("embedded_context_field_count") or 0) > 0 for row in rows),
            "core_pit_complete_rows": len(strict_rows),
            "core_pit_complete_draws": len(strict_draws),
            "three_market_pit_complete_rows": len(three_market_rows),
            "three_market_pit_complete_draws": len(three_market_draws),
            "context_pit_complete_rows": len(context_rows),
            "context_pit_complete_draws": len(context_draws),
            "context_events_scanned": context_event_count,
        },
        "screen10_readiness": {
            "minimum_strict_pit_draws": minimum_draws,
            "market_1x2_ready": market_ready,
            "three_market_ready": three_market_ready,
            "timestamped_context_ready": context_ready,
            "next_action": (
                "run a fixed ten-draw timestamped-context screen" if context_ready else
                "run a fixed ten-draw three-market screen; context claims remain unavailable" if three_market_ready else
                "run a fixed ten-draw timestamped-1X2 screen; three-market/context claims remain unavailable" if market_ready else
                "continue immutable forward collection until at least ten strict PIT draw rows exist"
            ),
        },
        "gap_counts": dict(sorted(gap_counts.items())),
        "competition_distribution": dict(sorted(Counter(str(row.get("competition_id")) for row in rows).items())),
        "result_distribution": dict(sorted(Counter(str(row.get("actual_result")) for row in rows).items())),
        "ledger_integrity": {
            "genesis_hash": genesis,
            "final_row_hash": rows[-1]["row_hash"] if rows else None,
            "canonical_row_count": len(rows),
        },
        "source_contract": config["source_contract"],
        "pit_contract": config["pit_contract"],
        "hard_limits": config["hard_limits"],
        "governance_ruling": {
            "model_training_performed": False,
            "probabilities_generated": False,
            "provider_requests": 0,
            "new_external_data_collection": False,
            "formal_promotion_allowed": False,
            "current_or_main_mutation": False,
            "unified_matrix_allowed": False,
            "exact_score_allowed": False,
            "ev_allowed": False,
        },
        "scan_errors": scan_errors,
        "schema_inventory_top20": dict(schemas.most_common(20)),
    }
    return rows, summary


def self_test() -> None:
    payload = {"sequence": 1, "value": "x"}
    expected = sha256_text("GENESIS\n" + canonical_json(payload))
    row = {**payload, "previous_row_hash": "GENESIS", "row_hash": expected}
    if not verify_chain([row], "GENESIS"):
        raise LedgerError("hash-chain self-test failed")
    fixture = {"competition_id": "X", "kickoff_at": "2026-08-05T10:00:00+00:00", "home_team": "A", "away_team": "B"}
    if fixture_key(fixture) != ("X", "2026-08-05T10:00:00+00:00", "a", "b"):
        raise LedgerError("fixture identity self-test failed")
    print(json.dumps({"self_test": "PASS", "row_hash": expected}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--context", type=Path, default=DEFAULT_CONTEXT)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--jsonl", type=Path, default=DEFAULT_JSONL)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--gaps", type=Path, default=DEFAULT_GAPS)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    config = load_json(args.config)
    rows, summary = build(config, args.results, args.context)
    args.jsonl.parent.mkdir(parents=True, exist_ok=True)
    jsonl_text = "".join(canonical_json(row) + "\n" for row in rows)
    args.jsonl.write_text(jsonl_text, encoding="utf-8")
    summary["ledger_integrity"]["jsonl_sha256"] = hashlib.sha256(jsonl_text.encode("utf-8")).hexdigest()
    write_csv(args.csv, rows)
    write_gaps(args.gaps, rows)
    args.status.parent.mkdir(parents=True, exist_ok=True)
    args.status.write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
