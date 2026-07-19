#!/usr/bin/env python3
"""Ingest and profile the first top-division football league batch.

Rules live outside GitHub. This module only manages data, profiles and audit metadata.
It uses only Python's standard library so GitHub Actions can run it without extra packages.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "league_sources_batch_001.json"
USER_AGENT = "HHH1-football-data/1.0 (+private research archive)"
REQUIRED_RESULT_FIELDS = ("Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR")
ODDS_FIELD_GROUPS = {
    "opening_1x2": ("B365H", "B365D", "B365A", "AvgH", "AvgD", "AvgA"),
    "closing_1x2": ("B365CH", "B365CD", "B365CA", "AvgCH", "AvgCD", "AvgCA"),
    "ou25": ("B365>2.5", "B365<2.5", "Avg>2.5", "Avg<2.5", "B365C>2.5", "B365C<2.5"),
    "asian_handicap": ("AHh", "B365AHH", "B365AHA", "AvgAHH", "AvgAHA", "B365CAHH", "B365CAHA"),
}


class DataError(RuntimeError):
    pass


@dataclass(frozen=True)
class DownloadSpec:
    league_id: str
    season: str
    source_code: str
    url: str
    required: bool
    source_type: str


@dataclass
class DownloadResult:
    spec: DownloadSpec
    content: bytes | None
    status: str
    error: str | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_specs(config: dict[str, Any]) -> list[DownloadSpec]:
    specs: list[DownloadSpec] = []
    season_codes = config["season_codes"]
    for league in config["leagues"]:
        if league["source_type"] == "main":
            for season in league["seasons"]:
                code = season_codes[season]
                url = config["main_league_url_template"].format(
                    season_code=code, source_code=league["source_code"]
                )
                specs.append(
                    DownloadSpec(
                        league_id=league["league_id"],
                        season=season,
                        source_code=league["source_code"],
                        url=url,
                        required=True,
                        source_type="main",
                    )
                )
        else:
            season = league["current_season"]
            url = config["extra_current_url_template"].format(source_code=league["source_code"])
            specs.append(
                DownloadSpec(
                    league_id=league["league_id"],
                    season=season,
                    source_code=league["source_code"],
                    url=url,
                    required=False,
                    source_type="extra_current_snapshot",
                )
            )
    return specs


def download(spec: DownloadSpec, timeout: int = 45) -> DownloadResult:
    request = urllib.request.Request(spec.url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content = response.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        return DownloadResult(spec, None, "failed", str(exc))
    if not content:
        return DownloadResult(spec, None, "failed", "empty response")
    prefix = content[:512].lower()
    if b"<html" in prefix or b"<!doctype html" in prefix:
        return DownloadResult(spec, None, "failed", "received HTML instead of CSV")
    return DownloadResult(spec, content, "downloaded")


def decode_csv(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise DataError("CSV encoding could not be decoded")


def normalize_int(value: str, field: str) -> int:
    try:
        number = int(float(value.strip()))
    except (ValueError, TypeError) as exc:
        raise DataError(f"invalid {field}: {value!r}") from exc
    if number < 0:
        raise DataError(f"negative {field}: {number}")
    return number


def normalize_result(hg: int, ag: int) -> str:
    return "H" if hg > ag else "D" if hg == ag else "A"


def parse_csv(content: bytes, spec: DownloadSpec) -> tuple[list[dict[str, str]], list[str]]:
    text = decode_csv(content)
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise DataError("missing CSV header")
    fields = [f.strip() if f else "" for f in reader.fieldnames]
    reader.fieldnames = fields
    missing = [f for f in REQUIRED_RESULT_FIELDS if f not in fields]
    if missing:
        raise DataError(f"missing required fields: {missing}")

    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in reader:
        row = {str(k).strip(): ("" if v is None else str(v).strip()) for k, v in raw.items() if k}
        if not row.get("HomeTeam") or not row.get("AwayTeam"):
            continue
        if not row.get("FTHG") or not row.get("FTAG"):
            continue
        hg = normalize_int(row["FTHG"], "FTHG")
        ag = normalize_int(row["FTAG"], "FTAG")
        expected = normalize_result(hg, ag)
        if row.get("FTR") and row["FTR"] != expected:
            raise DataError(
                f"result mismatch {spec.league_id} {spec.season}: "
                f"{row.get('HomeTeam')} {hg}-{ag} {row.get('AwayTeam')} FTR={row.get('FTR')}"
            )
        key = (row.get("Date", ""), row["HomeTeam"], row["AwayTeam"])
        if key in seen:
            raise DataError(f"duplicate match key: {key}")
        seen.add(key)
        row["FTHG"] = str(hg)
        row["FTAG"] = str(ag)
        row["FTR"] = expected
        row["league_id"] = spec.league_id
        row["season"] = spec.season
        row["source_code"] = spec.source_code
        row["stage"] = infer_stage(spec.league_id, row)
        rows.append(row)
    if not rows:
        raise DataError("no completed match rows")
    return rows, fields


def infer_stage(league_id: str, row: dict[str, str]) -> str:
    # Source CSVs do not reliably expose split/playoff stage labels.
    # Keep uncertainty explicit rather than fabricating a stage.
    if league_id == "SCO_Premiership":
        return "regular_or_split_unverified"
    if league_id == "SUI_SuperLeague":
        return "pre_or_post_split_unverified"
    return "regular_league"


def union_fieldnames(rows: Iterable[dict[str, str]]) -> list[str]:
    fixed = [
        "league_id", "season", "stage", "source_code", "Date", "Time",
        "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR", "HTHG", "HTAG", "HTR",
    ]
    present: set[str] = set()
    for row in rows:
        present.update(row.keys())
    ordered = [f for f in fixed if f in present]
    ordered.extend(sorted(present - set(ordered)))
    return ordered


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = union_fieldnames(rows)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def pct(count: int, total: int) -> float:
    return round(count / total, 8) if total else 0.0


def sorted_counter(counter: Counter[Any], *, numeric: bool = False) -> dict[str, int]:
    if numeric:
        items = sorted(counter.items(), key=lambda kv: int(kv[0]))
    else:
        items = sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))
    return {str(k): int(v) for k, v in items}


def coverage(rows: list[dict[str, str]], fields: tuple[str, ...]) -> dict[str, Any]:
    total = len(rows)
    per_field = {field: sum(bool(r.get(field, "")) for r in rows) for field in fields}
    any_count = sum(any(r.get(field, "") for field in fields) for r in rows)
    complete_triplet = 0
    if len(fields) >= 3:
        first_three = fields[:3]
        complete_triplet = sum(all(r.get(field, "") for field in first_three) for r in rows)
    return {
        "rows": total,
        "any_field_count": any_count,
        "any_field_rate": pct(any_count, total),
        "first_three_complete_count": complete_triplet,
        "first_three_complete_rate": pct(complete_triplet, total),
        "per_field_count": per_field,
    }


def build_profile(league_id: str, rows: list[dict[str, str]], source_files: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    results = Counter(r["FTR"] for r in rows)
    total_goals = Counter()
    exact_totals = Counter()
    scorelines = Counter()
    goal_diffs = Counter()
    btts_yes = 0
    home_goals = away_goals = 0
    season_rows: dict[str, list[dict[str, str]]] = defaultdict(list)

    for r in rows:
        hg, ag = int(r["FTHG"]), int(r["FTAG"])
        total = hg + ag
        total_bin = str(total) if total <= 6 else "7+"
        total_goals[total_bin] += 1
        exact_totals[str(total)] += 1
        scorelines[f"{hg}-{ag}"] += 1
        goal_diffs[str(hg - ag)] += 1
        btts_yes += int(hg > 0 and ag > 0)
        home_goals += hg
        away_goals += ag
        season_rows[r["season"]].append(r)

    top_scores = scorelines.most_common(20)
    top3_count = sum(count for _, count in top_scores[:3])
    tail5 = sum(count for goals, count in exact_totals.items() if int(goals) >= 5)
    tail6 = sum(count for goals, count in exact_totals.items() if int(goals) >= 6)
    tail7 = sum(count for goals, count in exact_totals.items() if int(goals) >= 7)

    per_season: dict[str, Any] = {}
    for season, subset in sorted(season_rows.items()):
        goals = [int(r["FTHG"]) + int(r["FTAG"]) for r in subset]
        season_results = Counter(r["FTR"] for r in subset)
        per_season[season] = {
            "matches": len(subset),
            "mean_total_goals": round(sum(goals) / len(goals), 6),
            "result_distribution": {k: pct(season_results[k], len(subset)) for k in ("H", "D", "A")},
            "five_plus_rate": pct(sum(g >= 5 for g in goals), len(subset)),
            "six_plus_rate": pct(sum(g >= 6 for g in goals), len(subset)),
            "seven_plus_rate": pct(sum(g >= 7 for g in goals), len(subset)),
        }

    return {
        "schema_version": "1.0",
        "league_id": league_id,
        "generated_at_utc": utc_now(),
        "profile_status": "historical_descriptive_profile_only",
        "matches": n,
        "seasons": sorted(season_rows),
        "source_files": source_files,
        "result_distribution": {k: pct(results[k], n) for k in ("H", "D", "A")},
        "total_goals_0_7plus": {k: pct(total_goals[k], n) for k in ("0", "1", "2", "3", "4", "5", "6", "7+")},
        "mean_total_goals": round((home_goals + away_goals) / n, 6),
        "mean_home_goals": round(home_goals / n, 6),
        "mean_away_goals": round(away_goals / n, 6),
        "btts_yes_rate": pct(btts_yes, n),
        "five_plus_rate": pct(tail5, n),
        "six_plus_rate": pct(tail6, n),
        "seven_plus_rate": pct(tail7, n),
        "top_scorelines": [
            {"score": score, "count": count, "rate": pct(count, n)} for score, count in top_scores
        ],
        "top3_score_coverage": pct(top3_count, n),
        "goal_difference_distribution": {
            key: pct(value, n)
            for key, value in sorted(goal_diffs.items(), key=lambda kv: int(kv[0]))
        },
        "market_field_coverage": {
            name: coverage(rows, fields) for name, fields in ODDS_FIELD_GROUPS.items()
        },
        "per_season": per_season,
        "stage_warning": (
            "Split-stage labels are not verified in the upstream CSV; stage-specific calibration is disabled."
            if league_id in {"SCO_Premiership", "SUI_SuperLeague"}
            else None
        ),
        "usage_gate": {
            "may_supply_league_prior": True,
            "may_override_question_time_market": False,
            "may_supply_exact_score_without_joint_matrix": False,
            "requires_post_calculation_integrity_check": True,
        },
    }


def atomic_replace_dir(staged: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    backup = destination.with_name(destination.name + ".backup")
    if backup.exists():
        shutil.rmtree(backup)
    if destination.exists():
        destination.rename(backup)
    try:
        staged.rename(destination)
    except Exception:
        if backup.exists() and not destination.exists():
            backup.rename(destination)
        raise
    else:
        if backup.exists():
            shutil.rmtree(backup)


def run(strict_extra: bool = False) -> dict[str, Any]:
    config = load_config()
    specs = build_specs(config)
    started = utc_now()
    results = [download(spec) for spec in specs]

    failed_required = [r for r in results if r.status != "downloaded" and r.spec.required]
    if failed_required:
        details = "; ".join(f"{r.spec.league_id} {r.spec.season}: {r.error}" for r in failed_required)
        raise DataError(f"required downloads failed; no partial commit: {details}")
    if strict_extra:
        failed_extra = [r for r in results if r.status != "downloaded" and not r.spec.required]
        if failed_extra:
            details = "; ".join(f"{r.spec.league_id}: {r.error}" for r in failed_extra)
            raise DataError(f"optional extra-league downloads failed in strict mode: {details}")

    with tempfile.TemporaryDirectory(prefix="hhh-football-") as temp_name:
        stage_root = Path(temp_name)
        raw_root = stage_root / "raw"
        processed_root = stage_root / "processed"
        profiles_root = stage_root / "league_profiles"
        manifest_entries: list[dict[str, Any]] = []
        league_rows: dict[str, list[dict[str, str]]] = defaultdict(list)
        league_sources: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for result in results:
            spec = result.spec
            entry: dict[str, Any] = {
                "league_id": spec.league_id,
                "season": spec.season,
                "source_type": spec.source_type,
                "source_code": spec.source_code,
                "url": spec.url,
                "required": spec.required,
                "download_status": result.status,
                "error": result.error,
            }
            if result.content is None:
                manifest_entries.append(entry)
                continue
            rows, original_fields = parse_csv(result.content, spec)
            raw_path = raw_root / spec.league_id / f"{spec.season.replace('/', '-')}.csv"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(result.content)
            processed_path = processed_root / spec.league_id / f"{spec.season.replace('/', '-')}.csv"
            write_csv(processed_path, rows)
            entry.update(
                {
                    "rows": len(rows),
                    "original_column_count": len(original_fields),
                    "raw_sha256": sha256_file(raw_path),
                    "processed_sha256": sha256_file(processed_path),
                    "raw_path": str(raw_path.relative_to(stage_root)),
                    "processed_path": str(processed_path.relative_to(stage_root)),
                    "validated": True,
                }
            )
            manifest_entries.append(entry)
            league_rows[spec.league_id].extend(rows)
            league_sources[spec.league_id].append(entry)

        for league_id, rows in league_rows.items():
            profile = build_profile(league_id, rows, league_sources[league_id])
            path = profiles_root / league_id / "profile.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")

        snapshot_manifest = {
            "schema_version": "1.0",
            "batch_id": config["batch_id"],
            "started_at_utc": started,
            "completed_at_utc": utc_now(),
            "source_registry_sha256": sha256_file(CONFIG_PATH),
            "entries": manifest_entries,
            "summary": {
                "downloaded_files": sum(e["download_status"] == "downloaded" for e in manifest_entries),
                "failed_optional_files": sum(
                    e["download_status"] != "downloaded" and not e["required"] for e in manifest_entries
                ),
                "processed_leagues": len(league_rows),
                "processed_matches": sum(len(rows) for rows in league_rows.values()),
            },
            "limitations": [
                "SWE and SWZ historical seasons require separately frozen archive snapshots.",
                "Split-stage labels for Scotland and Switzerland are not inferred without a trusted round/stage source.",
                "Historical profiles are descriptive priors and cannot override question-time market and lineup evidence.",
            ],
        }
        manifest_path = stage_root / "manifests" / "latest_ingestion.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(snapshot_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        for name in ("raw", "processed", "league_profiles"):
            atomic_replace_dir(stage_root / name, ROOT / name)
        target_manifest = ROOT / "manifests" / "latest_ingestion.json"
        target_manifest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(manifest_path, target_manifest)

    return snapshot_manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict-extra", action="store_true", help="Fail when SWE/SWZ current snapshots fail")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    try:
        manifest = run(strict_extra=args.strict_extra)
    except DataError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.print_summary:
        print(json.dumps(manifest["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
