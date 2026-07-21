#!/usr/bin/env python3
from __future__ import annotations

import importlib.metadata
import json
import os
import socket
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
FOOTBALL = ROOT / "football-data"
REGISTRY = FOOTBALL / "config" / "context_intelligence_source_registry_v514.json"
OUT = FOOTBALL / "manifests" / "context_intelligence_source_readiness_v514_status.json"

TARGET_LEAGUES = [
    "ENG-Premier League",
    "ESP-La Liga",
    "GER-Bundesliga",
    "ITA-Serie A",
    "FRA-Ligue 1",
]
SEASON = "2025-26"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe(label: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    started = time.time()
    try:
        result = fn()
        return {"status": "PASS", "elapsed_seconds": round(time.time() - started, 3), **result}
    except Exception as exc:
        return {
            "status": "FAIL",
            "elapsed_seconds": round(time.time() - started, 3),
            "error": f"{type(exc).__name__}: {exc}",
            "label": label,
        }


def _frame_summary(frame) -> dict[str, Any]:
    columns = []
    try:
        columns = [str(c) for c in frame.columns]
    except Exception:
        pass
    return {
        "row_count": int(len(frame)),
        "column_count": len(columns),
        "columns_preview": columns[:30],
        "nonempty": bool(len(frame) > 0),
    }


def _available_leagues(sd, class_name: str) -> dict[str, Any]:
    cls = getattr(sd, class_name)
    leagues = list(cls.available_leagues())
    return {
        "available_league_count": len(leagues),
        "target_leagues": {league: league in leagues for league in TARGET_LEAGUES},
        "all_target_leagues_available": all(league in leagues for league in TARGET_LEAGUES),
    }


def audit_soccerdata_metadata(sd) -> dict[str, Any]:
    version = importlib.metadata.version("soccerdata")
    classes = ["ClubElo", "FBref", "Sofascore", "SoFIFA", "Understat", "WhoScored"]
    return {
        "version": version,
        "required_classes": {name: hasattr(sd, name) for name in classes},
        "all_required_classes_present": all(hasattr(sd, name) for name in classes),
    }


def audit_clubelo(sd) -> dict[str, Any]:
    reader = sd.ClubElo(no_cache=True)
    frame = reader.read_by_date("2026-05-01")
    summary = _frame_summary(frame)
    columns = set(str(c).lower() for c in frame.columns)
    summary.update({
        "pit_date_tested": "2026-05-01",
        "elo_column_present": "elo" in columns,
        "validity_columns_present": "from" in columns and "to" in columns,
        "usable_for_historical_pit_research": bool(summary["row_count"] >= 50 and "elo" in columns),
    })
    return summary


def audit_fbref(sd) -> dict[str, Any]:
    availability = _available_leagues(sd, "FBref")
    reader = sd.FBref(leagues="ENG-Premier League", seasons=SEASON, no_cache=True)
    schedule = reader.read_schedule()
    schedule_summary = _frame_summary(schedule)
    availability.update({
        "season_tested": SEASON,
        "schedule": schedule_summary,
        "methods_present": {
            "read_lineup": hasattr(reader, "read_lineup"),
            "read_player_match_stats": hasattr(reader, "read_player_match_stats"),
            "read_team_match_stats": hasattr(reader, "read_team_match_stats"),
            "read_events": hasattr(reader, "read_events"),
        },
        "recent_season_schedule_smoke_pass": schedule_summary["row_count"] >= 300,
        "pit_class": "LAGGED_HISTORY_ONLY",
    })
    return availability


def audit_sofascore(sd) -> dict[str, Any]:
    availability = _available_leagues(sd, "Sofascore")
    reader = sd.Sofascore(leagues="ENG-Premier League", seasons=SEASON, no_cache=True)
    schedule = reader.read_schedule()
    summary = _frame_summary(schedule)
    availability.update({
        "season_tested": SEASON,
        "schedule": summary,
        "recent_season_schedule_smoke_pass": summary["row_count"] >= 300,
        "pit_class": "RETROSPECTIVE_REFERENCE_OR_QUERY_TIME_ONLY",
    })
    return availability


def audit_sofifa(sd) -> dict[str, Any]:
    availability = _available_leagues(sd, "SoFIFA")
    reader = sd.SoFIFA(leagues="ENG-Premier League", versions="latest", no_cache=True)
    ratings = reader.read_team_ratings()
    summary = _frame_summary(ratings)
    index_names = [str(name) for name in getattr(ratings.index, "names", []) if name]
    availability.update({
        "version_tested": "latest",
        "team_ratings": summary,
        "index_names": index_names,
        "historical_version_gate_required": True,
        "pit_class": "VERSION_DATE_GATED_RESEARCH",
    })
    return availability


def audit_understat_interface(sd) -> dict[str, Any]:
    availability = _available_leagues(sd, "Understat")
    reader = sd.Understat(leagues="ENG-Premier League", seasons=SEASON)
    availability.update({
        "season_tested": SEASON,
        "methods_present": {
            "read_schedule": hasattr(reader, "read_schedule"),
            "read_player_match_stats": hasattr(reader, "read_player_match_stats"),
            "read_shot_events": hasattr(reader, "read_shot_events"),
        },
        "network_smoke_skipped": True,
        "reason": "V5.1.1-V5.1.3 already has a dedicated recent Understat ingestion and closed 2025/26 holdout; do not duplicate or retune it here.",
        "pit_class": "LAGGED_HISTORY_ONLY",
    })
    return availability


def audit_whoscored_interface(sd) -> dict[str, Any]:
    availability = _available_leagues(sd, "WhoScored")
    # Intentionally avoid bypassing anti-bot controls. Construction/method availability
    # is audited; an actual current query-time snapshot will be tested separately under
    # the source's normal access path and fail closed on captcha/IP blocking.
    reader = sd.WhoScored(leagues="ENG-Premier League", seasons=SEASON, no_cache=True, headless=True)
    availability.update({
        "season_tested": SEASON,
        "methods_present": {
            "read_schedule": hasattr(reader, "read_schedule"),
            "read_missing_players": hasattr(reader, "read_missing_players"),
            "read_events": hasattr(reader, "read_events"),
        },
        "network_smoke_skipped": True,
        "reason": "Do not automate around captcha/IP blocking. Current query-time use must succeed through normal access or remain unavailable.",
        "pit_class": "QUERY_TIME_PROSPECTIVE_PREFERRED",
    })
    return availability


def audit_gdelt_doc() -> dict[str, Any]:
    query = '"Liverpool" (injury OR injured OR doubtful OR suspended)'
    params = {
        "query": query,
        "mode": "artlist",
        "maxrecords": "10",
        "timespan": "7d",
        "sort": "datedesc",
        "format": "json",
    }
    url = "https://api.gdeltproject.org/api/v2/doc/doc?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "football-analysis-research/1.0"})
    with urllib.request.urlopen(req, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    articles = payload.get("articles") or []
    timestamps = []
    for article in articles:
        value = article.get("seendate") or article.get("date") or article.get("datetime")
        if value:
            timestamps.append(str(value))
    return {
        "endpoint": "GDELT DOC 2.0 ArticleList JSON",
        "query": query,
        "article_count": len(articles),
        "publication_timestamp_count": len(timestamps),
        "timestamps_preview": timestamps[:5],
        "source_urls_present": sum(1 for a in articles if a.get("url")),
        "usable_for_news_discovery": bool(articles and timestamps),
        "pit_class": "PUBLICATION_TIME_GATED_NEWS_DISCOVERY",
    }


def audit_optional_packages() -> dict[str, Any]:
    results = {}
    for package in ("socceraction",):
        try:
            results[package] = {"installed": True, "version": importlib.metadata.version(package)}
        except importlib.metadata.PackageNotFoundError:
            results[package] = {"installed": False, "version": None}
    return results


def main() -> int:
    socket.setdefaulttimeout(30)
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    try:
        import soccerdata as sd
    except Exception as exc:
        payload = {
            "schema_version": "V5.1.4-context-intelligence-source-readiness-r1",
            "generated_at_utc": utc_now(),
            "status": "FAIL",
            "fatal_error": f"soccerdata import failed: {type(exc).__name__}: {exc}",
            "formal_weight_change": False,
            "probability_change": False,
        }
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    reports = {
        "soccerdata_metadata": _safe("soccerdata_metadata", lambda: audit_soccerdata_metadata(sd)),
        "clubelo": _safe("clubelo", lambda: audit_clubelo(sd)),
        "fbref": _safe("fbref", lambda: audit_fbref(sd)),
        "sofascore": _safe("sofascore", lambda: audit_sofascore(sd)),
        "sofifa": _safe("sofifa", lambda: audit_sofifa(sd)),
        "understat_interface": _safe("understat_interface", lambda: audit_understat_interface(sd)),
        "whoscored_interface": _safe("whoscored_interface", lambda: audit_whoscored_interface(sd)),
        "gdelt_doc": _safe("gdelt_doc", audit_gdelt_doc),
        "optional_packages": {"status": "PASS", **audit_optional_packages()},
    }

    passes = [name for name, report in reports.items() if report.get("status") == "PASS"]
    failures = [name for name, report in reports.items() if report.get("status") == "FAIL"]
    high_priority_ready = []
    if reports["clubelo"].get("usable_for_historical_pit_research"):
        high_priority_ready.append("clubelo")
    if reports["gdelt_doc"].get("usable_for_news_discovery"):
        high_priority_ready.append("gdelt_doc")
    if reports["fbref"].get("recent_season_schedule_smoke_pass"):
        high_priority_ready.append("fbref_lagged_history")
    if reports["sofifa"].get("team_ratings", {}).get("nonempty"):
        high_priority_ready.append("sofifa_version_gated")

    payload = {
        "schema_version": "V5.1.4-context-intelligence-source-readiness-r1",
        "generated_at_utc": utc_now(),
        "season_focus": "2025/26",
        "soccerdata_version_expected": "1.9.0",
        "registry_schema_version": registry.get("schema_version"),
        "reports": reports,
        "passed_checks": passes,
        "failed_checks": failures,
        "high_priority_ready": high_priority_ready,
        "status": "PASS" if "soccerdata_metadata" in passes and len(high_priority_ready) >= 2 else "PARTIAL",
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "governance": {
            "historical_pages_without_original_timestamp": "NOT_FORMAL_PIT",
            "query_time_current_snapshots": "MAY_BE_FROZEN_PROSPECTIVELY",
            "postmatch_stats": "ONLY_FOR_LATER_FIXTURES",
            "captcha_or_ip_blocking": "FAIL_CLOSED_NO_BYPASS",
            "2025_26_xg_holdout": "CLOSED_NO_RETUNING"
        }
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
