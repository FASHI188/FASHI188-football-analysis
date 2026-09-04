#!/usr/bin/env python3
"""Acquire timestamped FPL bootstrap snapshots for strict pre-match availability research.

Research/data-acquisition only:
- no outcome labels are used for feature construction or scoring;
- no model training, tuning, calibration, prediction, or CURRENT/formal mutation;
- only EPL fixture identity + scheduled kickoff are consumed from local football-data CSVs;
- source snapshots are frozen from Randdalf/fplcache at one exact source HEAD;
- for each fixture we select only snapshots at or before fixed pre-registered cutoffs.

Outputs are written under --out and are intended for a GitHub Actions artifact, not the repo.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import gzip
import hashlib
import json
import lzma
import os
import re
import shutil
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

SOURCE_REPO = "Randdalf/fplcache"
SOURCE_BRANCH = "main"
FIXTURE_ROOT = Path("football-data/raw/ENG_PremierLeague")
SEASONS = ("2021-22", "2022-23", "2023-24", "2024-25", "2025-26")
CUTOFFS = (
    ("T_MINUS_24H", timedelta(hours=24)),
    ("T_MINUS_6H", timedelta(hours=6)),
    ("T_MINUS_90M", timedelta(minutes=90)),
)
SNAPSHOT_RE = re.compile(r"^(?P<month>\d{1,2})/(?P<day>\d{1,2})/(?P<hhmm>\d{4})\.json\.xz$")
UK_TZ = ZoneInfo("Europe/London")
USER_AGENT = "FASHI188-football-analysis/fplcache-pit-availability-v1"
MAX_ACCEPTABLE_STALENESS_MIN = 12 * 60


@dataclass(frozen=True)
class Snapshot:
    observed_at_utc: datetime
    path: str
    blob_sha: str
    size: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "observed_at_utc": self.observed_at_utc.isoformat().replace("+00:00", "Z"),
            "path": self.path,
            "blob_sha": self.blob_sha,
            "size": self.size,
        }


def _request(url: str, *, token: str | None = None, attempts: int = 6) -> bytes:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    last_error: Exception | None = None
    for attempt in range(attempts):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in {403, 429, 500, 502, 503, 504}:
                raise
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
        time.sleep(min(30.0, 1.5 * (2 ** attempt)))
    raise RuntimeError(f"request failed after {attempts} attempts: {url}: {last_error}")


def _api_json(path_or_url: str, token: str | None) -> Any:
    if path_or_url.startswith("https://"):
        url = path_or_url
    else:
        url = "https://api.github.com" + path_or_url
    return json.loads(_request(url, token=token).decode("utf-8"))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_fixture_datetime(date_text: str, time_text: str) -> datetime:
    local = datetime.strptime(f"{date_text.strip()} {time_text.strip()}", "%d/%m/%Y %H:%M")
    return local.replace(tzinfo=UK_TZ).astimezone(timezone.utc)


def _load_fixtures() -> list[dict[str, Any]]:
    fixtures: list[dict[str, Any]] = []
    for season in SEASONS:
        path = FIXTURE_ROOT / f"{season}.csv"
        if not path.exists():
            raise FileNotFoundError(path)
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"Date", "Time", "HomeTeam", "AwayTeam"}
            fields = set(reader.fieldnames or [])
            if not required.issubset(fields):
                raise RuntimeError(f"{path}: missing fixture identity fields: {sorted(required - fields)}")
            for row_number, row in enumerate(reader, start=2):
                date_text = str(row.get("Date") or "").strip()
                time_text = str(row.get("Time") or "").strip()
                home = str(row.get("HomeTeam") or "").strip()
                away = str(row.get("AwayTeam") or "").strip()
                if not (date_text and time_text and home and away):
                    raise RuntimeError(f"{path}:{row_number}: incomplete fixture identity/time")
                kickoff_utc = _parse_fixture_datetime(date_text, time_text)
                fixtures.append(
                    {
                        "season": season,
                        "date": date_text,
                        "time_local_uk": time_text,
                        "home_team": home,
                        "away_team": away,
                        "kickoff_utc": kickoff_utc,
                    }
                )
    fixtures.sort(key=lambda x: (x["kickoff_utc"], x["home_team"], x["away_team"]))
    return fixtures


def _source_head(token: str | None) -> str:
    payload = _api_json(f"/repos/{SOURCE_REPO}/branches/{SOURCE_BRANCH}", token)
    return str(payload["commit"]["sha"])


def _index_snapshots(token: str | None, source_head: str) -> list[Snapshot]:
    root = _api_json(f"/repos/{SOURCE_REPO}/contents/cache?ref={source_head}", token)
    year_items = {str(item["name"]): item for item in root if item.get("type") == "dir"}
    wanted_years = {str(year) for year in range(2021, 2027)}
    missing = sorted(wanted_years - set(year_items))
    if missing:
        raise RuntimeError(f"fplcache missing year directories: {missing}")

    snapshots: list[Snapshot] = []
    for year_text in sorted(wanted_years):
        tree_sha = str(year_items[year_text]["sha"])
        tree = _api_json(f"/repos/{SOURCE_REPO}/git/trees/{tree_sha}?recursive=1", token)
        if tree.get("truncated"):
            raise RuntimeError(f"recursive tree truncated for cache/{year_text}")
        year = int(year_text)
        for entry in tree.get("tree", []):
            if entry.get("type") != "blob":
                continue
            rel = str(entry.get("path") or "")
            match = SNAPSHOT_RE.match(rel)
            if not match:
                continue
            month = int(match.group("month"))
            day = int(match.group("day"))
            hhmm = match.group("hhmm")
            hour = int(hhmm[:2])
            minute = int(hhmm[2:])
            try:
                observed = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
            except ValueError:
                continue
            snapshots.append(
                Snapshot(
                    observed_at_utc=observed,
                    path=f"cache/{year_text}/{rel}",
                    blob_sha=str(entry["sha"]),
                    size=int(entry.get("size") or 0),
                )
            )
    snapshots.sort(key=lambda x: x.observed_at_utc)
    if not snapshots:
        raise RuntimeError("no fplcache snapshots indexed")
    return snapshots


def _map_cutoffs(fixtures: list[dict[str, Any]], snapshots: list[Snapshot]) -> tuple[list[dict[str, Any]], list[Snapshot]]:
    observed = [s.observed_at_utc for s in snapshots]
    rows: list[dict[str, Any]] = []
    selected_by_path: dict[str, Snapshot] = {}

    for fixture in fixtures:
        mapping: dict[str, Any] = {}
        for label, delta in CUTOFFS:
            cutoff = fixture["kickoff_utc"] - delta
            idx = bisect.bisect_right(observed, cutoff) - 1
            selected = snapshots[idx] if idx >= 0 else None
            if selected is None:
                mapping[label] = {
                    "cutoff_utc": cutoff.isoformat().replace("+00:00", "Z"),
                    "snapshot": None,
                    "staleness_minutes": None,
                    "acceptable_staleness": False,
                }
                continue
            staleness = (cutoff - selected.observed_at_utc).total_seconds() / 60.0
            mapping[label] = {
                "cutoff_utc": cutoff.isoformat().replace("+00:00", "Z"),
                "snapshot": selected.as_dict(),
                "staleness_minutes": round(staleness, 3),
                "acceptable_staleness": staleness <= MAX_ACCEPTABLE_STALENESS_MIN,
            }
            selected_by_path[selected.path] = selected

        rows.append(
            {
                "competition_id": "ENG_PremierLeague",
                "season": fixture["season"],
                "date": fixture["date"],
                "time_local_uk": fixture["time_local_uk"],
                "home_team": fixture["home_team"],
                "away_team": fixture["away_team"],
                "kickoff_utc": fixture["kickoff_utc"].isoformat().replace("+00:00", "Z"),
                "cutoffs": mapping,
            }
        )

    return rows, sorted(selected_by_path.values(), key=lambda x: x.observed_at_utc)


def _raw_url(source_head: str, path: str) -> str:
    quoted = urllib.parse.quote(path, safe="/")
    return f"https://raw.githubusercontent.com/{SOURCE_REPO}/{source_head}/{quoted}"


def _download_one(snapshot: Snapshot, source_head: str, out_root: Path) -> dict[str, Any]:
    target = out_root / "raw" / snapshot.path
    target.parent.mkdir(parents=True, exist_ok=True)
    url = _raw_url(source_head, snapshot.path)
    if target.exists() and target.stat().st_size == snapshot.size and snapshot.size > 0:
        data_sha = _sha256_file(target)
    else:
        data = _request(url, token=None)
        if snapshot.size and len(data) != snapshot.size:
            raise RuntimeError(f"size mismatch for {snapshot.path}: got {len(data)}, expected {snapshot.size}")
        target.write_bytes(data)
        data_sha = _sha256_bytes(data)
    return {
        **snapshot.as_dict(),
        "raw_url": url,
        "downloaded_path": str(target.relative_to(out_root)),
        "sha256": data_sha,
        "downloaded_bytes": target.stat().st_size,
    }


def _download_snapshots(snapshots: list[Snapshot], source_head: str, out_root: Path, workers: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_download_one, snapshot, source_head, out_root): snapshot for snapshot in snapshots}
        for future in as_completed(futures):
            snapshot = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                raise RuntimeError(f"failed downloading {snapshot.path}: {exc}") from exc
    results.sort(key=lambda x: x["observed_at_utc"])
    return results


def _extract_snapshot(raw_path: Path, meta: dict[str, Any]) -> dict[str, Any]:
    with lzma.open(raw_path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)

    teams = [
        {
            "id": team.get("id"),
            "code": team.get("code"),
            "name": team.get("name"),
            "short_name": team.get("short_name"),
        }
        for team in payload.get("teams", [])
    ]

    players = []
    for player in payload.get("elements", []):
        players.append(
            {
                "id": player.get("id"),
                "code": player.get("code"),
                "first_name": player.get("first_name"),
                "second_name": player.get("second_name"),
                "web_name": player.get("web_name"),
                "team": player.get("team"),
                "element_type": player.get("element_type"),
                "status": player.get("status"),
                "news": player.get("news"),
                "news_added": player.get("news_added"),
                "chance_of_playing_this_round": player.get("chance_of_playing_this_round"),
                "chance_of_playing_next_round": player.get("chance_of_playing_next_round"),
                "now_cost": player.get("now_cost"),
                "selected_by_percent": player.get("selected_by_percent"),
                "form": player.get("form"),
                "minutes": player.get("minutes"),
                "starts": player.get("starts"),
                "total_points": player.get("total_points"),
            }
        )

    events = [
        {
            "id": event.get("id"),
            "name": event.get("name"),
            "deadline_time": event.get("deadline_time"),
            "finished": event.get("finished"),
            "is_current": event.get("is_current"),
            "is_next": event.get("is_next"),
        }
        for event in payload.get("events", [])
    ]

    return {
        "source": {
            "path": meta["path"],
            "blob_sha": meta["blob_sha"],
            "observed_at_utc": meta["observed_at_utc"],
            "sha256": meta["sha256"],
            "raw_url": meta["raw_url"],
            "compressed_bytes": meta["downloaded_bytes"],
        },
        "team_count": len(teams),
        "player_count": len(players),
        "teams": teams,
        "events": events,
        "players": players,
    }


def _write_jsonl_gz(path: Path, rows: list[dict[str, Any]]) -> None:
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=6) as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            handle.write("\n")


def _save_source_text(source_head: str, out_root: Path, name: str) -> None:
    url = _raw_url(source_head, name)
    data = _request(url)
    target = out_root / f"source_{name.lower()}"
    target.write_bytes(data)


def _coverage_summary(mapping_rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_season: dict[str, Any] = {}
    all_staleness: list[float] = []
    missing_total = stale_total = mapped_total = 0
    for season in SEASONS:
        subset = [row for row in mapping_rows if row["season"] == season]
        season_report: dict[str, Any] = {"fixture_count": len(subset), "cutoffs": {}}
        for label, _ in CUTOFFS:
            values = [row["cutoffs"][label] for row in subset]
            mapped = [v for v in values if v["snapshot"] is not None]
            acceptable = [v for v in mapped if v["acceptable_staleness"]]
            stale = [v for v in mapped if not v["acceptable_staleness"]]
            missing = [v for v in values if v["snapshot"] is None]
            staleness = [float(v["staleness_minutes"]) for v in mapped]
            all_staleness.extend(staleness)
            mapped_total += len(mapped)
            stale_total += len(stale)
            missing_total += len(missing)
            season_report["cutoffs"][label] = {
                "mapped": len(mapped),
                "acceptable": len(acceptable),
                "stale": len(stale),
                "missing": len(missing),
                "median_staleness_minutes": round(statistics.median(staleness), 3) if staleness else None,
                "max_staleness_minutes": round(max(staleness), 3) if staleness else None,
            }
        by_season[season] = season_report
    return {
        "by_season": by_season,
        "mapped_cutoff_count": mapped_total,
        "stale_cutoff_count": stale_total,
        "missing_cutoff_count": missing_total,
        "global_median_staleness_minutes": round(statistics.median(all_staleness), 3) if all_staleness else None,
        "global_max_staleness_minutes": round(max(all_staleness), 3) if all_staleness else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    if args.workers < 1 or args.workers > 16:
        raise SystemExit("--workers must be between 1 and 16")

    out_root = args.out
    if out_root.exists():
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    token = os.environ.get("GITHUB_TOKEN") or None
    source_head = _source_head(token)
    fixtures = _load_fixtures()
    snapshots = _index_snapshots(token, source_head)
    mapping_rows, selected = _map_cutoffs(fixtures, snapshots)

    downloads = _download_snapshots(selected, source_head, out_root, args.workers)
    by_path = {item["path"]: item for item in downloads}

    extracted_path = out_root / "availability_snapshots.jsonl.gz"
    with gzip.open(extracted_path, "wt", encoding="utf-8", compresslevel=6) as handle:
        for snapshot in selected:
            meta = by_path[snapshot.path]
            raw_path = out_root / meta["downloaded_path"]
            record = _extract_snapshot(raw_path, meta)
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            handle.write("\n")

    _write_jsonl_gz(out_root / "fixture_cutoff_map.jsonl.gz", mapping_rows)
    _write_jsonl_gz(out_root / "selected_snapshot_index.jsonl.gz", downloads)
    _save_source_text(source_head, out_root, "README.md")
    _save_source_text(source_head, out_root, "LICENSE")

    total_downloaded = sum(int(item["downloaded_bytes"]) for item in downloads)
    coverage = _coverage_summary(mapping_rows)
    manifest = {
        "schema_version": "football3-fplcache-pit-availability-acquisition-v1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "classification": "RESEARCH_DATA_ACQUISITION_ONLY_NO_SCORING_NO_TRAINING",
        "source_repository": SOURCE_REPO,
        "source_branch": SOURCE_BRANCH,
        "source_head_sha": source_head,
        "source_snapshot_semantics": {
            "cache_schedule": "four times daily; source workflow cron 00:03/06:03/12:03/18:03 UTC with actual file time recorded in path",
            "path_time_assumed_timezone": "UTC",
            "strict_cutoff_rule": "selected snapshot observed_at_utc must be <= fixed cutoff",
            "cutoffs": {label: str(delta) for label, delta in CUTOFFS},
            "max_acceptable_staleness_minutes": MAX_ACCEPTABLE_STALENESS_MIN,
        },
        "fixture_scope": {
            "competition_id": "ENG_PremierLeague",
            "seasons": list(SEASONS),
            "fixture_count": len(fixtures),
            "consumed_fixture_fields_only": ["Date", "Time", "HomeTeam", "AwayTeam"],
            "outcome_scoring_performed": False,
        },
        "source_inventory": {
            "indexed_snapshot_count": len(snapshots),
            "indexed_first_snapshot_utc": snapshots[0].observed_at_utc.isoformat().replace("+00:00", "Z"),
            "indexed_last_snapshot_utc": snapshots[-1].observed_at_utc.isoformat().replace("+00:00", "Z"),
            "selected_unique_snapshot_count": len(selected),
            "downloaded_unique_snapshot_count": len(downloads),
            "downloaded_compressed_bytes": total_downloaded,
        },
        "coverage": coverage,
        "extracted_fields": {
            "team": ["id", "code", "name", "short_name"],
            "player": [
                "id", "code", "first_name", "second_name", "web_name", "team", "element_type",
                "status", "news", "news_added", "chance_of_playing_this_round",
                "chance_of_playing_next_round", "now_cost", "selected_by_percent", "form",
                "minutes", "starts", "total_points"
            ],
            "event": ["id", "name", "deadline_time", "finished", "is_current", "is_next"],
        },
        "governance": {
            "formal_weight_change": False,
            "runtime_probability_change": False,
            "current_change": False,
            "training_performed": False,
            "tuning_performed": False,
            "labels_scored": False,
            "target_match_actual_xi_used": False,
            "artifact_only": True,
        },
        "artifact_files": [
            "manifest.json",
            "fixture_cutoff_map.jsonl.gz",
            "selected_snapshot_index.jsonl.gz",
            "availability_snapshots.jsonl.gz",
            "source_readme.md",
            "source_license",
            "raw/cache/...",
        ],
    }
    (out_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(json.dumps({
        "status": "ACQUISITION_COMPLETE",
        "source_head_sha": source_head,
        "fixture_count": len(fixtures),
        "selected_unique_snapshot_count": len(selected),
        "downloaded_compressed_bytes": total_downloaded,
        "coverage": coverage,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
