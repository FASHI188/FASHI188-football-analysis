#!/usr/bin/env python3
"""Audit existing processed score labels and market identities for strict V5.1 PIT use.

The audit is deliberately conservative. Historical closing prices without an original quote
timestamp are counted as retrospective market references, never as formal pre-match snapshots.
No model is fitted and no probability is generated.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "v510_existing_score_market_pit_ledger_r1.json"
PROCESSED = ROOT / "processed"
CONTEXT = ROOT / "forward" / "v6_context_enriched_events_v6486.json"
RESULT_INBOX = ROOT / "forward" / "inbox" / "market_first_results_v651.json"
DEFAULT_OUT = ROOT / "manifests" / "v510_existing_score_market_pit_ledger_r1_status.json"
DEFAULT_LEDGER = ROOT / "manifests" / "v510_existing_score_market_pit_ledger_r1_rows.csv"


class AuditError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise AuditError(f"missing input: {path.relative_to(ROOT)}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuditError(f"JSON root is not an object: {path.relative_to(ROOT)}")
    return value


def norm_header(value: Any) -> str:
    return re.sub(r"[^a-z0-9><]+", "", str(value or "").casefold())


def normalize_team(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(ch for ch in text if ch.isalnum())


def field_name(headers: list[str], aliases: list[str]) -> str | None:
    mapping = {norm_header(header): header for header in headers}
    for alias in aliases:
        hit = mapping.get(norm_header(alias))
        if hit is not None:
            return hit
    return None


def text_value(row: dict[str, Any], field: str | None) -> str:
    return str(row.get(field) or "").strip() if field else ""


def int_value(row: dict[str, Any], field: str | None) -> int | None:
    text = text_value(row, field)
    if not text:
        return None
    try:
        value = int(float(text))
    except ValueError:
        return None
    return value if 0 <= value <= 30 else None


def float_value(row: dict[str, Any], field: str | None) -> float | None:
    text = text_value(row, field)
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def valid_price(value: float | None) -> bool:
    return value is not None and value > 1.0


def parse_iso_timestamp(value: Any) -> datetime | None:
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


def parse_kickoff(date_text: str, time_text: str) -> tuple[datetime | None, str | None, bool]:
    date_text = date_text.strip()
    time_text = time_text.strip()
    if not date_text:
        return None, None, False

    direct = parse_iso_timestamp(date_text)
    if direct is not None:
        return direct, direct.date().isoformat(), True

    date_formats = ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d", "%m/%d/%Y")
    parsed_date = None
    for fmt in date_formats:
        try:
            parsed_date = datetime.strptime(date_text, fmt)
            break
        except ValueError:
            continue
    if parsed_date is None:
        return None, None, False

    hour = minute = second = 0
    if time_text:
        parsed_time = None
        for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p"):
            try:
                parsed_time = datetime.strptime(time_text, fmt)
                break
            except ValueError:
                continue
        if parsed_time is None:
            return None, parsed_date.date().isoformat(), False
        hour, minute, second = parsed_time.hour, parsed_time.minute, parsed_time.second
    naive = parsed_date.replace(hour=hour, minute=minute, second=second)
    return naive, naive.date().isoformat(), False


def first_complete_triplet(row: dict[str, Any], headers: list[str], candidates: list[tuple[str, str, str]]) -> str | None:
    for home_alias, draw_alias, away_alias in candidates:
        home = field_name(headers, [home_alias])
        draw = field_name(headers, [draw_alias])
        away = field_name(headers, [away_alias])
        if all((home, draw, away)) and all(valid_price(float_value(row, field)) for field in (home, draw, away)):
            return f"{home}/{draw}/{away}"
    return None


def detect_markets(row: dict[str, Any], headers: list[str]) -> dict[str, Any]:
    one_x_two = first_complete_triplet(row, headers, [
        ("PSCH", "PSCD", "PSCA"), ("AvgCH", "AvgCD", "AvgCA"),
        ("MaxCH", "MaxCD", "MaxCA"), ("B365CH", "B365CD", "B365CA"),
        ("PSH", "PSD", "PSA"), ("AvgH", "AvgD", "AvgA"),
        ("MaxH", "MaxD", "MaxA"), ("B365H", "B365D", "B365A"),
        ("home_odds", "draw_odds", "away_odds"),
    ])

    ah_line = field_name(headers, ["AHCh", "AHh", "asian_handicap_line", "handicap_line", "spread_line"])
    ah_pair = None
    for left_alias, right_alias in [
        ("PCAHH", "PCAHA"), ("AvgCAHH", "AvgCAHA"), ("MaxCAHH", "MaxCAHA"),
        ("B365CAHH", "B365CAHA"), ("PAHH", "PAHA"), ("AvgAHH", "AvgAHA"),
        ("asian_home_odds", "asian_away_odds"),
    ]:
        left = field_name(headers, [left_alias])
        right = field_name(headers, [right_alias])
        if left and right and valid_price(float_value(row, left)) and valid_price(float_value(row, right)):
            ah_pair = f"{left}/{right}"
            break
    ah_available = bool(ah_line and float_value(row, ah_line) is not None and ah_pair)

    total_pair = None
    total_line_value = None
    for over_alias, under_alias, implicit_line in [
        ("PC>2.5", "PC<2.5", 2.5), ("AvgC>2.5", "AvgC<2.5", 2.5),
        ("MaxC>2.5", "MaxC<2.5", 2.5), ("B365C>2.5", "B365C<2.5", 2.5),
        ("P>2.5", "P<2.5", 2.5), ("Avg>2.5", "Avg<2.5", 2.5),
        ("over_odds", "under_odds", None),
    ]:
        over = field_name(headers, [over_alias])
        under = field_name(headers, [under_alias])
        if over and under and valid_price(float_value(row, over)) and valid_price(float_value(row, under)):
            total_pair = f"{over}/{under}"
            total_line_value = implicit_line
            break
    total_line = field_name(headers, ["total_line", "over_under_line", "OUCh", "closing_total_line"])
    if total_line_value is None and total_line:
        total_line_value = float_value(row, total_line)
    total_available = bool(total_pair and total_line_value is not None)

    quote_field = field_name(headers, [
        "market_observed_at_utc", "observed_at_utc", "available_at_utc", "snapshot_at_utc",
        "quoted_at_utc", "odds_timestamp_utc", "market_timestamp_utc", "collected_at_utc",
    ])
    quote_ts = parse_iso_timestamp(text_value(row, quote_field)) if quote_field else None

    return {
        "one_x_two": bool(one_x_two),
        "one_x_two_fields": one_x_two,
        "asian_handicap": ah_available,
        "asian_fields": f"{ah_line}|{ah_pair}" if ah_available else None,
        "totals": total_available,
        "totals_fields": total_pair,
        "synchronized_reference": bool(one_x_two and ah_available and total_available),
        "quote_timestamp_field": quote_field,
        "quote_timestamp_utc": quote_ts.isoformat() if quote_ts else None,
    }


def result_consistent(home: int, away: int, raw_result: str) -> bool:
    if not raw_result:
        return True
    actual = "H" if home > away else "A" if away > home else "D"
    aliases = {"HOME": "H", "DRAW": "D", "AWAY": "A", "H": "H", "D": "D", "A": "A"}
    return aliases.get(raw_result.strip().upper()) == actual


def total_bucket(total: int) -> str:
    return str(total) if total <= 6 else "7+"


def extract_context_identities() -> tuple[set[tuple[str, str, str, str]], set[tuple[str, str, str, str]]]:
    if not CONTEXT.is_file():
        return set(), set()
    value = json.loads(CONTEXT.read_text(encoding="utf-8"))
    events = value.get("events") or value.get("rows") or value.get("records") or []
    date_keys: set[tuple[str, str, str, str]] = set()
    exact_keys: set[tuple[str, str, str, str]] = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        fixture = payload.get("fixture_identity") if isinstance(payload.get("fixture_identity"), dict) else {}
        kickoff = parse_iso_timestamp(fixture.get("kickoff_at"))
        competition = str(fixture.get("competition_id") or "").strip()
        home = normalize_team(fixture.get("home_team"))
        away = normalize_team(fixture.get("away_team"))
        if kickoff and competition and home and away:
            date_keys.add((competition, kickoff.date().isoformat(), home, away))
            exact_keys.add((competition, kickoff.isoformat(), home, away))
    return date_keys, exact_keys


def scan_processed() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if not PROCESSED.is_dir():
        raise AuditError(f"missing processed root: {PROCESSED.relative_to(ROOT)}")
    rows_out: list[dict[str, Any]] = []
    source_summaries: list[dict[str, Any]] = []
    file_errors: list[dict[str, Any]] = []

    for path in sorted(PROCESSED.rglob("*.csv")):
        relative = str(path.relative_to(ROOT)).replace("\\", "/")
        counters = Counter()
        distribution = Counter()
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                headers = list(reader.fieldnames or [])
                competition_field = field_name(headers, ["competition_id", "competition", "league_id"])
                season_field = field_name(headers, ["season", "source_season", "Season"])
                date_field = field_name(headers, ["Date", "date", "kickoff_at", "kickoff_utc"])
                time_field = field_name(headers, ["Time", "time"])
                home_field = field_name(headers, ["HomeTeam", "home_team", "home"])
                away_field = field_name(headers, ["AwayTeam", "away_team", "away"])
                home_score_field = field_name(headers, ["FTHG", "home_goals_90", "home_score"])
                away_score_field = field_name(headers, ["FTAG", "away_goals_90", "away_score"])
                result_field = field_name(headers, ["FTR", "actual_result", "result"])

                for row_number, row in enumerate(reader, start=2):
                    counters["rows"] += 1
                    competition = text_value(row, competition_field) or path.parent.name
                    season = text_value(row, season_field)
                    home_team = text_value(row, home_field)
                    away_team = text_value(row, away_field)
                    home_score = int_value(row, home_score_field)
                    away_score = int_value(row, away_score_field)
                    kickoff, date_key, timezone_explicit = parse_kickoff(
                        text_value(row, date_field), text_value(row, time_field)
                    )
                    identity_complete = bool(competition and date_key and home_team and away_team)
                    if identity_complete:
                        counters["identity_complete"] += 1
                    if timezone_explicit:
                        counters["kickoff_timezone_explicit"] += 1

                    score_valid = home_score is not None and away_score is not None
                    result_ok = score_valid and result_consistent(
                        int(home_score), int(away_score), text_value(row, result_field)
                    )
                    if score_valid:
                        counters["score_rows"] += 1
                    if result_ok:
                        counters["score_result_consistent"] += 1

                    market = detect_markets(row, headers)
                    for name in ("one_x_two", "asian_handicap", "totals", "synchronized_reference"):
                        if market[name]:
                            counters[name] += 1
                    if market["quote_timestamp_utc"]:
                        counters["original_quote_timestamp"] += 1

                    quote_before_kickoff = False
                    if market["quote_timestamp_utc"] and timezone_explicit and kickoff is not None:
                        quote = parse_iso_timestamp(market["quote_timestamp_utc"])
                        if quote and kickoff.tzinfo is not None and quote < kickoff.astimezone(timezone.utc):
                            quote_before_kickoff = True
                            counters["quote_before_kickoff"] += 1

                    strict_market = bool(
                        market["synchronized_reference"]
                        and timezone_explicit
                        and market["quote_timestamp_utc"]
                        and quote_before_kickoff
                    )
                    if strict_market:
                        counters["strict_market_pit"] += 1

                    if not (identity_complete and score_valid):
                        continue
                    total = int(home_score) + int(away_score)
                    difference = int(home_score) - int(away_score)
                    bucket = total_bucket(total)
                    distribution[bucket] += 1
                    if difference == 0:
                        counters["draw_rows"] += 1
                    rows_out.append({
                        "source_file": relative,
                        "row_number": row_number,
                        "competition_id": competition,
                        "season": season,
                        "date_key": date_key,
                        "kickoff_raw": text_value(row, date_field),
                        "time_raw": text_value(row, time_field),
                        "kickoff_timezone_explicit": timezone_explicit,
                        "kickoff_utc": kickoff.astimezone(timezone.utc).isoformat() if timezone_explicit and kickoff else None,
                        "home_team": home_team,
                        "away_team": away_team,
                        "normalized_home_team": normalize_team(home_team),
                        "normalized_away_team": normalize_team(away_team),
                        "home_goals_90": int(home_score),
                        "away_goals_90": int(away_score),
                        "result_consistent": bool(result_ok),
                        "total_goals": total,
                        "total_bucket": bucket,
                        "goal_difference": difference,
                        "one_x_two_reference": market["one_x_two"],
                        "asian_handicap_reference": market["asian_handicap"],
                        "totals_reference": market["totals"],
                        "synchronized_three_market_reference": market["synchronized_reference"],
                        "original_quote_timestamp_utc": market["quote_timestamp_utc"],
                        "quote_before_kickoff": quote_before_kickoff,
                        "strict_market_pit": strict_market,
                    })
        except Exception as exc:  # audit must retain source-level failures
            file_errors.append({"source_file": relative, "reason": f"{type(exc).__name__}: {exc}"})
            continue
        source_summaries.append({
            "source_file": relative,
            **dict(counters),
            "total_goal_distribution": dict(sorted(distribution.items(), key=lambda item: (item[0] == "7+", item[0]))),
        })
    return rows_out, source_summaries, file_errors


def write_ledger(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "source_file", "row_number", "competition_id", "season", "date_key", "kickoff_raw", "time_raw",
        "kickoff_timezone_explicit", "kickoff_utc", "home_team", "away_team", "home_goals_90",
        "away_goals_90", "result_consistent", "total_goals", "total_bucket", "goal_difference",
        "one_x_two_reference", "asian_handicap_reference", "totals_reference",
        "synchronized_three_market_reference", "original_quote_timestamp_utc", "quote_before_kickoff",
        "context_date_team_match", "context_exact_utc_match", "strict_pit_eligible", "strict_pit_failure_reasons",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})


def run(config: dict[str, Any], out_path: Path, ledger_path: Path) -> dict[str, Any]:
    rows, source_summaries, file_errors = scan_processed()
    context_date_keys, context_exact_keys = extract_context_identities()

    total_distribution = Counter(row["total_bucket"] for row in rows)
    goal_difference_by_total: dict[str, Counter[str]] = defaultdict(Counter)
    competition_counts = Counter(row["competition_id"] for row in rows)
    duplicate_counter = Counter(
        (row["competition_id"], row["date_key"], row["normalized_home_team"], row["normalized_away_team"])
        for row in rows
    )
    duplicate_identity_rows = sum(count - 1 for count in duplicate_counter.values() if count > 1)

    context_date_matches = context_exact_matches = strict_rows = strict_draws = 0
    strict_full_context = 0
    for row in rows:
        date_key = (
            row["competition_id"], row["date_key"], row["normalized_home_team"], row["normalized_away_team"]
        )
        exact_key = (
            row["competition_id"], row["kickoff_utc"], row["normalized_home_team"], row["normalized_away_team"]
        ) if row["kickoff_utc"] else None
        date_match = date_key in context_date_keys
        exact_match = bool(exact_key and exact_key in context_exact_keys)
        context_date_matches += int(date_match)
        context_exact_matches += int(exact_match)
        reasons = []
        if not row["kickoff_timezone_explicit"]:
            reasons.append("kickoff_timezone_provenance_missing")
        if not row["synchronized_three_market_reference"]:
            reasons.append("synchronized_1x2_ah_ou_missing")
        if not row["original_quote_timestamp_utc"]:
            reasons.append("original_market_quote_timestamp_missing")
        elif not row["quote_before_kickoff"]:
            reasons.append("quote_not_verified_before_kickoff")
        if not exact_match:
            reasons.append("frozen_context_packet_exact_identity_missing")
        strict = not reasons and row["result_consistent"]
        row["context_date_team_match"] = date_match
        row["context_exact_utc_match"] = exact_match
        row["strict_pit_eligible"] = strict
        row["strict_pit_failure_reasons"] = "|".join(reasons)
        strict_rows += int(strict)
        strict_draws += int(strict and row["goal_difference"] == 0)
        strict_full_context += int(strict and exact_match)
        goal_difference_by_total[row["total_bucket"]][str(row["goal_difference"])] += 1

    thresholds = config["label_coverage_thresholds"]
    label_checks = {
        "minimum_score_rows": len(rows) >= int(thresholds["minimum_score_rows"]),
        "minimum_draw_rows": sum(row["goal_difference"] == 0 for row in rows) >= int(thresholds["minimum_draw_rows"]),
        "minimum_rows_each_total_0_to_6": all(
            total_distribution[str(total)] >= int(thresholds["minimum_rows_per_total_bucket_0_to_6"])
            for total in range(7)
        ),
        "minimum_7plus_rows": total_distribution["7+"] >= int(thresholds["minimum_7plus_rows"]),
        "no_file_errors": not file_errors,
    }
    strict_thresholds = config["strict_pit_fit_thresholds"]
    strict_checks = {
        "minimum_rows": strict_rows >= int(strict_thresholds["minimum_rows"]),
        "minimum_draw_rows": strict_draws >= int(strict_thresholds["minimum_draw_rows"]),
        "minimum_full_context_rows": strict_full_context >= int(strict_thresholds["minimum_full_context_rows"]),
    }
    label_pass = all(label_checks.values())
    strict_pass = all(strict_checks.values())

    result_inbox_count = 0
    if RESULT_INBOX.is_file():
        inbox = json.loads(RESULT_INBOX.read_text(encoding="utf-8"))
        values = inbox.get("results") or inbox.get("rows") or inbox.get("records") or []
        result_inbox_count = len(values) if isinstance(values, list) else 0

    if strict_pass:
        status = "PASS_STRICT_PIT_SCORE_MARKET_LEDGER_READY"
    elif label_pass:
        status = "PASS_SCORE_LABEL_LEDGER_STRICT_PIT_FEATURES_UNAVAILABLE"
    else:
        status = "FAIL_SCORE_LABEL_LEDGER_INSUFFICIENT"

    result = {
        "schema_version": config["schema_version"],
        "status": status,
        "counts": {
            "processed_csv_files": len(source_summaries),
            "file_errors": len(file_errors),
            "score_identity_rows": len(rows),
            "competitions": len(competition_counts),
            "duplicate_date_team_identity_rows": duplicate_identity_rows,
            "draw_rows": sum(row["goal_difference"] == 0 for row in rows),
            "zero_zero_rows": sum(row["home_goals_90"] == 0 and row["away_goals_90"] == 0 for row in rows),
            "rows_with_1x2_reference": sum(row["one_x_two_reference"] for row in rows),
            "rows_with_asian_reference": sum(row["asian_handicap_reference"] for row in rows),
            "rows_with_totals_reference": sum(row["totals_reference"] for row in rows),
            "rows_with_synchronized_three_market_reference": sum(row["synchronized_three_market_reference"] for row in rows),
            "rows_with_original_market_quote_timestamp": sum(bool(row["original_quote_timestamp_utc"]) for row in rows),
            "rows_with_explicit_kickoff_timezone": sum(row["kickoff_timezone_explicit"] for row in rows),
            "context_fixture_identities": len(context_date_keys),
            "context_date_team_matches": context_date_matches,
            "context_exact_utc_matches": context_exact_matches,
            "strict_pit_eligible_rows": strict_rows,
            "strict_pit_draw_rows": strict_draws,
            "result_inbox_rows": result_inbox_count,
        },
        "competition_counts": dict(sorted(competition_counts.items())),
        "total_goal_distribution": {str(total): total_distribution[str(total)] for total in range(7)} | {"7+": total_distribution["7+"]},
        "goal_difference_by_total": {
            total: dict(sorted(values.items(), key=lambda item: int(item[0])))
            for total, values in sorted(goal_difference_by_total.items(), key=lambda item: (item[0] == "7+", item[0]))
        },
        "label_coverage_gate": {"passed": label_pass, "checks": label_checks, "thresholds": thresholds},
        "strict_pit_fit_gate": {"passed": strict_pass, "checks": strict_checks, "thresholds": strict_thresholds},
        "source_summaries": source_summaries,
        "file_errors": file_errors,
        "market_evidence_ruling": {
            "processed_closing_prices": "回顾性市场参考",
            "formal_pre_match_snapshot": strict_pass,
            "reason": "original quote timestamp and timezone provenance are required; a closing-price column name alone is insufficient",
        },
        "model_ruling": {
            "score_labels_available": label_pass,
            "strict_pit_features_available": strict_pass,
            "direct_total_fit_allowed": strict_pass,
            "conditional_goal_difference_fit_allowed": strict_pass,
            "unified_score_matrix_allowed": False,
            "training_run_performed": False,
            "probabilities_generated": False,
        },
        "fixed_outputs": {
            "total_goals": "总进球分布不可用。",
            "exact_score": "精确比分不可用。",
        },
        "governance": config["governance"],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_ledger(ledger_path, rows)
    return result


def self_test() -> None:
    headers = [
        "competition_id", "Date", "Time", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR",
        "PSCH", "PSCD", "PSCA", "AHCh", "PCAHH", "PCAHA", "PC>2.5", "PC<2.5",
    ]
    row = {
        "competition_id": "TEST", "Date": "04/08/2026", "Time": "20:00",
        "HomeTeam": "Home", "AwayTeam": "Away", "FTHG": "0", "FTAG": "0", "FTR": "D",
        "PSCH": "2.2", "PSCD": "3.1", "PSCA": "3.4", "AHCh": "-0.25",
        "PCAHH": "1.95", "PCAHA": "1.95", "PC>2.5": "2.0", "PC<2.5": "1.9",
    }
    market = detect_markets(row, headers)
    assert market["synchronized_reference"] is True
    kickoff, date_key, aware = parse_kickoff("04/08/2026", "20:00")
    assert kickoff is not None and date_key == "2026-08-04" and aware is False
    assert result_consistent(0, 0, "D")
    assert total_bucket(0) == "0" and total_bucket(7) == "7+"
    assert parse_iso_timestamp("2026-08-04T19:00:00Z") is not None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        print(json.dumps({"status": "PASS", "self_test": True}))
        return
    result = run(load_json(args.config), args.out, args.ledger)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
