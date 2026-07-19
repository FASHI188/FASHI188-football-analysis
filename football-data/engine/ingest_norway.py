#!/usr/bin/env python3
"""Ingest the Norwegian Eliteserien result archive with explicit season gates.

Historical odds columns are preserved as source fields but are never promoted to
question-time market snapshots because they lack the required synchronized
original quote timestamp. Formal single-match prices are always collected at the
user's actual question-time freeze.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import tempfile
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "league_source_norway.json"
RAW_PATH = ROOT / "raw" / "NOR_Eliteserien" / "full_archive_through_2026.csv"
PROCESSED_PATH = ROOT / "processed" / "NOR_Eliteserien" / "full_archive_through_2026.csv"
PROFILE_PATH = ROOT / "league_profiles" / "NOR_Eliteserien" / "profile.json"
MANIFEST_PATH = ROOT / "manifests" / "latest_norway_ingestion.json"
USER_AGENT = "HHH1-football-data/1.0 (+private research archive)"
REQUIRED = ("Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR")


class DataError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def decode_csv(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise DataError("CSV encoding could not be decoded")


def download(url: str, timeout: int = 60) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content = response.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        raise DataError(f"download failed: {exc}") from exc
    if not content:
        raise DataError("download returned an empty response")
    prefix = content[:512].lower()
    if b"<html" in prefix or b"<!doctype html" in prefix:
        raise DataError("received HTML instead of CSV")
    return content


def canonicalize_and_filter(content: bytes, config: dict[str, Any]) -> tuple[list[dict[str, str]], list[str], dict[str, Any]]:
    text = decode_csv(content)
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise DataError("missing CSV header")
    aliases = config["aliases"]
    original_fields = [str(field).strip() for field in reader.fieldnames if field is not None]
    canonical_fields: list[str] = []
    for field in original_fields:
        mapped = aliases.get(field, field)
        if mapped not in canonical_fields:
            canonical_fields.append(mapped)
    missing = [field for field in REQUIRED if field not in canonical_fields]
    if missing:
        raise DataError(f"unsupported Norway CSV schema; missing={missing}; fields={original_fields[:25]}")

    season_field = config["row_season_field"]
    allowed = set(config["allowed_source_seasons"])
    rows: list[dict[str, str]] = []
    observed_seasons: set[str] = set()
    raw_count = 0
    for raw in reader:
        raw_count += 1
        row: dict[str, str] = {}
        for key, value in raw.items():
            if key is None:
                continue
            mapped = aliases.get(str(key).strip(), str(key).strip())
            value_text = "" if value is None else str(value).strip()
            if mapped not in row or not row[mapped]:
                row[mapped] = value_text
        source_season = row.get(season_field, "")
        observed_seasons.add(source_season)
        if source_season not in allowed:
            continue
        if not row.get("HomeTeam") or not row.get("AwayTeam"):
            continue
        if row.get("FTHG", "") == "" or row.get("FTAG", "") == "":
            continue
        try:
            hg = int(float(row["FTHG"]))
            ag = int(float(row["FTAG"]))
        except ValueError as exc:
            raise DataError(f"invalid score row: {row.get('HomeTeam')} {row.get('FTHG')}-{row.get('FTAG')} {row.get('AwayTeam')}") from exc
        if hg < 0 or ag < 0:
            raise DataError("negative goals found")
        expected = "H" if hg > ag else "D" if hg == ag else "A"
        if row.get("FTR") and row["FTR"] != expected:
            raise DataError(f"result mismatch: {row.get('HomeTeam')} {hg}-{ag} {row.get('AwayTeam')} FTR={row.get('FTR')}")
        row["FTHG"] = str(hg)
        row["FTAG"] = str(ag)
        row["FTR"] = expected
        row["league_id"] = config["competition_id"]
        row["season"] = config["season_label_map"][source_season]
        row["source_code"] = config["source"]["source_code"]
        row["stage"] = "regular_league"
        rows.append(row)

    absent = sorted(allowed - observed_seasons)
    if absent:
        raise DataError(f"configured seasons absent from source: {absent}")
    if not rows:
        raise DataError("no completed Norway rows after season filtering")

    threshold = int(config["exclude_low_frequency_team_rows_below"])
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["season"]].append(row)
    kept: list[dict[str, str]] = []
    excluded: list[dict[str, str]] = []
    season_counts: dict[str, int] = {}
    for season, subset in sorted(grouped.items()):
        appearances: Counter[str] = Counter()
        for row in subset:
            appearances[row["HomeTeam"]] += 1
            appearances[row["AwayTeam"]] += 1
        regular_teams = {team for team, count in appearances.items() if count >= threshold}
        season_kept = [row for row in subset if row["HomeTeam"] in regular_teams and row["AwayTeam"] in regular_teams]
        season_excluded = [row for row in subset if row not in season_kept]
        kept.extend(season_kept)
        excluded.extend(season_excluded)
        season_counts[season] = len(season_kept)

    minimum = int(config["minimum_rows_complete_season"])
    for season in config["complete_seasons"]:
        if season_counts.get(season, 0) < minimum:
            raise DataError(f"{season}: only {season_counts.get(season, 0)} regular-league rows; expected at least {minimum}")

    seen: set[tuple[str, str, str, str]] = set()
    for row in kept:
        key = (row["season"], row.get("Date", ""), row["HomeTeam"], row["AwayTeam"])
        if key in seen:
            raise DataError(f"duplicate match key: {key}")
        seen.add(key)

    audit = {
        "raw_archive_rows": raw_count,
        "rows_after_recent_season_filter": len(rows),
        "rows_after_playoff_filter": len(kept),
        "excluded_playoff_rows": len(excluded),
        "excluded_matches": [
            {"season": row["season"], "date": row.get("Date"), "home": row["HomeTeam"], "away": row["AwayTeam"], "score": f"{row['FTHG']}-{row['FTAG']}"}
            for row in excluded
        ],
        "final_rows_by_season": season_counts,
        "filter_reason": config["excluded_rows_meaning"],
    }
    return kept, canonical_fields, audit


def union_fields(rows: list[dict[str, str]]) -> list[str]:
    fixed = ["league_id", "season", "stage", "source_code", "Date", "Time", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"]
    present = {key for row in rows for key in row}
    return [field for field in fixed if field in present] + sorted(present - set(fixed))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=union_fields(rows), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temp, path)


def pct(count: int, total: int) -> float:
    return round(count / total, 8) if total else 0.0


def build_profile(rows: list[dict[str, str]], source_sha: str, audit: dict[str, Any]) -> dict[str, Any]:
    n = len(rows)
    results = Counter(row["FTR"] for row in rows)
    totals = Counter()
    exact_totals = Counter()
    scores = Counter()
    diffs = Counter()
    per_season: dict[str, list[dict[str, str]]] = defaultdict(list)
    btts = 0
    home_goals = 0
    away_goals = 0
    for row in rows:
        hg, ag = int(row["FTHG"]), int(row["FTAG"])
        total = hg + ag
        totals[str(total) if total <= 6 else "7+"] += 1
        exact_totals[total] += 1
        scores[f"{hg}-{ag}"] += 1
        diffs[hg - ag] += 1
        btts += int(hg > 0 and ag > 0)
        home_goals += hg
        away_goals += ag
        per_season[row["season"]].append(row)
    season_summary = {}
    for season, subset in sorted(per_season.items()):
        goals = [int(row["FTHG"]) + int(row["FTAG"]) for row in subset]
        season_results = Counter(row["FTR"] for row in subset)
        season_summary[season] = {
            "matches": len(subset),
            "mean_total_goals": round(sum(goals) / len(goals), 6),
            "result_distribution": {key: pct(season_results[key], len(subset)) for key in ("H", "D", "A")},
            "five_plus_rate": pct(sum(value >= 5 for value in goals), len(subset)),
            "seven_plus_rate": pct(sum(value >= 7 for value in goals), len(subset)),
        }
    return {
        "schema_version": "1.0",
        "league_id": "NOR_Eliteserien",
        "generated_at_utc": utc_now(),
        "profile_status": "historical_descriptive_profile_only",
        "matches": n,
        "seasons": sorted(per_season),
        "source_files": [{"path": str(RAW_PATH.relative_to(ROOT)), "sha256": source_sha}],
        "result_distribution": {key: pct(results[key], n) for key in ("H", "D", "A")},
        "total_goals_0_7plus": {key: pct(totals[key], n) for key in ("0", "1", "2", "3", "4", "5", "6", "7+")},
        "mean_total_goals": round((home_goals + away_goals) / n, 6),
        "mean_home_goals": round(home_goals / n, 6),
        "mean_away_goals": round(away_goals / n, 6),
        "btts_yes_rate": pct(btts, n),
        "five_plus_rate": pct(sum(count for total, count in exact_totals.items() if total >= 5), n),
        "six_plus_rate": pct(sum(count for total, count in exact_totals.items() if total >= 6), n),
        "seven_plus_rate": pct(sum(count for total, count in exact_totals.items() if total >= 7), n),
        "goal_difference_counts": {str(key): value for key, value in sorted(diffs.items())},
        "top_scorelines": [{"score": score, "count": count, "probability": pct(count, n)} for score, count in scores.most_common(20)],
        "per_season": season_summary,
        "ingestion_filter_audit": audit,
        "historical_market_policy": "research_only_no_question_time_freeze_authority"
    }


def run(content: bytes | None = None) -> dict[str, Any]:
    config = load_config()
    content = content if content is not None else download(config["source"]["url"])
    source_sha = sha256_bytes(content)
    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=RAW_PATH.parent, delete=False) as handle:
        handle.write(content)
        temp_name = handle.name
    os.replace(temp_name, RAW_PATH)
    rows, fields, audit = canonicalize_and_filter(content, config)
    write_csv(PROCESSED_PATH, rows)
    profile = build_profile(rows, source_sha, audit)
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {
        "schema_version": "1.0",
        "run_at_utc": utc_now(),
        "competition_id": config["competition_id"],
        "source_url": config["source"]["url"],
        "source_sha256": source_sha,
        "source_fields": fields,
        "processed_rows": len(rows),
        "seasons": audit["final_rows_by_season"],
        "excluded_playoff_rows": audit["excluded_playoff_rows"],
        "raw_path": str(RAW_PATH.relative_to(ROOT)),
        "processed_path": str(PROCESSED_PATH.relative_to(ROOT)),
        "profile_path": str(PROFILE_PATH.relative_to(ROOT)),
        "question_time_market_policy": "current_prices_only",
        "status": "passed"
    }
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    try:
        manifest = run()
    except DataError as exc:
        print(f"ERROR: {exc}")
        return 2
    if args.print_summary:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
