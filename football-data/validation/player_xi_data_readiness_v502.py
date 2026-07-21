#!/usr/bin/env python3
"""V5.0.2 player/XI point-in-time data readiness and leakage audit.

The audit never trains a probability model. It separates observed starting-XI
labels, predicted XI inputs and player availability evidence, and it reports
which competition domains may proceed to shadow-only training.
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
SHADOW_ROUTE = FOOTBALL / "validation" / "probable_lineup_route_v502.py"
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
    "injured", "suspended", "questionable", "doubtful", "available", "out"
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def read_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    if not path.is_file():
        return [], []
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("not_object")
            rows.append(row)
        except Exception as exc:
            errors.append(f"line_{line_number}:{exc}")
    return rows, errors


def audit_lineups(competition_id: str) -> dict[str, Any]:
    path = FOOTBALL / "lineups" / competition_id / "historical_lineups.jsonl"
    rows, parse_errors = read_jsonl(path)
    errors = list(parse_errors)
    safe_rows = 0
    predicted_rows = 0
    unknown_label_rows = 0
    timestamp_complete_rows = 0
    fixtures: set[str] = set()
    teams: set[str] = set()
    seasons: set[str] = set()
    duplicate_keys: Counter[tuple[str, str, str]] = Counter()
    source_names: Counter[str] = Counter()
    namespaces: Counter[str] = Counter()

    for index, row in enumerate(rows, 1):
        row_errors: list[str] = []
        if str(row.get("competition_id") or "") != competition_id:
            row_errors.append("competition_id_mismatch")
        season = str(row.get("season") or "").strip()
        team = str(row.get("team_source_id") or row.get("team") or "").strip()
        fixture_id = str(row.get("fixture_id") or row.get("game_id") or "").strip()
        label_type = str(row.get("label_type") or "").strip().lower()
        starters = [str(item).strip() for item in row.get("starters") or []]
        kickoff = parse_iso(row.get("kickoff_utc"))
        observed = parse_iso(row.get("source_observed_at_utc"))
        source_name = str(row.get("source_name") or row.get("source") or "").strip()
        source_url = str(row.get("source_url") or "").strip()
        namespace = str(row.get("player_id_namespace") or "").strip()

        if not season:
            row_errors.append("missing_season")
        if not team:
            row_errors.append("missing_team")
        if not fixture_id:
            row_errors.append("missing_fixture_id")
        if len(starters) != 11 or len(set(starters)) != 11 or not all(starters):
            row_errors.append("starters_not_11_unique_nonempty")
        if kickoff is None:
            row_errors.append("missing_or_invalid_kickoff_utc")
        if observed is None:
            row_errors.append("missing_or_invalid_source_observed_at_utc")
        else:
            timestamp_complete_rows += 1
        if label_type in PREDICTED_LABEL_TYPES:
            predicted_rows += 1
            row_errors.append("predicted_xi_in_observed_store")
        elif label_type not in OBSERVED_LABEL_TYPES:
            unknown_label_rows += 1
            row_errors.append("unknown_or_missing_label_type")
        if not source_name:
            row_errors.append("missing_source_name")
        if not source_url:
            row_errors.append("missing_source_url")
        if not namespace:
            row_errors.append("missing_player_id_namespace")

        if row_errors:
            errors.extend(f"row_{index}:{item}" for item in row_errors)
            continue
        safe_rows += 1
        fixtures.add(fixture_id)
        teams.add(team)
        seasons.add(season)
        duplicate_keys[(fixture_id, team, label_type)] += 1
        source_names[source_name] += 1
        namespaces[namespace] += 1

    duplicate_count = sum(count - 1 for count in duplicate_keys.values() if count > 1)
    if duplicate_count:
        errors.append(f"duplicate_team_fixture_rows:{duplicate_count}")

    sample_sufficient = (
        safe_rows >= MIN_OBSERVED_TEAM_MATCH_ROWS
        and len(fixtures) >= MIN_UNIQUE_FIXTURES
        and len(teams) >= MIN_UNIQUE_TEAMS
        and len(seasons) >= MIN_SEASONS
    )
    contract_pass = (
        not errors and predicted_rows == 0 and unknown_label_rows == 0 and duplicate_count == 0
    )
    trainable = sample_sufficient and contract_pass

    if not path.is_file():
        status = "NO_LINEUP_DATA_FILE"
    elif not rows:
        status = "EMPTY_LINEUP_DATA"
    elif not contract_pass:
        status = "LINEUP_DATA_CONTRACT_FAIL"
    elif not sample_sufficient:
        status = "LINEUP_LABELS_INSUFFICIENT_SAMPLE"
    else:
        status = "LINEUP_LABELS_READY_FOR_SHADOW_TRAINING"

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
        "player_id_namespaces": dict(namespaces),
        "sample_sufficient": sample_sufficient,
        "contract_pass": contract_pass,
        "trainable": trainable,
        "error_count": len(errors),
        "error_examples": errors[:25],
    }


def audit_availability(competition_id: str) -> dict[str, Any]:
    path = FOOTBALL / "availability" / competition_id / "historical_availability.jsonl"
    rows, parse_errors = read_jsonl(path)
    errors = list(parse_errors)
    safe_rows = 0
    post_freeze_rows = 0
    statuses: Counter[str] = Counter()
    fixtures: set[str] = set()
    players: set[str] = set()

    for index, row in enumerate(rows, 1):
        row_errors: list[str] = []
        if str(row.get("competition_id") or "") != competition_id:
            row_errors.append("competition_id_mismatch")
        fixture_id = str(row.get("fixture_id") or row.get("game_id") or "").strip()
        player_id = str(row.get("player_id") or "").strip()
        status = str(row.get("status") or "").strip().lower()
        freeze = parse_iso(row.get("freeze_time_utc"))
        observed = parse_iso(row.get("source_observed_at_utc"))
        if not fixture_id:
            row_errors.append("missing_fixture_id")
        if not player_id:
            row_errors.append("missing_player_id")
        if status not in AVAILABILITY_STATUSES:
            row_errors.append("invalid_status")
        if freeze is None:
            row_errors.append("missing_or_invalid_freeze_time")
        if observed is None:
            row_errors.append("missing_or_invalid_source_observed_at")
        if freeze and observed and observed > freeze:
            post_freeze_rows += 1
            row_errors.append("post_freeze_evidence")
        if not str(row.get("source_name") or row.get("source") or "").strip():
            row_errors.append("missing_source_name")
        if not str(row.get("source_url") or "").strip():
            row_errors.append("missing_source_url")
        if not str(row.get("player_id_namespace") or "").strip():
            row_errors.append("missing_player_id_namespace")
        if row_errors:
            errors.extend(f"row_{index}:{item}" for item in row_errors)
            continue
        safe_rows += 1
        fixtures.add(fixture_id)
        players.add(player_id)
        statuses[status] += 1

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
        "unique_player_count": len(players),
        "post_freeze_row_count": post_freeze_rows,
        "status_counts": dict(statuses),
        "error_count": len(errors),
        "error_examples": errors[:25],
    }


def audit_route(path: Path, *, version: str) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    kickoff_guard = "item[\"kickoff\"] <" in text
    observation_guard = "source_observed_at" in text and "< freeze" in text
    label_guard = "OBSERVED_LABEL_TYPES" in text and "non-observed label_type" in text
    exact_eleven_guard = "len(starters) != 11" in text
    safe = all((kickoff_guard, observation_guard, label_guard, exact_eleven_guard))
    return {
        "version": version,
        "path": path.relative_to(ROOT).as_posix(),
        "file_present": path.is_file(),
        "same_season_kickoff_guard": kickoff_guard,
        "source_observed_at_guard": observation_guard,
        "observed_vs_predicted_label_guard": label_guard,
        "exact_eleven_guard": exact_eleven_guard,
        "shadow_training_route_safe": safe,
    }


def audit() -> dict[str, Any]:
    registry = load_json(REGISTRY)
    competitions = [
        str(item["competition_id"])
        for item in registry.get("competitions", [])
        if isinstance(item, dict) and item.get("competition_id")
    ]
    legacy_route = audit_route(LEGACY_ROUTE, version="V4.6.2")
    shadow_route = audit_route(SHADOW_ROUTE, version="V5.0.2")

    reports: dict[str, Any] = {}
    lineup_domains: list[str] = []
    availability_domains: list[str] = []
    lineup_shadow_domains: list[str] = []
    full_domains: list[str] = []
    for competition_id in competitions:
        lineup = audit_lineups(competition_id)
        availability = audit_availability(competition_id)
        if lineup["trainable"]:
            lineup_domains.append(competition_id)
        if availability["safe_row_count"] and not availability["error_count"]:
            availability_domains.append(competition_id)
        if lineup["trainable"] and shadow_route["shadow_training_route_safe"]:
            lineup_shadow_domains.append(competition_id)
        if (
            lineup["trainable"]
            and shadow_route["shadow_training_route_safe"]
            and availability["safe_row_count"]
            and not availability["error_count"]
        ):
            full_domains.append(competition_id)

        if not lineup["file_present"]:
            domain_status = "BLOCKED_NO_ACQUIRED_LINEUP_DATA"
        elif not lineup["contract_pass"]:
            domain_status = "BLOCKED_LINEUP_CONTRACT_FAIL"
        elif not lineup["sample_sufficient"]:
            domain_status = "BLOCKED_INSUFFICIENT_LINEUP_SAMPLE"
        elif not shadow_route["shadow_training_route_safe"]:
            domain_status = "BLOCKED_UNSAFE_SHADOW_ROUTE"
        elif not availability["file_present"]:
            domain_status = "LINEUP_SHADOW_READY_AVAILABILITY_UNAVAILABLE"
        elif availability["error_count"]:
            domain_status = "BLOCKED_AVAILABILITY_CONTRACT_FAIL"
        else:
            domain_status = "READY_FOR_FULL_PLAYER_XI_SHADOW_TRAINING"
        reports[competition_id] = {
            "status": domain_status,
            "lineup": lineup,
            "availability": availability,
            "formal_weight": 0,
        }

    if full_domains:
        status = "PARTIAL_READY_FOR_FULL_PLAYER_XI_SHADOW_TRAINING"
    elif lineup_shadow_domains:
        status = "PARTIAL_READY_FOR_LINEUP_ONLY_SHADOW_TRAINING"
    elif lineup_domains:
        status = "LINEUP_LABELS_PRESENT_BUT_SHADOW_ROUTE_BLOCKED"
    else:
        status = "BLOCKED_NO_TRAINABLE_DOMAIN"

    return {
        "schema_version": "V5.0.2-player-xi-data-readiness-r2",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "registered_competition_count": len(competitions),
        "trainable_lineup_domain_count": len(lineup_domains),
        "availability_domain_count": len(availability_domains),
        "lineup_only_shadow_candidate_domain_count": len(lineup_shadow_domains),
        "full_shadow_candidate_domain_count": len(full_domains),
        "trainable_lineup_domains": lineup_domains,
        "availability_domains": availability_domains,
        "lineup_only_shadow_candidate_domains": lineup_shadow_domains,
        "full_shadow_candidate_domains": full_domains,
        "legacy_route_audit": legacy_route,
        "shadow_route_audit": shadow_route,
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
        "policy": "Readiness/leakage audit only. Lineup-only shadow training may proceed without availability, but full player-XI latent-strength training requires separately timestamped availability evidence.",
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
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.print_summary:
        print(json.dumps({
            "status": report["status"],
            "trainable_lineup_domain_count": report["trainable_lineup_domain_count"],
            "availability_domain_count": report["availability_domain_count"],
            "lineup_only_shadow_candidate_domain_count": report["lineup_only_shadow_candidate_domain_count"],
            "full_shadow_candidate_domain_count": report["full_shadow_candidate_domain_count"],
            "legacy_route_audit": report["legacy_route_audit"],
            "shadow_route_audit": report["shadow_route_audit"],
        }, ensure_ascii=False, indent=2))
    return 2 if args.strict_exit and report["status"] == "BLOCKED_NO_TRAINABLE_DOMAIN" else 0


if __name__ == "__main__":
    raise SystemExit(main())
