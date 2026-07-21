#!/usr/bin/env python3
"""Ingest public Transfermarkt observed starting-XI labels for V5.0.2 pilots.

Research-data adapter only. It downloads the public CC0 curated dataset,
keeps only ``type=starting_lineup``, validates exact 11-player team lineups,
and writes one normalized JSONL row per team-match.

Because the upstream curated games table exposes a date but no exact kickoff or
lineup publication timestamp, this adapter uses conservative proxies:
- kickoff_utc: source date at 12:00 UTC;
- source_observed_at_utc: source date + 2 days at 00:00 UTC.

The proxy makes the labels usable only as lagged observed history in shadow
research. It is not evidence that a lineup was known before its own kickoff.
No formal probability or model weight is changed by this adapter.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import shutil
import tempfile
import urllib.request
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
FOOTBALL = ROOT / "football-data"
MAP_PATH = FOOTBALL / "config" / "transfermarkt_lineup_map_v502.json"
OUT_MANIFEST = FOOTBALL / "manifests" / "transfermarkt_lineup_backfill_v502_status.json"

USER_AGENT = "FASHI188-football-analysis/5.0.2 lineup-research"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=180) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output, length=1024 * 1024)


def iter_csv_gz(path: Path) -> Iterable[dict[str, str]]:
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def parse_source_date(raw: str) -> date:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("missing source date")
    return date.fromisoformat(text[:10])


def proxy_times(match_date: date) -> tuple[str, str]:
    kickoff = datetime.combine(match_date, time(hour=12), tzinfo=timezone.utc)
    observed = datetime.combine(match_date + timedelta(days=2), time.min, tzinfo=timezone.utc)
    return kickoff.isoformat(), observed.isoformat()


def season_label(source_season: int, calendar: str) -> str:
    if calendar == "calendar_year":
        return str(source_season)
    if calendar in {"cross_year", "transition"}:
        return f"{source_season}/{str(source_season + 1)[-2:]}"
    return str(source_season)


def normalize_tokens(value: str) -> set[str]:
    cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in str(value or ""))
    return {token for token in cleaned.split() if token}


def verify_mapping(
    competition_rows: dict[str, dict[str, str]],
    competition_id: str,
    mapping: dict[str, Any],
) -> dict[str, Any]:
    source_id = str(mapping["source_competition_id"])
    row = competition_rows.get(source_id)
    if row is None:
        return {
            "status": "SOURCE_COMPETITION_NOT_FOUND",
            "source_competition_id": source_id,
        }

    haystack = normalize_tokens(row.get("name", ""))
    country_tokens = normalize_tokens(row.get("country_name", ""))
    expected_name = {str(item).lower() for item in mapping.get("expected_name_tokens", [])}
    expected_country = {str(item).lower() for item in mapping.get("expected_country_tokens", [])}
    name_ok = expected_name.issubset(haystack)
    country_ok = expected_country.issubset(country_tokens)
    return {
        "status": "VERIFIED" if name_ok and country_ok else "MAPPING_TOKEN_MISMATCH",
        "competition_id": competition_id,
        "source_competition_id": source_id,
        "source_name": row.get("name"),
        "source_country": row.get("country_name"),
        "expected_name_tokens": sorted(expected_name),
        "expected_country_tokens": sorted(expected_country),
        "name_tokens_ok": name_ok,
        "country_tokens_ok": country_ok,
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(temp, path)


def ingest(*, min_source_season: int, enabled_only: bool, write: bool) -> dict[str, Any]:
    config = load_json(MAP_PATH)
    source = config["source_dataset"]
    base_url = str(source["base_url"]).rstrip("/")
    files = source["files"]
    acquired_at = utc_now()

    with tempfile.TemporaryDirectory(prefix="tm-lineups-v502-") as tmp_dir:
        tmp = Path(tmp_dir)
        downloaded: dict[str, Path] = {}
        source_meta: dict[str, Any] = {}
        for logical_name in ("competitions", "games", "game_lineups"):
            filename = str(files[logical_name])
            url = f"{base_url}/{filename}"
            destination = tmp / filename
            download(url, destination)
            downloaded[logical_name] = destination
            source_meta[logical_name] = {
                "url": url,
                "filename": filename,
                "size_bytes": destination.stat().st_size,
                "sha256": sha256_file(destination),
            }

        competition_rows = {
            str(row.get("competition_id") or "").strip(): row
            for row in iter_csv_gz(downloaded["competitions"])
            if str(row.get("competition_id") or "").strip()
        }

        selected_map: dict[str, dict[str, Any]] = {}
        mapping_checks: dict[str, Any] = {}
        for competition_id, mapping in config["competition_map"].items():
            if enabled_only and not bool(mapping.get("enabled_pilot")):
                continue
            check = verify_mapping(competition_rows, competition_id, mapping)
            mapping_checks[competition_id] = check
            if check["status"] != "VERIFIED":
                continue
            selected_map[competition_id] = mapping

        source_to_domain = {
            str(mapping["source_competition_id"]): competition_id
            for competition_id, mapping in selected_map.items()
        }

        games: dict[str, dict[str, Any]] = {}
        games_seen = 0
        for row in iter_csv_gz(downloaded["games"]):
            source_competition_id = str(row.get("competition_id") or "").strip()
            competition_id = source_to_domain.get(source_competition_id)
            if competition_id is None:
                continue
            games_seen += 1
            try:
                source_season = int(str(row.get("season") or "").strip())
                if source_season < min_source_season:
                    continue
                match_date = parse_source_date(row.get("date", ""))
                game_id = str(int(str(row.get("game_id") or "").strip()))
                home_club_id = str(int(str(row.get("home_club_id") or "").strip()))
                away_club_id = str(int(str(row.get("away_club_id") or "").strip()))
            except (TypeError, ValueError):
                continue
            mapping = selected_map[competition_id]
            games[game_id] = {
                "competition_id": competition_id,
                "source_competition_id": source_competition_id,
                "source_season": source_season,
                "season": season_label(source_season, str(mapping.get("calendar") or "")),
                "match_date": match_date,
                "home_club_id": home_club_id,
                "away_club_id": away_club_id,
                "home_club_name": str(row.get("home_club_name") or "").strip(),
                "away_club_name": str(row.get("away_club_name") or "").strip(),
                "source_url": str(row.get("url") or "").strip(),
            }

        grouped: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
        player_names: dict[tuple[str, str, str], str] = {}
        lineup_source_rows = 0
        duplicate_player_rows = 0
        for row in iter_csv_gz(downloaded["game_lineups"]):
            if str(row.get("type") or "").strip().lower() != "starting_lineup":
                continue
            game_id_raw = str(row.get("game_id") or "").strip()
            try:
                game_id = str(int(game_id_raw))
            except ValueError:
                continue
            if game_id not in games:
                continue
            try:
                club_id = str(int(str(row.get("club_id") or "").strip()))
                player_id = str(int(str(row.get("player_id") or "").strip()))
            except ValueError:
                continue
            lineup_source_rows += 1
            key = (game_id, club_id)
            canonical_player = f"transfermarkt:{player_id}"
            if canonical_player in grouped[key]:
                duplicate_player_rows += 1
            grouped[key][canonical_player] = str(row.get("player_name") or "").strip()
            player_names[(game_id, club_id, canonical_player)] = str(
                row.get("player_name") or ""
            ).strip()

        output_by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
        rejected: list[dict[str, Any]] = []
        for (game_id, club_id), players in grouped.items():
            game = games[game_id]
            if club_id == game["home_club_id"]:
                side = "home"
                team = game["home_club_name"]
            elif club_id == game["away_club_id"]:
                side = "away"
                team = game["away_club_name"]
            else:
                rejected.append({
                    "game_id": game_id,
                    "club_id": club_id,
                    "reason": "club_not_home_or_away_in_games_table",
                })
                continue
            starters = sorted(players)
            if len(starters) != 11:
                rejected.append({
                    "game_id": game_id,
                    "club_id": club_id,
                    "team": team,
                    "starter_count": len(starters),
                    "reason": "not_exactly_11_unique_starting_players",
                })
                continue
            kickoff_utc, source_observed_at_utc = proxy_times(game["match_date"])
            source_url = game["source_url"] or (
                f"https://www.transfermarkt.com/spielbericht/index/spielbericht/{game_id}"
            )
            row = {
                "competition_id": game["competition_id"],
                "source_competition_id": game["source_competition_id"],
                "season": game["season"],
                "source_season": game["source_season"],
                "fixture_id": f"transfermarkt:{game_id}",
                "game_id": game_id,
                "kickoff_utc": kickoff_utc,
                "team": team,
                "team_source_id": f"transfermarkt:{club_id}",
                "home_away": side,
                "starters": starters,
                "starter_names": {
                    player: player_names[(game_id, club_id, player)] for player in starters
                },
                "label_type": "actual_starting_xi",
                "player_id_namespace": "transfermarkt",
                "source_name": "dcaribou_transfermarkt_datasets",
                "source_url": source_url,
                "source_dataset_url": source_meta["game_lineups"]["url"],
                "source_observed_at_utc": source_observed_at_utc,
                "source_timestamp_basis": "match_date_plus_2_days_00_utc_conservative_proxy",
                "kickoff_timestamp_basis": "match_date_12_utc_proxy",
                "ingested_at_utc": acquired_at,
            }
            output_by_domain[game["competition_id"]].append(row)

        domain_reports: dict[str, Any] = {}
        for competition_id, mapping in selected_map.items():
            rows = output_by_domain.get(competition_id, [])
            rows.sort(key=lambda item: (item["kickoff_utc"], item["fixture_id"], item["team"]))
            data_path = FOOTBALL / "lineups" / competition_id / "historical_lineups.jsonl"
            if write:
                write_jsonl(data_path, rows)
            fixtures = {row["fixture_id"] for row in rows}
            teams = {row["team_source_id"] for row in rows}
            seasons = {row["season"] for row in rows}
            domain_reports[competition_id] = {
                "status": "OBSERVED_LINEUP_LABELS_WRITTEN" if rows else "NO_VALID_LINEUP_ROWS",
                "source_competition_id": mapping["source_competition_id"],
                "data_path": data_path.relative_to(ROOT).as_posix(),
                "team_match_row_count": len(rows),
                "unique_fixture_count": len(fixtures),
                "unique_team_count": len(teams),
                "season_count": len(seasons),
                "seasons": sorted(seasons),
                "data_sha256": sha256_file(data_path) if write and data_path.is_file() else None,
            }

        report = {
            "schema_version": "V5.0.2-transfermarkt-lineup-backfill-r1",
            "generated_at_utc": utc_now(),
            "status": (
                "PASS_PILOT_LABELS_WRITTEN"
                if selected_map and all(item["team_match_row_count"] > 0 for item in domain_reports.values())
                else "PARTIAL_OR_BLOCKED"
            ),
            "enabled_only": enabled_only,
            "minimum_source_season": min_source_season,
            "source_files": source_meta,
            "mapping_checks": mapping_checks,
            "selected_domains": sorted(selected_map),
            "games_seen_before_season_filter": games_seen,
            "eligible_game_count": len(games),
            "starting_lineup_source_row_count": lineup_source_rows,
            "duplicate_player_row_count": duplicate_player_rows,
            "rejected_team_lineup_count": len(rejected),
            "rejected_examples": rejected[:50],
            "domains": domain_reports,
            "timestamp_policy": config["timestamp_policy"],
            "raw_preservation": {
                "mode": "public_remote_snapshot_hash_manifest",
                "local_raw_files_committed": False,
                "reason": "The upstream full curated files are large; immutable URLs, byte sizes, SHA256 hashes and acquisition time are recorded for reproducibility.",
            },
            "formal_weight_change": False,
            "probability_change": False,
            "automatic_promotion": False,
            "policy": "Observed-label research backfill only. No availability inference and no formal probability influence.",
        }
        if write:
            OUT_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
            OUT_MANIFEST.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-source-season", type=int, default=2021)
    parser.add_argument("--all-mapped", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()

    try:
        report = ingest(
            min_source_season=args.min_source_season,
            enabled_only=not args.all_mapped,
            write=not args.check_only,
        )
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False))
        return 2

    if args.print_summary:
        print(json.dumps({
            "status": report["status"],
            "selected_domains": report["selected_domains"],
            "eligible_game_count": report["eligible_game_count"],
            "rejected_team_lineup_count": report["rejected_team_lineup_count"],
            "domains": report["domains"],
        }, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS_PILOT_LABELS_WRITTEN" else 2


if __name__ == "__main__":
    raise SystemExit(main())
