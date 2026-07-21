#!/usr/bin/env python3
"""V5.0.2 player/XI point-in-time data readiness audit.

This audit does not train a model and never changes CURRENT or formal weights.
It verifies whether each registered competition has enough timestamp-safe,
observed starting-XI and availability evidence to begin shadow-only training.

The audit deliberately separates:
- observed starting-XI labels;
- predicted/probable XI inputs;
- injury/suspension availability evidence.

Predicted XI rows are never accepted as observed labels.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FOOTBALL = ROOT / "football-data"
REGISTRY = FOOTBALL / "config" / "platform_registry.json"
LEGACY_ROUTE = FOOTBALL / "validation" / "probable_lineup_route_v462.py"
OUT = FOOTBALL / "manifests" / "player_xi_data_readiness_v502_status.json"

MIN_OBSERVED_TEAM_MATCH_ROWS = 400
MIN_UNIQUE_FIXTURES = 200
MIN_UNIQUE_TEAMS = 8
MIN_SEASONS = 2

OBSERVED_LABEL_TYPES = {
    "observed_starting_xi",
    "confirmed_starting_xi",
    "actual_starting_xi",
}
PREDICTED_LABEL_TYPES = {"predicted_xi", "probable_xi", "projected_xi"}
AVAILABILITY_STATUSES = {
    "injured",
    "suspended",
    "questionable",
    "doubtful",
    "available",
    "out",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_iso(value: Any, field: str, errors: list[str]) -> datetime | None:
    if value in (None, ""):
        errors.append(f"missing_{field}")
        return None
    try:
        text = str(value).strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        errors.append(f"invalid_{field}")
        return None


def read_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    if not path.is_file():
        return rows, errors
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"invalid_json_line_{line_number}")
            continue
        if not isinstance(row, dict):
            errors.append(f"non_object_line_{line_number}")
            continue
        rows.append(row)
    return rows, errors


def audit_lineups(competition_id: str) -> dict[str, Any]:
    path = FOOTBALL / "lineups" / competition_id / "historical_lineups.jsonl"
    rows, errors = read_jsonl(path)
    safe_rows = 0
    predicted_rows = 0
    unknown_label_rows = 0
    timestamp_complete_rows = 0
    fixtures: set[str] = set()
    teams: set[str] = set()
    seasons: set[str] = set()
    duplicate_keys: Counter[tuple[str, str, str]] = Counter()
    source_names: Counter[str] = Counter()
    player_namespace_counts: Counter[str] = Counter()

    for index, row in enumerate(rows, start=1):
        row_errors: list[str] = []
        if str(row.get("competition_id") or "") != competition_id:
            row_errors.append("competition_id_mismatch")
        season = str(row.get("season") or "").strip()
        team = str(row.get("team") or "").strip()
        fixture_id = str(row.get("fixture_id") or row.get("game_id") or "").strip()
        label_type = str(row.get("label_type") or "").strip().lower()
        starters = row.get("starters")
        kickoff = parse_iso(row.get("kickoff_utc"), "kickoff_utc", row_errors)
        source_observed = parse_iso(
            row.get("source_observed_at_utc"),
            "source_observed_at_utc",
            row_errors,
        )

        if not season:
            row_errors.append("missing_season")
        if not team:
            row_errors.append("missing_team")
        if not fixture_id:
            row_errors.append("missing_fixture_id")
        if not isinstance(starters, list) or len(starters) != 11:
            row_errors.append("starters_not_exactly_11")
        elif len({str(item).strip() for item in starters if str(item).strip()}) != 11:
            row_errors.append("starters_not_11_unique_nonempty_ids")

        if label_type in PREDICTED_LABEL_TYPES:
            predicted_rows += 1
            row_errors.append("predicted_xi_not_observed_label")
        elif label_type not in OBSERVED_LABEL_TYPES:
            unknown_label_rows += 1
            row_errors.append("unrecognized_or_missing_label_type")

        source_name = str(row.get("source_name") or row.get("source") or "").strip()
        source_url = str(row.get("source_url") or "").strip()
        if not source_name:
            row_errors.append("missing_source_name")
        if not source_url:
            row_errors.append("missing_source_url")

        if kickoff and source_observed:
            timestamp_complete_rows += 1

        namespace = str(row.get("player_id_namespace") or "").strip()
        if not namespace:
            row_errors.append("missing_player_id_namespace")

        if not row_errors:
            safe_rows += 1
            fixtures.add(fixture_id)
            teams.add(team)
            seasons.add(season)
            duplicate_keys[(fixture_id, team, label_type)] += 1
            source_names[source_name] += 1
            player_namespace_counts[namespace] += 1
        else:
            errors.extend(f"row_{index}:{item}" for item in row_errors)

    duplicate_count = sum(count - 1 for count in duplicate_keys.values() if count > 1)
    if duplicate_count:
        errors.append(f"duplicate_observed_team_fixture_rows:{duplicate_count}")

    trainable = (
        safe_rows >= MIN_OBSERVED_TEAM_MATCH_ROWS
        and len(fixtures) >= MIN_UNIQUE_FIXTURES
        and len(teams) >= MIN_UNIQUE_TEAMS
        and len(seasons) >= MIN_SEASONS
        and predicted_rows == 0
        and unknown_label_rows == 0
        and duplicate_count == 0
    )

    if not path.is_file():
        status = "NO_LINEUP_DATA_FILE"
    elif not rows:
        status = "EMPTY_LINEUP_DATA"
    elif errors:
        status = "LINEUP_DATA_CONTRACT_FAIL"
    elif trainable:
        status = "LINEUP_LABELS_READY_FOR_SHADOW_TRAINING"
    else:
        status = "LINEUP_LABELS_INSUFFICIENT_SAMPLE"

    return {
        "path": path.relative_to(ROOT).as_posix(),
        "status": status,
        "file_present": path.is_file(),
        "row_count": len(rows),
        "safe_observed_row_count": safe_rows,
        "predicted_row_count": predicted_rows,
        "unknown_label_row_count": unknown_label_rows,
        "timestamp_complete_row_count": timestamp_complete_rows,
        "unique_fixture_count": len(fixtures),
        "unique_team_count": len(teams),
        "season_count": len(seasons),
        "duplicate_count": duplicate_count,
        "source_names": dict(source_names),
        "player_id_namespaces": dict(player_namespace_counts),
        "trainable": trainable,
        "error_count": len(errors),
        "error_examples": errors[:25],
    }


def audit_availability(competition_id: str) -> dict[str, Any]:
    path = FOOTBALL / "availability" / competition_id / "historical_availability.jsonl"
    rows, errors = read_jsonl(path)
    safe_rows = 0
    fixtures: set[str] = set()
    teams: set[str] = set()
    players: set[str] = set()
    statuses: Counter[str] = Counter()

    for index, row in enumerate(rows, start=1):
        row_errors: list[str] = []
        if str(row.get("competition_id") or "") != competition_id:
            row_errors.append("competition_id_mismatch")
        fixture_id = str(row.get("fixture_id") or row.get("game_id") or "").strip()
        team = str(row.get("team") or "").strip()
        player_id = str(row.get("player_id") or "").strip()
        status = str(row.get("status") or "").strip().lower()
        freeze = parse_iso(row.get("freeze_time_utc"), "freeze_time_utc", row_errors)
        observed = parse_iso(
            row.get("source_observed_at_utc"),
            "source_observed_at_utc",
            row_errors,
        )

        if not fixture_id:
            row_errors.append("missing_fixture_id")
        if not team:
            row_errors.append("missing_team")
        if not player_id:
            row_errors.append("missing_player_id")
        if status not in AVAILABILITY_STATUSES:
            row_errors.append("invalid_availability_status")
        if freeze and observed and observed > freeze:
            row_errors.append("post_freeze_availability_evidence")
        if not str(row.get("source_name") or row.get("source") or "").strip():
            row_errors.append("missing_source_name")
        if not str(row.get("source_url") or "").strip():
            row_errors.append("missing_source_url")
        if not str(row.get("player_id_namespace") or "").strip():
            row_errors.append("missing_player_id_namespace")

        if not row_errors:
            safe_rows += 1
            fixtures.add(fixture_id)
            teams.add(team)
            players.add(player_id)
            statuses[status] += 1
        else:
            errors.extend(f"row_{index}:{item}" for item in row_errors)

    if not path.is_file():
        status = "NO_AVAILABILITY_DATA_FILE"
    elif not rows:
        status = "EMPTY_AVAILABILITY_DATA"
    elif errors:
        status = "AVAILABILITY_DATA_CONTRACT_FAIL"
    elif safe_rows:
        status = "AVAILABILITY_DATA_PRESENT"
    else:
        status = "AVAILABILITY_DATA_UNUSABLE"

    return {
        "path": path.relative_to(ROOT).as_posix(),
        "status": status,
        "file_present": path.is_file(),
        "row_count": len(rows),
        "safe_row_count": safe_rows,
        "unique_fixture_count": len(fixtures),
        "unique_team_count": len(teams),
        "unique_player_count": len(players),
        "status_counts": dict(statuses),
        "error_count": len(errors),
        "error_examples": errors[:25],
    }


def audit_legacy_route() -> dict[str, Any]:
    text = LEGACY_ROUTE.read_text(encoding="utf-8") if LEGACY_ROUTE.is_file() else ""
    kickoff_guard = 'item["kickoff"] < row["kickoff"]' in text
    observed_timestamp_guard = "source_observed_at_utc" in text
    explicit_label_type_guard = "label_type" in text and "predicted_xi" in text
    exact_eleven_guard = "len(starters) != 11" in text
    return {
        "path": LEGACY_ROUTE.relative_to(ROOT).as_posix(),
        "file_present": LEGACY_ROUTE.is_file(),
        "same_season_kickoff_guard": kickoff_guard,
        "source_observed_at_guard": observed_timestamp_guard,
        "observed_vs_predicted_label_guard": explicit_label_type_guard,
        "exact_eleven_guard": exact_eleven_guard,
        "shadow_training_route_safe": bool(
            kickoff_guard
            and observed_timestamp_guard
            and explicit_label_type_guard
            and exact_eleven_guard
        ),
        "policy": "Legacy V4.6.2 route is not reused for V5 player/XI shadow training unless every timestamp and observed-label guard is explicit.",
    }


def audit() -> dict[str, Any]:
    registry = load_json(REGISTRY)
    competitions = [
        str(item["competition_id"])
        for item in registry.get("competitions", [])
        if isinstance(item, dict) and item.get("competition_id")
    ]

    reports: dict[str, Any] = {}
    trainable_lineup_domains: list[str] = []
    availability_domains: list[str] = []
    full_candidate_domains: list[str] = []

    for competition_id in competitions:
        lineup = audit_lineups(competition_id)
        availability = audit_availability(competition_id)
        if lineup["trainable"]:
            trainable_lineup_domains.append(competition_id)
        if availability["safe_row_count"] > 0 and availability["error_count"] == 0:
            availability_domains.append(competition_id)
        if (
            lineup["trainable"]
            and availability["safe_row_count"] > 0
            and availability["error_count"] == 0
        ):
            full_candidate_domains.append(competition_id)

        if not lineup["file_present"]:
            status = "BLOCKED_NO_ACQUIRED_LINEUP_DATA"
        elif lineup["error_count"]:
            status = "BLOCKED_LINEUP_CONTRACT_FAIL"
        elif not lineup["trainable"]:
            status = "BLOCKED_INSUFFICIENT_LINEUP_SAMPLE"
        elif not availability["file_present"]:
            status = "LINEUP_ONLY_AVAILABILITY_UNAVAILABLE"
        elif availability["error_count"]:
            status = "BLOCKED_AVAILABILITY_CONTRACT_FAIL"
        else:
            status = "READY_FOR_PLAYER_XI_SHADOW_TRAINING"

        reports[competition_id] = {
            "status": status,
            "lineup": lineup,
            "availability": availability,
            "formal_weight": 0,
        }

    legacy_route = audit_legacy_route()
    if full_candidate_domains and legacy_route["shadow_training_route_safe"]:
        status = "PARTIAL_READY_FOR_SHADOW_TRAINING"
    elif trainable_lineup_domains:
        status = "LINEUP_LABELS_PARTIAL_ROUTE_OR_AVAILABILITY_BLOCKED"
    else:
        status = "BLOCKED_NO_TRAINABLE_DOMAIN"

    return {
        "schema_version": "V5.0.2-player-xi-data-readiness-r1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "registered_competition_count": len(competitions),
        "trainable_lineup_domain_count": len(trainable_lineup_domains),
        "availability_domain_count": len(availability_domains),
        "full_shadow_candidate_domain_count": len(full_candidate_domains),
        "trainable_lineup_domains": trainable_lineup_domains,
        "availability_domains": availability_domains,
        "full_shadow_candidate_domains": full_candidate_domains,
        "legacy_route_audit": legacy_route,
        "minimum_gates": {
            "observed_team_match_rows": MIN_OBSERVED_TEAM_MATCH_ROWS,
            "unique_fixtures": MIN_UNIQUE_FIXTURES,
            "unique_teams": MIN_UNIQUE_TEAMS,
            "seasons": MIN_SEASONS,
            "timestamp_completeness": "100%",
            "predicted_rows_in_observed_labels": 0,
        },
        "reports": reports,
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "policy": "Data-readiness and leakage audit only. Player/XI modules remain formal_weight=0 until competition-specific chronological OOF and hash-bound promotion.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-receipt", action="store_true")
    parser.add_argument("--strict-exit", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()

    report = audit()
    if args.write_receipt:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.print_summary:
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "registered_competition_count": report[
                        "registered_competition_count"
                    ],
                    "trainable_lineup_domain_count": report[
                        "trainable_lineup_domain_count"
                    ],
                    "availability_domain_count": report[
                        "availability_domain_count"
                    ],
                    "full_shadow_candidate_domain_count": report[
                        "full_shadow_candidate_domain_count"
                    ],
                    "legacy_route_audit": report["legacy_route_audit"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return (
        2
        if args.strict_exit and report["status"] == "BLOCKED_NO_TRAINABLE_DOMAIN"
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
