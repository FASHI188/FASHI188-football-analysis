#!/usr/bin/env python3
"""Ingest and profile football competition batch 002.

Formal football rules remain in the ChatGPT project.  This module only freezes
source data, normalizes completed 90-minute results, builds descriptive
competition profiles and records reproducibility metadata.

Batch 002:
- J1 / Brazil Serie A / Argentina top flight / MLS from Football-Data archives
- UEFA Champions League main tournament from OpenFootball CC0 text files
- K League 1 remains explicitly pending until the official-results crawler is
  independently implemented and validated
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import io
import json
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "league_sources_batch_002.json"
USER_AGENT = "HHH1-football-data/2.0 (+private research archive)"

CORE_PATH = Path(__file__).with_name("ingest_batch_001.py")
CORE_SPEC = importlib.util.spec_from_file_location("ingest_batch_001_for_batch_002", CORE_PATH)
CORE = importlib.util.module_from_spec(CORE_SPEC)
assert CORE_SPEC.loader is not None
sys.modules[CORE_SPEC.name] = CORE
CORE_SPEC.loader.exec_module(CORE)

ALIASES = {
    "Home": "HomeTeam",
    "Home Team": "HomeTeam",
    "Away": "AwayTeam",
    "Away Team": "AwayTeam",
    "HG": "FTHG",
    "AG": "FTAG",
    "Res": "FTR",
    "Result": "FTR",
}
REQUIRED_FIELDS = ("Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG")
ET_MARKERS = ("a.e.t", "aet", "pen.", " pens", "penalties")


class DataError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourcePayload:
    competition_id: str
    source_id: str
    url: str
    content: bytes
    source_type: str
    season: str | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def download(url: str, timeout: int = 60) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content = response.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        raise DataError(f"download failed {url}: {exc}") from exc
    if not content:
        raise DataError(f"empty response: {url}")
    prefix = content[:512].lower()
    if b"<html" in prefix or b"<!doctype html" in prefix:
        raise DataError(f"received HTML instead of data: {url}")
    return content


def decode_text(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise DataError("source encoding could not be decoded")


def norm_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def canonicalize_row(raw: dict[str, Any]) -> dict[str, str]:
    row: dict[str, str] = {}
    for key, value in raw.items():
        if key is None:
            continue
        clean_key = str(key).strip()
        mapped = ALIASES.get(clean_key, clean_key)
        clean_value = "" if value is None else str(value).strip()
        if mapped not in row or not row[mapped]:
            row[mapped] = clean_value
    return row


def normalize_int(value: str, field: str) -> int:
    try:
        number = int(float(value.strip()))
    except (TypeError, ValueError) as exc:
        raise DataError(f"invalid {field}: {value!r}") from exc
    if number < 0:
        raise DataError(f"negative {field}: {number}")
    return number


def normalize_result(home_goals: int, away_goals: int) -> str:
    return "H" if home_goals > away_goals else "D" if home_goals == away_goals else "A"


def stage_for_extra(competition_id: str, project_season: str) -> str:
    if competition_id == "JPN_J1":
        return "special_100_year_vision_transition" if project_season == "2026" else "regular_league"
    if competition_id == "BRA_SerieA":
        return "regular_league"
    if competition_id == "ARG_Primera":
        return "top_flight_phase_unverified"
    if competition_id == "USA_MLS":
        return "regular_or_playoffs_unverified"
    return "stage_unverified"


def _candidate_match(value: str, candidates: list[str]) -> bool:
    normalized = norm_text(value)
    return any(normalized == norm_text(candidate) for candidate in candidates)


def parse_extra_archive(content: bytes, competition: dict[str, Any]) -> tuple[list[dict[str, str]], dict[str, Any]]:
    text = decode_text(content)
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise DataError(f"{competition['competition_id']}: missing CSV header")

    fields = [ALIASES.get(str(f).strip(), str(f).strip()) for f in reader.fieldnames if f is not None]
    missing = [field for field in REQUIRED_FIELDS if field not in fields]
    if missing:
        raise DataError(
            f"{competition['competition_id']}: missing required fields {missing}; "
            f"observed={fields[:30]}"
        )

    allowed_seasons = set(competition["allowed_source_seasons"])
    season_map = competition["season_label_map"]
    league_candidates = competition.get("league_candidates", [])
    country_candidates = competition.get("country_candidates", [])
    observed_leagues: Counter[str] = Counter()
    observed_countries: Counter[str] = Counter()
    observed_seasons: Counter[str] = Counter()
    selected: list[dict[str, str]] = []
    incomplete_rows = 0

    for raw in reader:
        row = canonicalize_row(raw)
        source_season = row.get("Season", "")
        league = row.get("League", "")
        country = row.get("Country", "")
        if source_season:
            observed_seasons[source_season] += 1
        if league:
            observed_leagues[league] += 1
        if country:
            observed_countries[country] += 1

        if source_season not in allowed_seasons:
            continue
        if league_candidates and not _candidate_match(league, league_candidates):
            continue
        if country_candidates and country and not _candidate_match(country, country_candidates):
            continue
        if not row.get("HomeTeam") or not row.get("AwayTeam"):
            incomplete_rows += 1
            continue
        if row.get("FTHG", "") == "" or row.get("FTAG", "") == "":
            incomplete_rows += 1
            continue

        home_goals = normalize_int(row["FTHG"], "FTHG")
        away_goals = normalize_int(row["FTAG"], "FTAG")
        expected = normalize_result(home_goals, away_goals)
        supplied = row.get("FTR", "")
        if supplied and supplied not in {"H", "D", "A"}:
            supplied = ""
        if supplied and supplied != expected:
            raise DataError(
                f"{competition['competition_id']} result mismatch: "
                f"{row.get('Date')} {row['HomeTeam']} {home_goals}-{away_goals} "
                f"{row['AwayTeam']} source={supplied} expected={expected}"
            )

        project_season = season_map[source_season]
        row["competition_id"] = competition["competition_id"]
        row["season"] = project_season
        row["source_season"] = source_season
        row["source_code"] = competition["source_code"]
        row["stage"] = stage_for_extra(competition["competition_id"], project_season)
        row["FTHG"] = str(home_goals)
        row["FTAG"] = str(away_goals)
        row["FTR"] = expected
        selected.append(row)

    if not selected:
        raise DataError(
            f"{competition['competition_id']}: no matching completed rows; "
            f"observed leagues={dict(observed_leagues.most_common(20))}; "
            f"countries={dict(observed_countries.most_common(10))}; "
            f"seasons={dict(observed_seasons.most_common(20))}"
        )

    seen: set[tuple[str, str, str, str]] = set()
    for row in selected:
        key = (row["season"], row.get("Date", ""), row["HomeTeam"], row["AwayTeam"])
        if key in seen:
            raise DataError(f"{competition['competition_id']}: duplicate match key {key}")
        seen.add(key)

    per_season = Counter(row["season"] for row in selected)
    audit = {
        "selected_rows": len(selected),
        "incomplete_rows_skipped": incomplete_rows,
        "rows_by_project_season": dict(sorted(per_season.items())),
        "observed_leagues": dict(observed_leagues.most_common(20)),
        "observed_countries": dict(observed_countries.most_common(10)),
        "observed_source_seasons": dict(sorted(observed_seasons.items())),
        "stage_policy": competition["stage_policy"],
    }
    return selected, audit


UCL_MATCH_RE = re.compile(
    r"^\s*(?:(?P<time>\d{1,2}:\d{2})\s+)?"
    r"(?P<home>.+?)\s+v\s+(?P<away>.+?)\s+"
    r"(?P<hg>\d+)-(?P<ag>\d+)"
    r"(?:\s+\((?P<hthg>\d+)-(?P<htag>\d+)\))?\s*$"
)


def ucl_stage_from_heading(heading: str, season: str) -> str:
    normalized = norm_text(heading)
    if normalized.startswith("league"):
        return "league_phase"
    if normalized.startswith("group"):
        return "group_stage"
    if "playoff" in normalized or "play-off" in normalized:
        return "knockout_playoff"
    if "round of 16" in normalized or "last 16" in normalized:
        return "round_of_16"
    if "quarter" in normalized:
        return "quarterfinal"
    if "semi" in normalized:
        return "semifinal"
    if "final" in normalized:
        return "final"
    return "stage_unverified"


def parse_ucl_text(content: bytes, source_season: str, project_season: str) -> tuple[list[dict[str, str]], dict[str, Any]]:
    text = decode_text(content)
    rows: list[dict[str, str]] = []
    current_stage = "stage_unverified"
    current_date = ""
    ambiguous_lines: list[str] = []
    nonmatch_score_lines: list[str] = []

    for line_no, original in enumerate(text.splitlines(), start=1):
        stripped = original.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("="):
            continue
        if stripped.startswith("▪"):
            current_stage = ucl_stage_from_heading(stripped.lstrip("▪ "), project_season)
            continue
        if re.match(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b", stripped):
            current_date = stripped
            continue
        lower = stripped.casefold()
        if " v " not in lower:
            continue
        if any(marker in lower for marker in ET_MARKERS):
            ambiguous_lines.append(stripped)
            continue

        match = UCL_MATCH_RE.match(original)
        if not match:
            nonmatch_score_lines.append(stripped)
            continue

        home_goals = int(match.group("hg"))
        away_goals = int(match.group("ag"))
        row = {
            "competition_id": "UEFA_ChampionsLeague",
            "season": project_season,
            "source_season": source_season,
            "stage": current_stage,
            "source_code": "openfootball/champions-league",
            "Date": current_date or f"line-{line_no}",
            "Time": match.group("time") or "",
            "HomeTeam": match.group("home").strip(),
            "AwayTeam": match.group("away").strip(),
            "FTHG": str(home_goals),
            "FTAG": str(away_goals),
            "FTR": normalize_result(home_goals, away_goals),
            "HTHG": match.group("hthg") or "",
            "HTAG": match.group("htag") or "",
        }
        rows.append(row)

    if not rows:
        raise DataError(f"UCL {project_season}: no 90-minute-safe match rows parsed")

    seen: set[tuple[str, str, str, str]] = set()
    for row in rows:
        key = (row["season"], row["Date"], row["HomeTeam"], row["AwayTeam"])
        if key in seen:
            raise DataError(f"UCL {project_season}: duplicate match key {key}")
        seen.add(key)

    audit = {
        "parsed_90min_safe_rows": len(rows),
        "ambiguous_extra_time_or_penalty_lines_excluded": ambiguous_lines,
        "unparsed_lines_containing_v": nonmatch_score_lines,
        "stage_counts": dict(Counter(row["stage"] for row in rows)),
    }
    return rows, audit


def union_fieldnames(rows: list[dict[str, str]]) -> list[str]:
    fixed = [
        "competition_id", "season", "source_season", "stage", "source_code",
        "Date", "Time", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR",
        "HTHG", "HTAG", "HTR", "Country", "League",
    ]
    present = set().union(*(row.keys() for row in rows)) if rows else set()
    ordered = [field for field in fixed if field in present]
    ordered.extend(sorted(present - set(ordered)))
    return ordered


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=union_fieldnames(rows), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def enhance_profile(profile: dict[str, Any], competition: dict[str, Any], audit: dict[str, Any]) -> dict[str, Any]:
    profile["competition_id"] = profile.pop("league_id", competition["competition_id"])
    profile["profile_status"] = (
        "historical_descriptive_profile_90min_safe_subset"
        if competition["competition_id"] == "UEFA_ChampionsLeague"
        else "historical_descriptive_profile_only"
    )
    profile["competition_policy"] = competition["stage_policy"]
    profile["ingestion_audit"] = audit
    profile["usage_gate"] = {
        "may_supply_competition_prior": True,
        "may_override_question_time_market": False,
        "may_supply_exact_score_without_joint_matrix": False,
        "requires_post_calculation_integrity_check": True,
    }
    if competition["competition_id"] in {"ARG_Primera", "USA_MLS"}:
        profile["stage_warning"] = "Stage labels are not fully verified; stage-specific calibration is disabled."
    elif competition["competition_id"] == "JPN_J1":
        profile["stage_warning"] = "The 2026 transition tournament is isolated from ordinary J1 seasons."
    elif competition["competition_id"] == "UEFA_ChampionsLeague":
        profile["stage_warning"] = "Group-stage and league-phase eras remain explicit; ambiguous ET/penalty rows are excluded."
    else:
        profile["stage_warning"] = None
    return profile


def validate_profile(profile: dict[str, Any], tolerance: float) -> None:
    if abs(sum(profile["result_distribution"].values()) - 1.0) > tolerance:
        raise DataError(f"result probability conservation failed: {profile.get('competition_id')}")
    if abs(sum(profile["total_goals_0_7plus"].values()) - 1.0) > tolerance:
        raise DataError(f"total-goal probability conservation failed: {profile.get('competition_id')}")


def process_extra(
    competition: dict[str, Any], config: dict[str, Any], staging: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    url = config["football_data_extra_url_template"].format(source_code=competition["source_code"])
    content = download(url)
    rows, audit = parse_extra_archive(content, competition)

    raw_rel = Path("raw") / competition["competition_id"] / "upstream_archive.csv"
    processed_rel = Path("processed") / competition["competition_id"] / "recent_seasons.csv"
    profile_rel = Path("league_profiles") / competition["competition_id"] / "profile.json"
    write_bytes(staging / raw_rel, content)
    write_csv(staging / processed_rel, rows)

    source_record = {
        "competition_id": competition["competition_id"],
        "source_type": competition["source_type"],
        "source_code": competition["source_code"],
        "url": url,
        "downloaded_at_utc": utc_now(),
        "raw_sha256": sha256_bytes(content),
        "raw_path": str(raw_rel),
        "processed_path": str(processed_rel),
        "selected_rows": len(rows),
        "validated": True,
    }
    profile = CORE.build_profile(competition["competition_id"], rows, [source_record])
    profile = enhance_profile(profile, competition, audit)
    validate_profile(profile, config["hard_checks"]["probability_conservation_tolerance"])
    (staging / profile_rel).parent.mkdir(parents=True, exist_ok=True)
    (staging / profile_rel).write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return source_record, profile


def process_ucl(
    competition: dict[str, Any], config: dict[str, Any], staging: Path
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    all_rows: list[dict[str, str]] = []
    source_records: list[dict[str, Any]] = []
    per_season_audit: dict[str, Any] = {}

    for source_season in competition["seasons"]:
        project_season = competition["season_label_map"][source_season]
        url = competition["raw_url_template"].format(season=source_season)
        content = download(url)
        rows, audit = parse_ucl_text(content, source_season, project_season)
        all_rows.extend(rows)
        per_season_audit[project_season] = audit
        raw_rel = Path("raw") / competition["competition_id"] / f"{source_season}.txt"
        write_bytes(staging / raw_rel, content)
        source_records.append(
            {
                "competition_id": competition["competition_id"],
                "season": project_season,
                "source_type": competition["source_type"],
                "repository": competition["repository"],
                "url": url,
                "downloaded_at_utc": utc_now(),
                "raw_sha256": sha256_bytes(content),
                "raw_path": str(raw_rel),
                "parsed_rows": len(rows),
                "validated": True,
            }
        )

    seen: set[tuple[str, str, str, str]] = set()
    for row in all_rows:
        key = (row["season"], row["Date"], row["HomeTeam"], row["AwayTeam"])
        if key in seen:
            raise DataError(f"UCL duplicate match across archive: {key}")
        seen.add(key)

    processed_rel = Path("processed") / competition["competition_id"] / "main_tournament_90min_safe.csv"
    profile_rel = Path("league_profiles") / competition["competition_id"] / "profile.json"
    write_csv(staging / processed_rel, all_rows)
    for record in source_records:
        record["processed_path"] = str(processed_rel)

    audit = {
        "include_qualifiers": competition["include_qualifiers"],
        "per_season": per_season_audit,
        "total_90min_safe_rows": len(all_rows),
        "format_eras": {
            "group_stage": ["2021/22", "2022/23", "2023/24"],
            "league_phase": ["2024/25", "2025/26"],
        },
    }
    profile = CORE.build_profile(competition["competition_id"], all_rows, source_records)
    profile = enhance_profile(profile, competition, audit)
    validate_profile(profile, config["hard_checks"]["probability_conservation_tolerance"])
    (staging / profile_rel).parent.mkdir(parents=True, exist_ok=True)
    (staging / profile_rel).write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return source_records, profile


def replace_generated_tree(staging: Path) -> None:
    for dirname in ("raw", "processed", "league_profiles"):
        source = staging / dirname
        if not source.exists():
            continue
        for competition_dir in source.iterdir():
            destination = ROOT / dirname / competition_dir.name
            if destination.exists():
                shutil.rmtree(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(competition_dir, destination)


def run() -> dict[str, Any]:
    config = load_config()
    started = utc_now()
    source_records: list[dict[str, Any]] = []
    profile_summary: dict[str, Any] = {}
    pending: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="football-batch-002-") as temp_dir:
        staging = Path(temp_dir)
        for competition in config["competitions"]:
            source_type = competition["source_type"]
            if source_type == "football_data_extra_archive":
                record, profile = process_extra(competition, config, staging)
                source_records.append(record)
                profile_summary[competition["competition_id"]] = {
                    "matches": profile["matches"],
                    "seasons": profile["seasons"],
                    "profile_status": profile["profile_status"],
                }
            elif source_type == "openfootball_champions_league":
                records, profile = process_ucl(competition, config, staging)
                source_records.extend(records)
                profile_summary[competition["competition_id"]] = {
                    "matches": profile["matches"],
                    "seasons": profile["seasons"],
                    "profile_status": profile["profile_status"],
                }
            elif source_type == "official_kleague_crawler_pending":
                pending.append(
                    {
                        "competition_id": competition["competition_id"],
                        "status": competition["status"],
                        "reason": "Official K League crawler is not yet implemented and independently validated.",
                        "official_schedule_url": competition["official_schedule_url"],
                        "seasons_requested": competition["seasons"],
                    }
                )
            else:
                raise DataError(f"unsupported source_type: {source_type}")

        if len(profile_summary) < 5:
            raise DataError(f"only {len(profile_summary)} competition profiles were generated; expected at least 5")
        replace_generated_tree(staging)

    manifest = {
        "schema_version": "1.0",
        "batch_id": config["batch_id"],
        "started_at_utc": started,
        "completed_at_utc": utc_now(),
        "source_registry_sha256": hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest(),
        "summary": {
            "requested_competitions": len(config["competitions"]),
            "processed_competitions": len(profile_summary),
            "pending_competitions": len(pending),
            "source_files": len(source_records),
            "processed_matches": sum(item["matches"] for item in profile_summary.values()),
        },
        "profiles": profile_summary,
        "sources": source_records,
        "pending": pending,
        "limitations": [
            "K League 1 remains unavailable until the official crawler passes reproducibility and completeness tests.",
            "J1 2026 is a transition tournament and is isolated from ordinary J1 seasons.",
            "Argentina and MLS stage labels are not fully identified from the archive source; stage-specific calibration is disabled.",
            "UCL qualifiers are excluded; ambiguous extra-time or penalty rows are excluded from the 90-minute-safe profile.",
            "All profiles are descriptive priors and cannot override question-time market, lineup and injury evidence.",
        ],
    }
    manifest_path = ROOT / "manifests" / "latest_ingestion_batch_002.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    try:
        manifest = run()
    except (DataError, CORE.DataError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.print_summary:
        print(json.dumps(manifest["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
