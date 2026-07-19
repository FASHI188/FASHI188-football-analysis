#!/usr/bin/env python3
"""Final batch 002 runner with official K League 1 schedule ingestion.

The public K League schedule endpoint is queried month-by-month.  To respect
source restrictions, raw JSON bodies are not archived.  The repository stores
only normalized match results, request parameters, response SHA-256 hashes and
an audit manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

ADAPTER_PATH = Path(__file__).with_name("ingest_batch_002_accuracy_adapter.py")
SPEC = importlib.util.spec_from_file_location("ingest_batch_002_accuracy_core", ADAPTER_PATH)
ACC = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = ACC
SPEC.loader.exec_module(ACC)
BASE = ACC.CORE


def post_json(url: str, payload: dict[str, Any], timeout: int = 60) -> tuple[dict[str, Any], bytes]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": BASE.USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content = response.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        raise BASE.DataError(f"K League request failed {url} payload={payload}: {exc}") from exc
    if not content:
        raise BASE.DataError(f"K League empty response payload={payload}")
    try:
        parsed = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        preview = content[:300].decode("utf-8", errors="replace")
        raise BASE.DataError(f"K League invalid JSON payload={payload}: {preview!r}") from exc
    return parsed, content


def _data_object(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data", payload)
    return data if isinstance(data, dict) else {}


def _club_directory(data: dict[str, Any]) -> dict[str, str]:
    directory: dict[str, str] = {}
    for club in data.get("clubList", []) or []:
        if not isinstance(club, dict):
            continue
        code = str(club.get("teamId") or club.get("code") or "").strip()
        name = str(
            club.get("teamNameShort")
            or club.get("teamName")
            or club.get("name")
            or club.get("teamNameFull")
            or club.get("fullName")
            or code
        ).strip()
        if code:
            directory[code] = name
    return directory


def _number(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def parse_kleague_payload(
    payload: dict[str, Any],
    season: str,
    competition: dict[str, Any],
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    data = _data_object(payload)
    clubs = _club_directory(data)
    schedule = data.get("scheduleList", []) or []
    if not isinstance(schedule, list):
        raise BASE.DataError(f"K League {season}: scheduleList is not a list")

    excluded_tokens = [str(token) for token in competition.get("exclude_meet_name_tokens", [])]
    rows: list[dict[str, str]] = []
    skipped_scheduled = 0
    skipped_other_competition = 0
    status_counts: Counter[str] = Counter()
    meet_names: Counter[str] = Counter()

    for item in schedule:
        if not isinstance(item, dict):
            continue
        status_code = str(item.get("gameStatus") or ("FE" if item.get("endYn") == "Y" else "NS"))
        status_counts[status_code] += 1
        meet_name = str(item.get("meetName") or "").strip()
        if meet_name:
            meet_names[meet_name] += 1
        if any(token and token in meet_name for token in excluded_tokens):
            skipped_other_competition += 1
            continue
        if status_code != "FE" and item.get("endYn") != "Y":
            skipped_scheduled += 1
            continue

        home_goals = _number(item.get("homeGoal"))
        away_goals = _number(item.get("awayGoal"))
        if home_goals is None or away_goals is None:
            skipped_scheduled += 1
            continue
        if home_goals < 0 or away_goals < 0:
            raise BASE.DataError(f"K League {season}: negative score in game {item.get('gameId')}")

        home_code = str(item.get("homeTeam") or "").strip()
        away_code = str(item.get("awayTeam") or "").strip()
        home_name = str(item.get("homeTeamName") or clubs.get(home_code) or home_code).strip()
        away_name = str(item.get("awayTeamName") or clubs.get(away_code) or away_code).strip()
        if not home_name or not away_name:
            raise BASE.DataError(f"K League {season}: missing team name game={item.get('gameId')}")

        round_id = _number(item.get("roundId"))
        stage = "regular_rounds_1_33" if round_id is not None and round_id <= 33 else "final_rounds_34_38"
        date = str(item.get("gameDate") or "").replace(".", "-").strip()
        if not date:
            raise BASE.DataError(f"K League {season}: missing gameDate game={item.get('gameId')}")

        rows.append(
            {
                "competition_id": competition["competition_id"],
                "season": season,
                "source_season": season,
                "stage": stage,
                "source_code": "kleague.com/getScheduleList.do",
                "Date": date,
                "Time": str(item.get("gameTime") or "").strip(),
                "HomeTeam": home_name,
                "AwayTeam": away_name,
                "FTHG": str(home_goals),
                "FTAG": str(away_goals),
                "FTR": BASE.normalize_result(home_goals, away_goals),
                "round": "" if round_id is None else str(round_id),
                "game_id": str(item.get("gameId") or "").strip(),
                "meet_name": meet_name,
                "venue": str(item.get("fieldNameFull") or item.get("fieldName") or "").strip(),
            }
        )

    audit = {
        "payload_schedule_rows": len(schedule),
        "finished_rows_selected": len(rows),
        "scheduled_or_scoreless_rows_skipped": skipped_scheduled,
        "excluded_other_competition_rows": skipped_other_competition,
        "status_counts": dict(status_counts),
        "meet_names": dict(meet_names.most_common()),
    }
    return rows, audit


def process_kleague(
    competition: dict[str, Any],
    config: dict[str, Any],
    staging: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    all_rows: list[dict[str, str]] = []
    request_audit: list[dict[str, Any]] = []
    per_season_audit: dict[str, Any] = {}

    for season in competition["seasons"]:
        season_rows: list[dict[str, str]] = []
        season_seen: set[str] = set()
        monthly_audits = []
        for month in range(1, 13):
            request_payload = {
                "year": season,
                "month": f"{month:02d}",
                "leagueId": int(competition["league_id"]),
            }
            response, raw = post_json(competition["schedule_endpoint"], request_payload)
            rows, audit = parse_kleague_payload(response, season, competition)
            month_added = 0
            for row in rows:
                identity = row.get("game_id") or "|".join(
                    [row["Date"], row["HomeTeam"], row["AwayTeam"]]
                )
                if identity in season_seen:
                    continue
                season_seen.add(identity)
                season_rows.append(row)
                month_added += 1
            response_hash = hashlib.sha256(raw).hexdigest()
            request_entry = {
                "year": season,
                "month": f"{month:02d}",
                "league_id": int(competition["league_id"]),
                "response_sha256": response_hash,
                "response_bytes": len(raw),
                "unique_finished_rows_added": month_added,
            }
            request_audit.append(request_entry)
            monthly_audits.append({**request_entry, "payload_audit": audit})
            time.sleep(0.12)

        expected = int(competition["expected_complete_matches"])
        if season in competition["complete_seasons"] and len(season_rows) != expected:
            raise BASE.DataError(
                f"K League {season}: {len(season_rows)} completed rows; expected {expected}. "
                "Refusing to create an incomplete complete-season profile."
            )
        if season == competition["current_partial_season"] and not season_rows:
            raise BASE.DataError(f"K League {season}: current season returned no completed matches")
        all_rows.extend(season_rows)
        per_season_audit[season] = {
            "unique_finished_rows": len(season_rows),
            "monthly_requests": monthly_audits,
        }

    seen: set[tuple[str, str, str, str]] = set()
    for row in all_rows:
        key = (row["season"], row["Date"], row["HomeTeam"], row["AwayTeam"])
        if key in seen:
            raise BASE.DataError(f"K League duplicate normalized match key: {key}")
        seen.add(key)

    raw_manifest_rel = Path("raw") / competition["competition_id"] / "request_manifest.json"
    processed_rel = Path("processed") / competition["competition_id"] / "official_results.csv"
    profile_rel = Path("league_profiles") / competition["competition_id"] / "profile.json"
    request_manifest = {
        "source": competition["schedule_endpoint"],
        "raw_payload_archived": False,
        "raw_payload_policy": competition["raw_payload_policy"],
        "generated_at_utc": BASE.utc_now(),
        "requests": request_audit,
    }
    (staging / raw_manifest_rel).parent.mkdir(parents=True, exist_ok=True)
    (staging / raw_manifest_rel).write_text(
        json.dumps(request_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    BASE.write_csv(staging / processed_rel, all_rows)

    request_hashes = "\n".join(entry["response_sha256"] for entry in request_audit).encode("ascii")
    source_record = {
        "competition_id": competition["competition_id"],
        "source_type": competition["source_type"],
        "url": competition["schedule_endpoint"],
        "official_schedule_page": competition["official_schedule_page"],
        "downloaded_at_utc": BASE.utc_now(),
        "request_count": len(request_audit),
        "response_hash_manifest_sha256": hashlib.sha256(request_hashes).hexdigest(),
        "raw_payload_archived": False,
        "raw_request_manifest_path": str(raw_manifest_rel),
        "processed_path": str(processed_rel),
        "selected_rows": len(all_rows),
        "validated": True,
    }
    profile = BASE.CORE.build_profile(competition["competition_id"], all_rows, [source_record])
    profile["competition_id"] = profile.pop("league_id", competition["competition_id"])
    profile["profile_status"] = "historical_descriptive_profile_official_results"
    profile["competition_policy"] = competition["stage_policy"]
    profile["ingestion_audit"] = {
        "per_season": {
            season: {"unique_finished_rows": audit["unique_finished_rows"]}
            for season, audit in per_season_audit.items()
        },
        "request_count": len(request_audit),
        "complete_season_expected_matches": competition["expected_complete_matches"],
        "promotion_playoff_exclusion_tokens": competition.get("exclude_meet_name_tokens", []),
        "raw_payload_archived": False,
    }
    profile["stage_warning"] = (
        "Rounds 1-33 and final rounds 34-38 are explicit. Promotion/relegation playoff meet names are excluded."
    )
    profile["usage_gate"] = {
        "may_supply_competition_prior": True,
        "may_override_question_time_market": False,
        "may_supply_exact_score_without_joint_matrix": False,
        "requires_post_calculation_integrity_check": True,
    }
    BASE.validate_profile(profile, config["hard_checks"]["probability_conservation_tolerance"])
    (staging / profile_rel).parent.mkdir(parents=True, exist_ok=True)
    (staging / profile_rel).write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return source_record, profile


def run() -> dict[str, Any]:
    config = BASE.load_config()
    started = BASE.utc_now()
    source_records: list[dict[str, Any]] = []
    profile_summary: dict[str, Any] = {}

    with tempfile.TemporaryDirectory(prefix="football-batch-002-final-") as temp_dir:
        staging = Path(temp_dir)
        for competition in config["competitions"]:
            source_type = competition["source_type"]
            if source_type == "football_data_extra_archive":
                record, profile = BASE.process_extra(competition, config, staging)
                source_records.append(record)
            elif source_type == "openfootball_champions_league":
                records, profile = BASE.process_ucl(competition, config, staging)
                source_records.extend(records)
            elif source_type == "official_kleague_public_endpoint":
                record, profile = process_kleague(competition, config, staging)
                source_records.append(record)
            else:
                raise BASE.DataError(f"unsupported source_type: {source_type}")

            profile_summary[competition["competition_id"]] = {
                "matches": profile["matches"],
                "seasons": profile["seasons"],
                "profile_status": profile["profile_status"],
            }

        if len(profile_summary) != 6:
            raise BASE.DataError(f"generated {len(profile_summary)} profiles; expected 6")
        BASE.replace_generated_tree(staging)

    manifest = {
        "schema_version": "1.1",
        "batch_id": config["batch_id"],
        "started_at_utc": started,
        "completed_at_utc": BASE.utc_now(),
        "source_registry_sha256": hashlib.sha256(BASE.CONFIG_PATH.read_bytes()).hexdigest(),
        "summary": {
            "requested_competitions": len(config["competitions"]),
            "processed_competitions": len(profile_summary),
            "pending_competitions": 0,
            "source_files_or_feeds": len(source_records),
            "processed_matches": sum(item["matches"] for item in profile_summary.values()),
        },
        "profiles": profile_summary,
        "sources": source_records,
        "pending": [],
        "limitations": [
            "The upstream J1 archive has no 2026 transition-tournament rows; 2026 is unavailable and is not pooled with ordinary J1 seasons.",
            "J1 promotion/relegation playoff rows outside the regular top flight are excluded and itemized in the profile audit.",
            "Argentina and MLS stage labels are not fully identified from the archive source; stage-specific calibration is disabled.",
            "K League raw JSON bodies are not archived; normalized official results and response hashes are retained.",
            "UCL qualifiers are excluded; ambiguous extra-time or penalty rows are excluded from the 90-minute-safe profile.",
            "All profiles are descriptive priors and cannot override question-time market, lineup and injury evidence.",
        ],
    }
    manifest_path = BASE.ROOT / "manifests" / "latest_ingestion_batch_002.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    try:
        manifest = run()
    except (BASE.DataError, BASE.CORE.DataError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.print_summary:
        print(json.dumps(manifest["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
