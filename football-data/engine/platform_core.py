#!/usr/bin/env python3
"""Shared utilities for the football data execution platform.

The formal football rules are intentionally not stored in this repository.
This module only validates data packages, computes descriptive features,
checks probability artifacts, and preserves immutable audit records.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "config" / "platform_registry.json"
TEAM_CONFIG_PATH = ROOT / "config" / "team_strength_config.json"
TEAM_ALIASES_PATH = ROOT / "config" / "team_aliases.json"

PROB_TOLERANCE = 1e-6
MARGINAL_TOLERANCE = 1e-5
EPSILON = 1e-15


class PlatformError(RuntimeError):
    """Raised when a hard data or audit gate fails."""


@dataclass(frozen=True)
class MatchRow:
    competition_id: str
    season: str
    stage: str
    date: datetime
    home_team: str
    away_team: str
    home_goals: int
    away_goals: int
    source_path: str

    @property
    def result(self) -> str:
        return outcome_from_score(self.home_goals, self.away_goals)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PlatformError(f"missing required file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PlatformError(f"invalid JSON: {path}: {exc}") from exc


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def parse_iso_datetime(value: str, field: str = "datetime") -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise PlatformError(f"{field} must be a non-empty ISO-8601 string")
    token = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(token)
    except ValueError as exc:
        raise PlatformError(f"invalid {field}: {value!r}") from exc
    if parsed.tzinfo is None:
        raise PlatformError(f"{field} must include a timezone offset: {value!r}")
    return parsed.astimezone(timezone.utc)


def _season_years(season: str) -> tuple[int | None, int | None]:
    token = str(season).strip()
    match = re.fullmatch(r"(20\d{2})/(\d{2})", token)
    if match:
        first = int(match.group(1))
        return first, first + 1
    match = re.fullmatch(r"(20\d{2})-(\d{2})", token)
    if match:
        first = int(match.group(1))
        return first, first + 1
    if re.fullmatch(r"20\d{2}", token):
        year = int(token)
        return year, year
    return None, None


def parse_match_date(value: str, season: str = "") -> datetime:
    """Parse the heterogeneous date formats used by the frozen sources.

    The returned value is UTC midnight. Text dates without a year are resolved
    from the competition season: July-December use the first season year and
    January-June use the second season year.
    """
    raw = str(value or "").strip()
    if not raw:
        raise PlatformError("match date is empty")
    if raw.startswith("line-"):
        raise PlatformError(f"synthetic line date cannot enter dynamic features: {raw}")

    formats = (
        "%d/%m/%Y",
        "%d/%m/%y",
        "%Y-%m-%d",
        "%Y.%m.%d",
        "%d.%m.%Y",
        "%a %b %d %Y",
        "%A %b %d %Y",
        "%b %d %Y",
    )
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    first_year, second_year = _season_years(season)
    text_without_year = re.sub(r"\s+", " ", raw)
    for fmt in ("%a %b %d", "%A %b %d", "%b %d"):
        try:
            partial = datetime.strptime(text_without_year + " 2000", fmt + " %Y")
        except ValueError:
            continue
        if first_year is None:
            raise PlatformError(f"date lacks year and season cannot resolve it: {raw!r}")
        year = first_year if partial.month >= 7 else (second_year or first_year)
        return partial.replace(year=year, tzinfo=timezone.utc)

    raise PlatformError(f"unsupported match date: {raw!r} season={season!r}")


def normalize_team_token(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    text = text.replace("&", " and ")
    text = re.sub(r"\b(fc|cf|afc|sc|ac|sv|fk|sk|club|football|calcio)\b", " ", text)
    text = re.sub(r"[^0-9a-z\u00c0-\u024f\u0370-\u03ff\u0400-\u04ff\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]+", "", text)
    return text


def load_aliases() -> dict[str, dict[str, str]]:
    if not TEAM_ALIASES_PATH.exists():
        return {}
    data = load_json(TEAM_ALIASES_PATH)
    aliases = data.get("competitions", {})
    if not isinstance(aliases, dict):
        raise PlatformError("team_aliases.json competitions must be an object")
    return aliases


def canonical_team_name(competition_id: str, raw_name: str, aliases: dict[str, dict[str, str]] | None = None) -> str:
    aliases = aliases if aliases is not None else load_aliases()
    mapping = aliases.get(competition_id, {})
    if raw_name in mapping:
        return mapping[raw_name]
    normalized_lookup = {normalize_team_token(key): value for key, value in mapping.items()}
    return normalized_lookup.get(normalize_team_token(raw_name), str(raw_name).strip())


def stable_team_id(competition_id: str, canonical_name: str) -> str:
    token = f"{competition_id}|{normalize_team_token(canonical_name)}".encode("utf-8")
    return f"team_{hashlib.sha256(token).hexdigest()[:16]}"


def load_registry() -> dict[str, Any]:
    registry = load_json(REGISTRY_PATH)
    competitions = registry.get("competitions")
    if not isinstance(competitions, list):
        raise PlatformError("platform registry competitions must be a list")
    ids = [item.get("competition_id") for item in competitions]
    if len(ids) != len(set(ids)):
        raise PlatformError("duplicate competition_id in platform registry")
    return registry


def registry_map() -> dict[str, dict[str, Any]]:
    return {item["competition_id"]: item for item in load_registry()["competitions"]}


def outcome_from_score(home_goals: int, away_goals: int) -> str:
    return "home" if home_goals > away_goals else "draw" if home_goals == away_goals else "away"


def result_code_from_score(home_goals: int, away_goals: int) -> str:
    return "H" if home_goals > away_goals else "D" if home_goals == away_goals else "A"


def normalize_probability(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PlatformError(f"{field} is not numeric: {value!r}") from exc
    if not math.isfinite(number) or number < 0 or number > 1:
        raise PlatformError(f"{field} must be between 0 and 1: {number}")
    return number


def validate_probability_vector(
    mapping: dict[str, Any],
    keys: Iterable[str],
    *,
    field: str,
    tolerance: float = PROB_TOLERANCE,
) -> dict[str, float]:
    expected = list(keys)
    missing = [key for key in expected if key not in mapping]
    extra = [key for key in mapping if key not in expected]
    if missing or extra:
        raise PlatformError(f"{field} keys mismatch; missing={missing}, extra={extra}")
    normalized = {key: normalize_probability(mapping[key], f"{field}.{key}") for key in expected}
    total = sum(normalized.values())
    if abs(total - 1.0) > tolerance:
        raise PlatformError(f"{field} probabilities sum to {total:.12f}, not 1")
    return normalized


def log_score(probability: float) -> float:
    return -math.log(max(float(probability), EPSILON))


def multiclass_brier(probabilities: dict[str, float], actual: str) -> float:
    return sum((probabilities[key] - (1.0 if key == actual else 0.0)) ** 2 for key in probabilities)


def ranked_probability_score(probabilities: list[float], actual_index: int) -> float:
    if not probabilities:
        raise PlatformError("RPS probabilities cannot be empty")
    cumulative_p = 0.0
    cumulative_o = 0.0
    score = 0.0
    for index in range(len(probabilities) - 1):
        cumulative_p += probabilities[index]
        cumulative_o += 1.0 if actual_index == index else 0.0
        score += (cumulative_p - cumulative_o) ** 2
    return score / max(1, len(probabilities) - 1)


def split_quarter_line(line: float) -> tuple[float, ...]:
    doubled = round(line * 4)
    if abs(line * 4 - doubled) > 1e-8:
        raise PlatformError(f"Asian line must be in quarter increments: {line}")
    fraction = doubled % 4
    if fraction in (0, 2):
        return (line,)
    return (line - 0.25, line + 0.25)


def settle_home_handicap(home_goals: int, away_goals: int, line: float) -> dict[str, float]:
    outcomes = Counter()
    parts = split_quarter_line(float(line))
    weight = 1.0 / len(parts)
    margin = home_goals - away_goals
    for part in parts:
        adjusted = margin + part
        if adjusted > 1e-12:
            outcomes["win"] += weight
        elif adjusted < -1e-12:
            outcomes["loss"] += weight
        else:
            outcomes["push"] += weight
    return {key: float(outcomes[key]) for key in ("win", "push", "loss")}


def settle_over_total(home_goals: int, away_goals: int, line: float) -> dict[str, float]:
    outcomes = Counter()
    parts = split_quarter_line(float(line))
    weight = 1.0 / len(parts)
    total = home_goals + away_goals
    for part in parts:
        adjusted = total - part
        if adjusted > 1e-12:
            outcomes["win"] += weight
        elif adjusted < -1e-12:
            outcomes["loss"] += weight
        else:
            outcomes["push"] += weight
    return {key: float(outcomes[key]) for key in ("win", "push", "loss")}


def score_matrix_rows(matrix: Any) -> Iterator[tuple[int, int, float]]:
    if not isinstance(matrix, list) or not matrix:
        raise PlatformError("score_matrix must be a non-empty list")
    seen: set[tuple[int, int]] = set()
    for index, cell in enumerate(matrix):
        if not isinstance(cell, dict):
            raise PlatformError(f"score_matrix[{index}] must be an object")
        try:
            home = int(cell["home_goals"])
            away = int(cell["away_goals"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PlatformError(f"invalid score cell at index {index}") from exc
        if home < 0 or away < 0:
            raise PlatformError(f"negative score cell at index {index}")
        key = (home, away)
        if key in seen:
            raise PlatformError(f"duplicate score cell: {home}-{away}")
        seen.add(key)
        probability = normalize_probability(cell.get("probability"), f"score_matrix[{index}].probability")
        yield home, away, probability


def derive_score_marginals(matrix: Any) -> dict[str, Any]:
    outcome = Counter({"home": 0.0, "draw": 0.0, "away": 0.0})
    totals = Counter({str(value): 0.0 for value in range(7)})
    totals["7+"] = 0.0
    btts_yes = 0.0
    probability_sum = 0.0
    score_probabilities: dict[str, float] = {}
    for home, away, probability in score_matrix_rows(matrix):
        probability_sum += probability
        outcome[outcome_from_score(home, away)] += probability
        total_key = str(home + away) if home + away <= 6 else "7+"
        totals[total_key] += probability
        btts_yes += probability if home > 0 and away > 0 else 0.0
        score_probabilities[f"{home}-{away}"] = probability
    return {
        "probability_sum": probability_sum,
        "1x2": {key: float(outcome[key]) for key in ("home", "draw", "away")},
        "total_goals": {key: float(totals[key]) for key in ("0", "1", "2", "3", "4", "5", "6", "7+")},
        "btts_yes": btts_yes,
        "score_probabilities": score_probabilities,
    }


def compare_marginals(observed: dict[str, float], expected: dict[str, float], tolerance: float = MARGINAL_TOLERANCE) -> dict[str, Any]:
    residuals = {key: observed[key] - expected[key] for key in expected}
    maximum = max((abs(value) for value in residuals.values()), default=0.0)
    return {"residuals": residuals, "max_abs_residual": maximum, "passed": maximum <= tolerance}


def _competition_from_row(row: dict[str, str], path: Path) -> str:
    return (
        row.get("competition_id")
        or row.get("league_id")
        or path.parent.name
    )


def read_processed_matches(competition_id: str, root: Path = ROOT) -> list[MatchRow]:
    directory = root / "processed" / competition_id
    if not directory.exists():
        raise PlatformError(f"processed competition directory missing: {directory}")
    aliases = load_aliases()
    rows: list[MatchRow] = []
    seen: set[tuple[str, str, str, str]] = set()
    errors: list[str] = []
    for path in sorted(directory.glob("*.csv")):
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for line_no, raw in enumerate(reader, start=2):
                row = {str(key).strip(): "" if value is None else str(value).strip() for key, value in raw.items() if key}
                if not row.get("HomeTeam") or not row.get("AwayTeam"):
                    continue
                if row.get("FTHG", "") == "" or row.get("FTAG", "") == "":
                    continue
                season = row.get("season") or row.get("Season") or ""
                try:
                    date = parse_match_date(row.get("Date", ""), season)
                    home_goals = int(float(row["FTHG"]))
                    away_goals = int(float(row["FTAG"]))
                except (PlatformError, ValueError) as exc:
                    errors.append(f"{path.name}:{line_no}: {exc}")
                    continue
                if home_goals < 0 or away_goals < 0:
                    raise PlatformError(f"negative goals in {path}:{line_no}")
                canonical_competition = _competition_from_row(row, path)
                if canonical_competition != competition_id:
                    raise PlatformError(
                        f"competition mismatch in {path}:{line_no}: {canonical_competition} != {competition_id}"
                    )
                home_team = canonical_team_name(competition_id, row["HomeTeam"], aliases)
                away_team = canonical_team_name(competition_id, row["AwayTeam"], aliases)
                key = (season, date.date().isoformat(), normalize_team_token(home_team), normalize_team_token(away_team))
                if key in seen:
                    raise PlatformError(f"duplicate processed match across files: {key}")
                seen.add(key)
                rows.append(
                    MatchRow(
                        competition_id=competition_id,
                        season=season,
                        stage=row.get("stage") or "stage_unverified",
                        date=date,
                        home_team=home_team,
                        away_team=away_team,
                        home_goals=home_goals,
                        away_goals=away_goals,
                        source_path=str(path.relative_to(root)),
                    )
                )
    if not rows:
        detail = "; ".join(errors[:5])
        raise PlatformError(f"no usable processed matches for {competition_id}; {detail}")
    rows.sort(key=lambda item: (item.date, item.home_team, item.away_team))
    return rows


def top_scores(matrix: Any, limit: int = 5) -> list[dict[str, Any]]:
    items = [
        {"score": f"{home}-{away}", "probability": probability}
        for home, away, probability in score_matrix_rows(matrix)
    ]
    items.sort(key=lambda item: (-item["probability"], item["score"]))
    return items[:limit]


def implied_probability(decimal_odds: float) -> float:
    try:
        odds = float(decimal_odds)
    except (TypeError, ValueError) as exc:
        raise PlatformError(f"invalid decimal odds: {decimal_odds!r}") from exc
    if not math.isfinite(odds) or odds <= 1.0:
        raise PlatformError(f"decimal odds must be greater than 1: {odds}")
    return 1.0 / odds


def expected_value(probability: float, decimal_odds: float) -> float:
    p = normalize_probability(probability, "probability")
    odds = float(decimal_odds)
    if odds <= 1.0:
        raise PlatformError("decimal odds must be greater than 1")
    return p * odds - 1.0
