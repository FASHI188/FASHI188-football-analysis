#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


class Stage4AcquisitionError(ValueError):
    pass


SOFASCORE_ORIGIN = "https://www.sofascore.com"
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
FORBIDDEN_KEY_FRAGMENTS = ("score", "winner", "result")


@dataclass(frozen=True)
class HttpObservation:
    payload: dict[str, Any]
    request_url: str
    payload_sha256: str
    http_status: int
    content_type: str
    received_at: datetime
    byte_count: int


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


def fixture_url(tournament_id: int, season_id: int) -> str:
    tid = positive_int(tournament_id, "tournament_id")
    sid = positive_int(season_id, "season_id")
    return f"{SOFASCORE_ORIGIN}/api/v1/unique-tournament/{tid}/season/{sid}/events/next/0"


def statistics_url(event_id: int) -> str:
    eid = positive_int(event_id, "event_id")
    return f"{SOFASCORE_ORIGIN}/api/v1/event/{eid}/statistics"


def forbidden_paths(value: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            token = str(key).casefold().replace("_", "").replace("-", "")
            if any(fragment in token for fragment in FORBIDDEN_KEY_FRAGMENTS):
                found.append(f"{path}.{key}")
            found.extend(forbidden_paths(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(forbidden_paths(child, f"{path}[{index}]"))
    return found


def assert_zero_label_fixture_payload(payload: dict[str, Any]) -> None:
    forbidden = forbidden_paths(payload)
    if forbidden:
        raise Stage4AcquisitionError(
            f"zero-label boundary violation forbidden result fields: {forbidden[:8]}"
        )


def _nested_id(obj: Any, key: str, field: str) -> int:
    if not isinstance(obj, dict) or not isinstance(obj.get(key), dict):
        raise Stage4AcquisitionError(f"{field} object missing")
    return positive_int(obj[key].get("id"), f"{field}.id")


def _team_name(team: Any, field: str) -> str:
    if not isinstance(team, dict):
        raise Stage4AcquisitionError(f"{field} must be object")
    return nonempty(team.get("name"), f"{field}.name")


def _event_identity(
    event: dict[str, Any], competition_id: str, spec: dict[str, int | str], observed_at: datetime
) -> dict[str, Any] | None:
    if not isinstance(event, dict):
        raise Stage4AcquisitionError("event must be object")
    status = event.get("status")
    if not isinstance(status, dict) or str(status.get("type") or "") != "notstarted":
        raise Stage4AcquisitionError("events/next returned non-notstarted event")
    tournament = event.get("tournament")
    if not isinstance(tournament, dict) or not isinstance(tournament.get("uniqueTournament"), dict):
        raise Stage4AcquisitionError("event.tournament.uniqueTournament missing")
    returned_tid = positive_int(tournament["uniqueTournament"].get("id"), "uniqueTournament.id")
    if returned_tid != int(spec["tournament_id"]):
        raise Stage4AcquisitionError("unique tournament mismatch")
    season = event.get("season")
    if not isinstance(season, dict):
        raise Stage4AcquisitionError("event.season missing")
    returned_sid = positive_int(season.get("id"), "season.id")
    if returned_sid != int(spec["season_id"]):
        raise Stage4AcquisitionError("season id mismatch")
    returned_year = nonempty(season.get("year"), "season.year")
    if returned_year != str(spec["season_year"]):
        raise Stage4AcquisitionError("season year mismatch")
    event_id = positive_int(event.get("id"), "event.id")
    kickoff = datetime.fromtimestamp(positive_int(event.get("startTimestamp"), "startTimestamp"), tz=timezone.utc)
    lead = kickoff - observed_at
    if lead < MIN_LEAD or lead > MAX_LEAD:
        return None
    home = event.get("homeTeam")
    away = event.get("awayTeam")
    home_id = _nested_id(event, "homeTeam", "homeTeam")
    away_id = _nested_id(event, "awayTeam", "awayTeam")
    if home_id == away_id:
        raise Stage4AcquisitionError("home and away team id collide")
    return {
        "competition_id": competition_id,
        "fixture_id": f"sofascore:{event_id}",
        "provider_event_id": event_id,
        "kickoff_at": kickoff,
        "prediction_cutoff": kickoff - timedelta(minutes=15),
        "home_team_id": f"sofascore:{home_id}",
        "away_team_id": f"sofascore:{away_id}",
        "home_team_name": _team_name(home, "homeTeam"),
        "away_team_name": _team_name(away, "awayTeam"),
        "provider_tournament_id": returned_tid,
        "provider_season_id": returned_sid,
        "provider_season_year": returned_year,
    }


def extract_expected_goals_statistics(payload: dict[str, Any]) -> tuple[float, float]:
    if not isinstance(payload, dict):
        raise Stage4AcquisitionError("statistics payload must be object")
    assert_zero_label_fixture_payload(payload)
    periods = payload.get("statistics")
    if not isinstance(periods, list):
        raise Stage4AcquisitionError("statistics array missing")
    matches: list[tuple[float, float]] = []
    for period in periods:
        if not isinstance(period, dict) or str(period.get("period") or "") != "ALL":
            continue
        for group in period.get("groups") if isinstance(period.get("groups"), list) else []:
            if not isinstance(group, dict):
                continue
            for item in group.get("statisticsItems") if isinstance(group.get("statisticsItems"), list) else []:
                if not isinstance(item, dict) or str(item.get("key") or "") != "expectedGoals":
                    continue
                try:
                    home = float(item["homeValue"])
                    away = float(item["awayValue"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise Stage4AcquisitionError("expectedGoals values must be numeric") from exc
                if home < 0.0 or away < 0.0:
                    raise Stage4AcquisitionError("expectedGoals values must be nonnegative")
                matches.append((home, away))
    if len(matches) != 1:
        raise Stage4AcquisitionError(f"expectedGoals must resolve exactly once; found={len(matches)}")
    return matches[0]
