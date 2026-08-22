#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

class Stage4AcquisitionError(ValueError):
    pass

EXPECTED_SEASON_YEAR = "26/27"
MIN_LEAD = timedelta(hours=1)
MAX_LEAD = timedelta(days=14)
MIN_TOTAL_TARGETS = 20
COMPETITIONS: dict[str, dict[str, int | str]] = {
    "ENG_PremierLeague": {"tournament_id": 17, "season_id": 96668, "season_year": EXPECTED_SEASON_YEAR},
    "ESP_LaLiga": {"tournament_id": 8, "season_id": 97268, "season_year": EXPECTED_SEASON_YEAR},
    "GER_Bundesliga": {"tournament_id": 35, "season_id": 97464, "season_year": EXPECTED_SEASON_YEAR},
    "ITA_SerieA": {"tournament_id": 23, "season_id": 95836, "season_year": EXPECTED_SEASON_YEAR},
    "FRA_Ligue1": {"tournament_id": 34, "season_id": 96127, "season_year": EXPECTED_SEASON_YEAR},
}

TOP_LEVEL_KEYS = frozenset({"events", "hasNextPage"})
EVENT_KEYS = frozenset({"id", "status", "startTimestamp", "tournament", "season", "homeTeam", "awayTeam"})
STATUS_KEYS = frozenset({"type"})
TOURNAMENT_KEYS = frozenset({"uniqueTournament"})
UNIQUE_TOURNAMENT_KEYS = frozenset({"id"})
SEASON_KEYS = frozenset({"id", "year"})
TEAM_KEYS = frozenset({"id", "name"})

@dataclass(frozen=True)
class FixtureProjectionObservation:
    payload: dict[str, Any]
    source_identity: str
    source_url: str
    payload_sha256: str
    received_at: datetime

def iso_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise Stage4AcquisitionError("timestamp must include timezone")
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise Stage4AcquisitionError(f"{field} must be positive integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise Stage4AcquisitionError(f"{field} must be positive integer") from exc
    if number <= 0:
        raise Stage4AcquisitionError(f"{field} must be positive integer")
    return number

def nonempty(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise Stage4AcquisitionError(f"{field} must be non-empty")
    return text

def sha256_hex(value: Any, field: str) -> str:
    text = str(value or "")
    if len(text) != 64 or any(c not in "0123456789abcdef" for c in text):
        raise Stage4AcquisitionError(f"{field} must be 64 lowercase hex")
    return text

def exact_keys(obj: Any, allowed: frozenset[str], path: str) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise Stage4AcquisitionError(f"{path} must be object")
    keys = set(obj)
    if keys != set(allowed):
        missing = sorted(set(allowed) - keys)
        unknown = sorted(keys - set(allowed))
        raise Stage4AcquisitionError(f"{path} exact-key contract failed missing={missing} unknown={unknown}")
    return obj

def validate_fixture_projection(payload: dict[str, Any]) -> None:
    root = exact_keys(payload, TOP_LEVEL_KEYS, "$")
    if not isinstance(root["events"], list):
        raise Stage4AcquisitionError("$.events must be list")
    if not isinstance(root["hasNextPage"], bool):
        raise Stage4AcquisitionError("$.hasNextPage must be boolean")
    for i, raw in enumerate(root["events"]):
        p = f"$.events[{i}]"
        event = exact_keys(raw, EVENT_KEYS, p)
        status = exact_keys(event["status"], STATUS_KEYS, p + ".status")
        tournament = exact_keys(event["tournament"], TOURNAMENT_KEYS, p + ".tournament")
        unique = exact_keys(tournament["uniqueTournament"], UNIQUE_TOURNAMENT_KEYS, p + ".tournament.uniqueTournament")
        season = exact_keys(event["season"], SEASON_KEYS, p + ".season")
        home = exact_keys(event["homeTeam"], TEAM_KEYS, p + ".homeTeam")
        away = exact_keys(event["awayTeam"], TEAM_KEYS, p + ".awayTeam")
        nonempty(status["type"], p + ".status.type")
        positive_int(unique["id"], p + ".tournament.uniqueTournament.id")
        positive_int(season["id"], p + ".season.id")
        nonempty(season["year"], p + ".season.year")
        positive_int(home["id"], p + ".homeTeam.id")
        nonempty(home["name"], p + ".homeTeam.name")
        positive_int(away["id"], p + ".awayTeam.id")
        nonempty(away["name"], p + ".awayTeam.name")
        positive_int(event["id"], p + ".id")
        positive_int(event["startTimestamp"], p + ".startTimestamp")

def _event_identity(
    event: dict[str, Any], competition_id: str, spec: dict[str, int | str], observed_at: datetime
) -> dict[str, Any] | None:
    exact_keys(event, EVENT_KEYS, "event")
    status = exact_keys(event["status"], STATUS_KEYS, "event.status")
    if str(status["type"]) != "notstarted":
        raise Stage4AcquisitionError("event status must be exactly notstarted")
    tournament = exact_keys(event["tournament"], TOURNAMENT_KEYS, "event.tournament")
    unique = exact_keys(tournament["uniqueTournament"], UNIQUE_TOURNAMENT_KEYS, "event.tournament.uniqueTournament")
    returned_tid = positive_int(unique["id"], "uniqueTournament.id")
    if returned_tid != int(spec["tournament_id"]):
        raise Stage4AcquisitionError("unique tournament mismatch")
    season = exact_keys(event["season"], SEASON_KEYS, "event.season")
    returned_sid = positive_int(season["id"], "season.id")
    if returned_sid != int(spec["season_id"]):
        raise Stage4AcquisitionError("season id mismatch")
    returned_year = nonempty(season["year"], "season.year")
    if returned_year != str(spec["season_year"]):
        raise Stage4AcquisitionError("season year mismatch")
    event_id = positive_int(event["id"], "event.id")
    kickoff = datetime.fromtimestamp(positive_int(event["startTimestamp"], "startTimestamp"), tz=timezone.utc)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise Stage4AcquisitionError("observed_at must be timezone-aware")
    lead = kickoff - observed_at.astimezone(timezone.utc)
    if lead < MIN_LEAD or lead > MAX_LEAD:
        return None
    home = exact_keys(event["homeTeam"], TEAM_KEYS, "event.homeTeam")
    away = exact_keys(event["awayTeam"], TEAM_KEYS, "event.awayTeam")
    home_id = positive_int(home["id"], "homeTeam.id")
    away_id = positive_int(away["id"], "awayTeam.id")
    if home_id == away_id:
        raise Stage4AcquisitionError("home and away team id collide")
    return {
        "competition_id": competition_id,
        "fixture_id": f"fixture-projection:{event_id}",
        "provider_event_id": event_id,
        "kickoff_at": kickoff,
        "prediction_cutoff": kickoff - timedelta(minutes=15),
        "home_team_id": f"fixture-projection:{home_id}",
        "away_team_id": f"fixture-projection:{away_id}",
        "home_team_name": nonempty(home["name"], "homeTeam.name"),
        "away_team_name": nonempty(away["name"], "awayTeam.name"),
        "provider_tournament_id": returned_tid,
        "provider_season_id": returned_sid,
        "provider_season_year": returned_year,
    }
